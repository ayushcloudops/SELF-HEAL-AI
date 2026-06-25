"""
healthcheck.py
==============

Pre-flight environment validation for the AI Self-Healing Terraform Pipeline.

Run this before the agent attempts any analysis. It verifies that every moving
part of the system is present and reachable, and prints detailed, actionable
failure messages for anything that is missing.

Checks performed:
    1. Terraform binary is installed and on PATH.
    2. Python version is supported (>= 3.11).
    3. Ollama server is reachable.
    4. The qwen2.5-coder:7b model is available locally.
    5. A GitHub token is present in the environment.

Exit code is 0 when all REQUIRED checks pass, 1 otherwise. The GitHub token
check is treated as a warning when running locally (no PR context) but as a
hard failure inside CI.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from typing import List

# Allow running both as ``python agent/healthcheck.py`` and as a module.
try:
    from agent.ollama_client import OllamaClient
except ImportError:  # pragma: no cover - fallback for direct execution
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from ollama_client import OllamaClient  # type: ignore

MIN_PYTHON = (3, 11)
REQUIRED_MODEL = os.environ.get("OLLAMA_MODEL", "qwen2.5-coder:7b")
OLLAMA_ENDPOINT = os.environ.get(
    "OLLAMA_ENDPOINT", "http://localhost:11434/api/generate"
)

GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
RESET = "\033[0m"


@dataclass
class CheckResult:
    name: str
    passed: bool
    required: bool
    detail: str


def _ok(msg: str) -> str:
    return f"{GREEN}PASS{RESET} {msg}"


def _fail(msg: str) -> str:
    return f"{RED}FAIL{RESET} {msg}"


def _warn(msg: str) -> str:
    return f"{YELLOW}WARN{RESET} {msg}"


# --------------------------------------------------------------------------- #
# Individual checks
# --------------------------------------------------------------------------- #
def check_terraform() -> CheckResult:
    path = shutil.which("terraform")
    if not path:
        return CheckResult(
            "terraform",
            False,
            True,
            "Terraform binary not found on PATH. Install it via "
            "scripts/setup-runner.sh or https://developer.hashicorp.com/terraform/install.",
        )
    try:
        out = subprocess.run(
            ["terraform", "version"],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
        version_line = out.stdout.strip().splitlines()[0] if out.stdout else "unknown"
        return CheckResult("terraform", True, True, f"Found at {path} ({version_line}).")
    except Exception as exc:  # noqa: BLE001
        return CheckResult(
            "terraform", False, True, f"Terraform present but failed to run: {exc}"
        )


def check_python() -> CheckResult:
    current = sys.version_info[:3]
    if current[:2] >= MIN_PYTHON:
        return CheckResult(
            "python",
            True,
            True,
            f"Python {'.'.join(map(str, current))} (>= {'.'.join(map(str, MIN_PYTHON))} required).",
        )
    return CheckResult(
        "python",
        False,
        True,
        f"Python {'.'.join(map(str, current))} detected but "
        f">= {'.'.join(map(str, MIN_PYTHON))} is required. Install Python 3.11+.",
    )


def check_ollama_reachable(client: OllamaClient) -> CheckResult:
    if client.is_reachable():
        return CheckResult(
            "ollama", True, True, f"Ollama server reachable at {client.base_url}."
        )
    return CheckResult(
        "ollama",
        False,
        True,
        f"Ollama server NOT reachable at {client.base_url}. Start it with "
        "`ollama serve` (or `systemctl status ollama`) and confirm port 11434 is open.",
    )


def check_model(client: OllamaClient) -> CheckResult:
    if not client.is_reachable():
        return CheckResult(
            "model",
            False,
            True,
            "Skipped model check because Ollama is not reachable.",
        )
    if client.model_available():
        return CheckResult(
            "model", True, True, f"Model '{REQUIRED_MODEL}' is available locally."
        )
    return CheckResult(
        "model",
        False,
        True,
        f"Model '{REQUIRED_MODEL}' is NOT available. Pull it with: "
        f"`ollama pull {REQUIRED_MODEL}`.",
    )


def check_github_token() -> CheckResult:
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    in_ci = os.environ.get("CI", "").lower() == "true" or bool(
        os.environ.get("GITHUB_ACTIONS")
    )
    if token:
        return CheckResult("github_token", True, in_ci, "GITHUB_TOKEN is present.")
    detail = (
        "GITHUB_TOKEN is not set. Required in CI to post PR comments. "
        "Provide it via the workflow's GITHUB_TOKEN secret."
    )
    # Required only inside CI. Locally it's a warning.
    return CheckResult("github_token", False, in_ci, detail)


# --------------------------------------------------------------------------- #
# Runner
# --------------------------------------------------------------------------- #
def run_all_checks() -> List[CheckResult]:
    client = OllamaClient(endpoint=OLLAMA_ENDPOINT, model=REQUIRED_MODEL)
    return [
        check_terraform(),
        check_python(),
        check_ollama_reachable(client),
        check_model(client),
        check_github_token(),
    ]


def main() -> int:
    print("=" * 70)
    print(" AI Self-Healing Terraform Pipeline — Environment Health Check")
    print("=" * 70)

    results = run_all_checks()
    hard_failure = False

    for r in results:
        if r.passed:
            print(_ok(f"[{r.name}] {r.detail}"))
        elif r.required:
            print(_fail(f"[{r.name}] {r.detail}"))
            hard_failure = True
        else:
            print(_warn(f"[{r.name}] {r.detail}"))

    print("=" * 70)
    if hard_failure:
        print(_fail("One or more REQUIRED checks failed. See messages above."))
        return 1
    print(_ok("All required checks passed."))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
