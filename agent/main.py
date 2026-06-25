"""
main.py
=======

Entry point for the AI analysis agent in the Self-Healing Terraform Pipeline.

Responsibilities (Phase 1 — analysis only):
    1. Read the captured Terraform logs (fmt / init / validate / plan).
    2. Read the list of changed files.
    3. Read the git diff.
    4. Load the curated knowledge base.
    5. Build a Terraform-troubleshooting prompt.
    6. Call the local Ollama model (qwen2.5-coder:7b).
    7. Parse the model response into a structured JSON report.
    8. Render the report as Markdown.
    9. Post the report as a PR comment (or commit comment as fallback) via the
       GitHub REST API, and always write the report to disk as an artifact.

This agent NEVER modifies Terraform code, creates commits, opens PRs, or merges
anything. It only analyzes and recommends.

It is intentionally fault-tolerant: any failure produces a degraded-but-useful
report rather than crashing the pipeline.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path
from typing import Any, Dict, List

# Support both module and script execution.
try:
    from agent.github_comment import GitHubCommenter, render_markdown
    from agent.ollama_client import OllamaClient, OllamaError
    from agent.prompt_template import SYSTEM_PROMPT, build_prompt
except ImportError:  # pragma: no cover - direct execution fallback
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from github_comment import GitHubCommenter, render_markdown  # type: ignore
    from ollama_client import OllamaClient, OllamaError  # type: ignore
    from prompt_template import SYSTEM_PROMPT, build_prompt  # type: ignore

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("agent.main")

# Required JSON keys in the report. Used to validate / repair model output.
REPORT_KEYS = [
    "root_cause",
    "confidence",
    "severity",
    "affected_files",
    "recommended_fix",
    "summary",
]


# --------------------------------------------------------------------------- #
# I/O helpers
# --------------------------------------------------------------------------- #
def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        logger.warning("Log file not found: %s", path)
        return ""
    except OSError as exc:
        logger.warning("Could not read %s: %s", path, exc)
        return ""


def read_lines(path: Path) -> List[str]:
    content = read_text(path)
    return [line.strip() for line in content.splitlines() if line.strip()]


def parse_status_file(path: Path) -> Dict[str, str]:
    """Parse a simple key=value status file into a dict."""
    result: Dict[str, str] = {}
    for line in read_lines(path):
        if "=" in line:
            key, _, value = line.partition("=")
            result[key.strip()] = value.strip()
    return result


def load_knowledge_base(repo_root: Path) -> str:
    """Concatenate the YAML knowledge-base files into a single reference block."""
    kb_dir = repo_root / "knowledge-base"
    parts: List[str] = []
    for name in ("terraform_errors.yaml", "aws_errors.yaml", "route53_errors.yaml"):
        kb_file = kb_dir / name
        if kb_file.exists():
            parts.append(f"### {name}\n{read_text(kb_file)}")
    return "\n\n".join(parts)


# --------------------------------------------------------------------------- #
# Report construction
# --------------------------------------------------------------------------- #
def normalize_report(data: Dict[str, Any]) -> Dict[str, Any]:
    """Ensure every required key exists with a sensible default and type."""
    report: Dict[str, Any] = {}
    report["root_cause"] = str(data.get("root_cause", "Unknown")).strip() or "Unknown"
    report["confidence"] = str(data.get("confidence", "Low")).strip() or "Low"
    report["severity"] = str(data.get("severity", "Medium")).strip() or "Medium"

    affected = data.get("affected_files", [])
    if isinstance(affected, str):
        affected = [affected]
    if not isinstance(affected, list):
        affected = []
    report["affected_files"] = [str(f) for f in affected if str(f).strip()]

    report["recommended_fix"] = (
        str(data.get("recommended_fix", "No specific fix recommended.")).strip()
        or "No specific fix recommended."
    )
    report["summary"] = str(data.get("summary", "")).strip() or "No summary produced."
    return report


def fallback_report(reason: str, terraform_failed: bool) -> Dict[str, Any]:
    """Produce a useful report when the model could not be consulted."""
    return {
        "root_cause": (
            "AI analysis unavailable — see details below."
            if terraform_failed
            else "No failure detected (AI analysis unavailable)."
        ),
        "confidence": "Low",
        "severity": "High" if terraform_failed else "Info",
        "affected_files": [],
        "recommended_fix": (
            "The AI model could not be consulted, so no automated fix is "
            "available. Please review the uploaded Terraform logs manually.\n\n"
            f"**Reason:** {reason}"
        ),
        "summary": (
            "The Terraform pipeline reported a failure, but the local LLM could "
            "not be reached to perform root cause analysis. "
            if terraform_failed
            else "Terraform stages passed. The local LLM could not be reached "
            "to confirm, so this is a heuristic result. "
        )
        + f"({reason})",
    }


# --------------------------------------------------------------------------- #
# Main flow
# --------------------------------------------------------------------------- #
def main() -> int:
    repo_root = Path(__file__).resolve().parent.parent
    log_dir = Path(os.environ.get("LOG_DIR", repo_root / ".ai-logs"))
    log_dir.mkdir(parents=True, exist_ok=True)

    working_dir = os.environ.get("TF_WORKING_DIR", "route53")
    event_name = os.environ.get("EVENT_NAME", "pull_request")
    model = os.environ.get("OLLAMA_MODEL", "qwen2.5-coder:7b")
    endpoint = os.environ.get(
        "OLLAMA_ENDPOINT", "http://localhost:11434/api/generate"
    )

    # ---- 1-4: Gather evidence -------------------------------------------- #
    stage_outcomes = parse_status_file(log_dir / "terraform-status.txt")
    terraform_failed = any(v == "failure" for v in stage_outcomes.values())

    fmt_log = read_text(log_dir / "terraform-fmt.log")
    init_log = read_text(log_dir / "terraform-init.log")
    validate_log = read_text(log_dir / "terraform-validate.log")
    plan_log = read_text(log_dir / "terraform-plan.log")
    changed_files = read_lines(log_dir / "changed-files.txt")
    git_diff = read_text(log_dir / "git-diff.txt")
    knowledge_base = load_knowledge_base(repo_root)

    logger.info(
        "Stage outcomes: %s | terraform_failed=%s | changed_files=%d",
        stage_outcomes,
        terraform_failed,
        len(changed_files),
    )

    # ---- 5: Build prompt -------------------------------------------------- #
    prompt = build_prompt(
        stage_outcomes=stage_outcomes,
        fmt_log=fmt_log,
        init_log=init_log,
        validate_log=validate_log,
        plan_log=plan_log,
        changed_files=changed_files,
        git_diff=git_diff,
        knowledge_base=knowledge_base,
        working_dir=working_dir,
        event_name=event_name,
    )
    (log_dir / "prompt.txt").write_text(prompt, encoding="utf-8")

    # ---- 6-7: Call model & parse ----------------------------------------- #
    client = OllamaClient(endpoint=endpoint, model=model)
    report: Dict[str, Any]

    if not client.is_reachable():
        logger.error("Ollama is not reachable; producing fallback report.")
        report = fallback_report("Ollama server unreachable.", terraform_failed)
    else:
        try:
            response = client.generate(prompt=prompt, system=SYSTEM_PROMPT, as_json=True)
            (log_dir / "model-raw-response.txt").write_text(
                response.raw_text, encoding="utf-8"
            )
            if response.parsed_json:
                report = normalize_report(response.parsed_json)
            else:
                logger.warning("Model output was not valid JSON; using fallback.")
                report = fallback_report(
                    "Model returned non-JSON output. Raw output stored in artifacts.",
                    terraform_failed,
                )
        except OllamaError as exc:
            logger.error("Model call failed: %s", exc)
            report = fallback_report(str(exc), terraform_failed)

    # ---- Persist structured report (artifact) ---------------------------- #
    report_path = log_dir / "ai-analysis-report.json"
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    logger.info("Wrote structured report to %s", report_path)

    markdown = render_markdown(report)
    (log_dir / "ai-analysis-report.md").write_text(markdown, encoding="utf-8")

    # ---- 8-9: Post to GitHub --------------------------------------------- #
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    repository = os.environ.get("GH_REPOSITORY", "").strip()
    pr_number_raw = os.environ.get("PR_NUMBER", "").strip()
    commit_sha = os.environ.get("COMMIT_SHA", "").strip()

    if not token or not repository:
        logger.warning(
            "GITHUB_TOKEN or GH_REPOSITORY not set — skipping comment posting. "
            "Report is available as an artifact."
        )
        _print_summary(report)
        return 0

    try:
        commenter = GitHubCommenter(token=token, repository=repository)
        if pr_number_raw and pr_number_raw.isdigit():
            commenter.post_or_update_pr_comment(int(pr_number_raw), markdown)
            logger.info("Posted analysis to PR #%s", pr_number_raw)
        elif commit_sha:
            commenter.post_commit_comment(commit_sha, markdown)
            logger.info("Posted analysis as a commit comment on %s", commit_sha)
        else:
            logger.warning("No PR number or commit SHA available; nothing posted.")
    except Exception as exc:  # noqa: BLE001 - never crash the pipeline on comment failure
        logger.error("Failed to post GitHub comment: %s", exc)

    _print_summary(report)
    return 0


def _print_summary(report: Dict[str, Any]) -> None:
    print("\n" + "=" * 70)
    print(" AI TERRAFORM ANALYSIS SUMMARY")
    print("=" * 70)
    print(f" Root cause : {report['root_cause']}")
    print(f" Confidence : {report['confidence']}")
    print(f" Severity   : {report['severity']}")
    print(f" Files      : {', '.join(report['affected_files']) or 'none'}")
    print("=" * 70 + "\n")


if __name__ == "__main__":
    raise SystemExit(main())
