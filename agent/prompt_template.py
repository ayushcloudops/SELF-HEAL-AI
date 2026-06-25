"""
prompt_template.py
==================

Prompt engineering for the AI Self-Healing Terraform Pipeline.

The model is instructed to behave as a panel of senior infrastructure experts
and to produce a strict JSON document describing the root cause of a Terraform
failure (or confirming success) together with a recommended fix.

The prompt is deliberately optimized for Terraform / AWS / Route53
troubleshooting. It feeds the model:
    * The outcome of each Terraform stage (fmt / init / validate / plan).
    * The captured logs from each stage.
    * The list of changed files.
    * The git diff.
    * A curated knowledge base of common Terraform / AWS / Route53 errors.
"""

from __future__ import annotations

import json
from typing import Dict, List, Optional

# The persona / system prompt establishes expertise and tone.
SYSTEM_PROMPT = (
    "You are a panel of world-class infrastructure experts operating as a "
    "single voice:\n"
    "  * a Senior Terraform Engineer,\n"
    "  * a Senior AWS Engineer,\n"
    "  * a Senior DevOps Engineer, and\n"
    "  * a Cloud Infrastructure Architect.\n\n"
    "You specialize in diagnosing Terraform pipeline failures, especially in "
    "AWS Route53, VPC, IAM and Security Group configurations. You are precise, "
    "evidence-driven, and never hallucinate resources or arguments that do not "
    "exist in the Terraform AWS provider. When you are unsure, you say so and "
    "lower your confidence. You always respond with a single valid JSON object "
    "and nothing else."
)

# The exact JSON schema the model must emit.
OUTPUT_SCHEMA = {
    "root_cause": "string - the precise root cause of the failure, or 'No failure detected' if everything passed",
    "confidence": "string - one of: High, Medium, Low",
    "severity": "string - one of: Critical, High, Medium, Low, Info",
    "affected_files": ["string - repository-relative paths most relevant to the issue"],
    "recommended_fix": "string - a concrete, actionable fix. Use markdown and code blocks. Do NOT include destructive commands.",
    "summary": "string - a concise one-paragraph executive summary",
}


def _truncate(text: str, max_chars: int) -> str:
    """Truncate long log blocks to keep the prompt within the context window."""
    if text is None:
        return ""
    text = text.strip()
    if len(text) <= max_chars:
        return text
    head = text[: max_chars // 2]
    tail = text[-max_chars // 2 :]
    return f"{head}\n\n...[TRUNCATED {len(text) - max_chars} chars]...\n\n{tail}"


def build_prompt(
    *,
    stage_outcomes: Dict[str, str],
    fmt_log: str,
    init_log: str,
    validate_log: str,
    plan_log: str,
    changed_files: List[str],
    git_diff: str,
    knowledge_base: Optional[str] = None,
    working_dir: str = "route53",
    event_name: str = "pull_request",
) -> str:
    """Construct the full user prompt sent to the model.

    The prompt is structured into clearly delimited sections so the 7B model
    can reliably attend to each piece of evidence.
    """
    schema_str = json.dumps(OUTPUT_SCHEMA, indent=2)

    changed_files_str = "\n".join(f"  - {f}" for f in changed_files) or "  (none detected)"

    kb_section = ""
    if knowledge_base:
        kb_section = (
            "## REFERENCE KNOWLEDGE BASE (common errors and fixes)\n"
            "Use this curated reference to ground your analysis. Match the "
            "observed logs to the closest known pattern when applicable.\n\n"
            f"{_truncate(knowledge_base, 6000)}\n"
        )

    prompt = f"""You are analyzing a Terraform CI/CD run for the AWS folder `{working_dir}/`.
The pipeline ran the following stages and continued even on failure so that a
full diagnosis is possible. Your job is to determine the ROOT CAUSE and a
RECOMMENDED FIX.

# CONTEXT
- Trigger event: {event_name}
- Terraform working directory: {working_dir}/
- Pipeline policy: ANALYSIS ONLY. Do NOT instruct anyone to auto-merge,
  auto-commit, or run destructive commands. Recommend fixes for a human to apply.

# STAGE OUTCOMES
- terraform fmt     : {stage_outcomes.get('fmt', 'unknown')}
- terraform init    : {stage_outcomes.get('init', 'unknown')}
- terraform validate: {stage_outcomes.get('validate', 'unknown')}
- terraform plan    : {stage_outcomes.get('plan', 'unknown')}

# CHANGED FILES
{changed_files_str}

# GIT DIFF
```diff
{_truncate(git_diff, 6000)}
```

# TERRAFORM FMT LOG
```
{_truncate(fmt_log, 2000)}
```

# TERRAFORM INIT LOG
```
{_truncate(init_log, 3000)}
```

# TERRAFORM VALIDATE LOG
```
{_truncate(validate_log, 4000)}
```

# TERRAFORM PLAN LOG
```
{_truncate(plan_log, 6000)}
```

{kb_section}

# WHAT TO ANALYZE
Carefully consider all of the following categories and identify which one(s)
apply:
  1. Terraform syntax errors (HCL parse failures, missing braces/quotes).
  2. Route53 configuration issues (invalid record types, missing zone_id,
     malformed `name`, conflicting records, alias misconfiguration, TTL with
     alias, etc.).
  3. Missing resources or modules (referenced but undefined).
  4. Missing or undeclared variables / outputs.
  5. Invalid references (resource attributes that do not exist).
  6. AWS API failures (throttling, region errors, invalid ARNs).
  7. IAM permission failures (AccessDenied, missing actions).
  8. Validation failures (provider schema violations).
  9. Plan failures (data source lookups, count/for_each evaluation errors).

# OUTPUT REQUIREMENTS
Respond with EXACTLY ONE valid JSON object matching this schema. Do not include
any prose, markdown fences, or commentary outside the JSON object.

JSON schema:
{schema_str}

Rules:
- If every stage succeeded, set "root_cause" to "No failure detected",
  "severity" to "Info", and explain in the summary that the change is healthy.
- "confidence" reflects how certain you are about the root cause.
- "affected_files" must be repository-relative paths drawn from the changed
  files and logs.
- "recommended_fix" must be specific and copy-pasteable where possible.
- Never invent Terraform arguments or AWS resource types that do not exist.
"""
    return prompt
