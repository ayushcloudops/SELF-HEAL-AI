"""
github_comment.py
=================

Posts the AI Terraform analysis back to the originating Pull Request as a
Markdown comment using the GitHub REST API.

Only the standard library + ``requests`` are used. The module is defensive:
if no PR number is available (e.g. a direct push to a branch with no PR) it
falls back to a commit comment, and it never raises in a way that would crash
the pipeline beyond an explicit, logged error.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import requests

logger = logging.getLogger("github_comment")

GITHUB_API = "https://api.github.com"

# A stable marker so we can find and update our previous comment instead of
# spamming the PR with a new comment on every run.
COMMENT_MARKER = "<!-- ai-terraform-analysis -->"


class GitHubCommentError(Exception):
    """Raised when a GitHub API interaction fails irrecoverably."""


def _headers(token: str) -> Dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "ai-self-healing-terraform-pipeline",
    }


def render_markdown(report: Dict[str, Any]) -> str:
    """Render the structured report into the required Markdown template."""
    affected: List[str] = report.get("affected_files") or []
    if affected:
        affected_md = "\n".join(f"- `{f}`" for f in affected)
    else:
        affected_md = "_None identified._"

    severity = report.get("severity", "Unknown")
    confidence = report.get("confidence", "Unknown")

    # A small badge-like emoji for quick visual scanning.
    severity_emoji = {
        "Critical": "🔴",
        "High": "🟠",
        "Medium": "🟡",
        "Low": "🟢",
        "Info": "🔵",
    }.get(severity, "⚪️")

    body = f"""{COMMENT_MARKER}
# 🤖 AI Terraform Analysis

> Generated locally by **qwen2.5-coder:7b** via Ollama on a self-hosted runner.
> This is an automated **analysis only** — no code was modified, committed, or merged.

## Root Cause
{report.get('root_cause', 'N/A')}

## Confidence
**{confidence}**

## Severity
{severity_emoji} **{severity}**

## Affected Files
{affected_md}

## Recommended Fix
{report.get('recommended_fix', 'N/A')}

## Summary
{report.get('summary', 'N/A')}

---
<sub>AI Self-Healing Terraform Pipeline · Phase 1 (analysis only) · review recommendations before applying.</sub>
"""
    return body


class GitHubCommenter:
    """Encapsulates posting/updating PR and commit comments."""

    def __init__(self, token: str, repository: str, timeout: int = 30) -> None:
        if not token:
            raise GitHubCommentError("A GitHub token is required to post comments.")
        if "/" not in repository:
            raise GitHubCommentError(
                f"Repository must be in 'owner/repo' format, got: {repository!r}"
            )
        self.token = token
        self.repository = repository
        self.timeout = timeout

    # ------------------------------------------------------------------ #
    def post_or_update_pr_comment(self, pr_number: int, body: str) -> Dict[str, Any]:
        """Create a new PR comment, or update our previous one if it exists."""
        existing = self._find_existing_comment(pr_number)
        if existing:
            logger.info("Updating existing AI analysis comment id=%s", existing)
            return self._update_comment(existing, body)
        logger.info("Creating new AI analysis comment on PR #%s", pr_number)
        return self._create_issue_comment(pr_number, body)

    def post_commit_comment(self, commit_sha: str, body: str) -> Dict[str, Any]:
        """Fallback: attach the analysis to a commit when no PR exists."""
        url = f"{GITHUB_API}/repos/{self.repository}/commits/{commit_sha}/comments"
        resp = requests.post(
            url, headers=_headers(self.token), json={"body": body}, timeout=self.timeout
        )
        return self._handle(resp)

    # ------------------------------------------------------------------ #
    # Internal helpers
    # ------------------------------------------------------------------ #
    def _find_existing_comment(self, pr_number: int) -> Optional[int]:
        url = f"{GITHUB_API}/repos/{self.repository}/issues/{pr_number}/comments"
        page = 1
        while True:
            resp = requests.get(
                url,
                headers=_headers(self.token),
                params={"per_page": 100, "page": page},
                timeout=self.timeout,
            )
            if resp.status_code != 200:
                logger.warning(
                    "Could not list PR comments (status %s); will create new.",
                    resp.status_code,
                )
                return None
            comments = resp.json()
            if not comments:
                return None
            for comment in comments:
                if COMMENT_MARKER in (comment.get("body") or ""):
                    return comment.get("id")
            if len(comments) < 100:
                return None
            page += 1

    def _create_issue_comment(self, pr_number: int, body: str) -> Dict[str, Any]:
        url = f"{GITHUB_API}/repos/{self.repository}/issues/{pr_number}/comments"
        resp = requests.post(
            url, headers=_headers(self.token), json={"body": body}, timeout=self.timeout
        )
        return self._handle(resp)

    def _update_comment(self, comment_id: int, body: str) -> Dict[str, Any]:
        url = f"{GITHUB_API}/repos/{self.repository}/issues/comments/{comment_id}"
        resp = requests.patch(
            url, headers=_headers(self.token), json={"body": body}, timeout=self.timeout
        )
        return self._handle(resp)

    def _handle(self, resp: requests.Response) -> Dict[str, Any]:
        if resp.status_code not in (200, 201):
            raise GitHubCommentError(
                f"GitHub API error {resp.status_code}: {resp.text[:500]}"
            )
        return resp.json()
