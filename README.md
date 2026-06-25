# 🤖 AI Self-Healing Terraform Pipeline

An AI-powered CI/CD system that **automatically diagnoses Terraform failures**
using a **local LLM** (`qwen2.5-coder:7b` via [Ollama](https://ollama.com)) and
posts a **Root Cause Analysis + recommended fix** directly to your Pull Request.

Everything runs on a **self-hosted GitHub Actions runner** — your Terraform
logs and code never leave your infrastructure, and no external AI API is called.

> **Phase 1 (this repository): analysis only.** The agent never modifies
> Terraform code, never commits, never merges. It only analyzes and recommends.

---

## Table of Contents

1. [Project Overview](#project-overview)
2. [Architecture Diagram](#architecture-diagram)
3. [Workflow Diagram](#workflow-diagram)
4. [Repository Structure](#repository-structure)
5. [Setup Instructions](#setup-instructions)
6. [Self-Hosted Runner Setup](#self-hosted-runner-setup)
7. [Ollama Installation](#ollama-installation)
8. [Model Download](#model-download)
9. [GitHub Secrets](#github-secrets)
10. [Usage Guide](#usage-guide)
11. [Local Testing](#local-testing)
12. [Troubleshooting](#troubleshooting)
13. [Security Considerations](#security-considerations)
14. [Future Roadmap](#future-roadmap)

---

## Project Overview

When a **Pull Request** or **Push** changes anything inside the `route53/`
folder, the pipeline:

1. Checks out the repository.
2. Detects the changed files and computes a git diff.
3. Runs `terraform fmt -check`, `init`, `validate`, and `plan`.
4. **Continues even if Terraform fails** so a complete diagnosis is possible.
5. Captures every log.
6. Triggers a local AI agent (`qwen2.5-coder:7b` through Ollama).
7. Generates a structured **Root Cause Analysis** and **recommended fix**.
8. Posts the analysis as a **PR comment** (and uploads all logs as artifacts).

| Component | Technology |
|-----------|------------|
| CI/CD | GitHub Actions (self-hosted runner) |
| IaC | Terraform |
| AI runtime | Ollama (local) |
| Model | `qwen2.5-coder:7b` |
| Agent | Python 3.11 |
| Integration | GitHub REST API |

**Phase 1 monitors only the `route53/` folder.** Other folders
(`vpc/`, `iam/`, `security-groups/`) are intentionally out of scope and
documented in the [roadmap](#future-roadmap).

---

## Architecture Diagram

```
                         ┌──────────────────────────────────────────────┐
                         │              GitHub Repository               │
                         │   route53/  vpc/  iam/  security-groups/     │
                         └───────────────────┬──────────────────────────┘
                                             │  push / pull_request
                                             │  (paths: route53/**)
                                             ▼
                         ┌──────────────────────────────────────────────┐
                         │            GitHub Actions Trigger            │
                         │     .github/workflows/terraform-ai-heal.yml  │
                         └───────────────────┬──────────────────────────┘
                                             │ dispatch to labelled runner
                                             ▼
   ┌───────────────────────────────────────────────────────────────────────────┐
   │   SELF-HOSTED RUNNER (Ubuntu 22.04)                                        │
   │   labels: self-hosted, linux, ollama, terraform                           │
   │                                                                           │
   │   ┌───────────────┐   logs    ┌──────────────────┐   prompt   ┌────────┐  │
   │   │  Terraform    │ ────────► │   Python Agent   │ ─────────► │ Ollama │  │
   │   │ fmt/init/     │           │   agent/main.py  │            │  API   │  │
   │   │ validate/plan │ ◄──────── │                  │ ◄───────── │ :11434 │  │
   │   └───────────────┘  changed  └────────┬─────────┘   JSON     └───┬────┘  │
   │                       files            │                          │       │
   │                                        │                  qwen2.5-coder:7b │
   └────────────────────────────────────────┼──────────────────────────────────┘
                                             │ GitHub REST API
                                             ▼
                         ┌──────────────────────────────────────────────┐
                         │   PR Comment: "🤖 AI Terraform Analysis"     │
                         │   Root Cause · Confidence · Severity · Fix   │
                         └──────────────────────────────────────────────┘
```

---

## Workflow Diagram

```
 push/PR on route53/**
        │
        ▼
 ┌──────────────┐   ┌──────────────┐   ┌──────────────┐   ┌──────────────┐
 │ 1. Checkout  │──►│ 2. Detect    │──►│ 3. fmt -check│──►│ 4. init      │
 └──────────────┘   │   Changes    │   │ (continue-   │   │ (continue-   │
                    └──────────────┘   │  on-error)   │   │  on-error)   │
                                       └──────────────┘   └──────┬───────┘
                                                                 ▼
 ┌──────────────┐   ┌──────────────┐   ┌──────────────┐   ┌──────────────┐
 │ 8. Publish   │◄──│ 7. AI Agent  │◄──│ 6. Capture   │◄──│ 5. validate  │
 │   Analysis + │   │  (Ollama)    │   │   Logs +     │   │   + plan     │
 │   artifacts  │   │              │   │   status     │   │ (continue-   │
 └──────────────┘   └──────────────┘   └──────────────┘   │  on-error)   │
        │                                                  └──────────────┘
        ▼
 PR comment posted · logs uploaded · job status reflects Terraform result
```

---

## Repository Structure

```
.
├── route53/                       # Terraform monitored in Phase 1 (your files)
├── agent/
│   ├── main.py                    # Orchestrator: logs → prompt → model → PR
│   ├── ollama_client.py           # Ollama API client (retry/timeout/parse)
│   ├── github_comment.py          # GitHub REST API PR/commit comments
│   ├── prompt_template.py         # Terraform-tuned prompt engineering
│   └── healthcheck.py             # Pre-flight environment validation
├── knowledge-base/
│   ├── terraform_errors.yaml      # Common Terraform/HCL error patterns
│   ├── aws_errors.yaml            # Common AWS API/provider errors
│   └── route53_errors.yaml        # Route53-specific error patterns
├── scripts/
│   └── setup-runner.sh            # Provision the Ubuntu 22.04 runner
├── .github/
│   └── workflows/
│       └── terraform-ai-heal.yml  # The pipeline
├── Makefile                       # Local dev: install/lint/validate/plan/test
├── requirements.txt               # Python deps (requests, PyYAML)
└── README.md
```

---

## Setup Instructions

### Prerequisites

- An Ubuntu 22.04 host you control (VM, bare metal, or EC2) with **≥ 8 GB RAM**
  (the 7B model needs roughly 6–8 GB to run comfortably).
- Admin access to the GitHub repository (to register a self-hosted runner and
  add secrets).
- Your Terraform code under `route53/`.

### One-command provisioning

On the Ubuntu host:

```bash
git clone https://github.com/<owner>/<repo>.git
cd <repo>
sudo bash scripts/setup-runner.sh
```

This installs Terraform, Python 3.11, Docker, Ollama, starts the Ollama
service, and pulls `qwen2.5-coder:7b`. Then follow the runner registration
steps it prints (also documented below).

---

## Self-Hosted Runner Setup

### 1. Create a Self-Hosted Runner

In GitHub:

> **Repository → Settings → Actions → Runners → New self-hosted runner**

Select **Linux** / **x64**.

### 2. Register the Runner

GitHub shows download + configure commands. Run them **as the `github-runner`
user** created by the setup script, and add the required labels:

```bash
sudo su - github-runner
mkdir -p ~/actions-runner && cd ~/actions-runner

# Use the exact download URL GitHub shows you:
curl -o actions-runner.tar.gz -L https://github.com/actions/runner/releases/download/<version>/actions-runner-linux-x64-<version>.tar.gz
tar xzf actions-runner.tar.gz

./config.sh \
  --url https://github.com/<owner>/<repo> \
  --token <REGISTRATION_TOKEN_FROM_GITHUB> \
  --labels self-hosted,linux,ollama,terraform \
  --name ollama-tf-runner
```

### 3. Run the Runner as a Service

```bash
sudo ./svc.sh install github-runner
sudo ./svc.sh start
sudo ./svc.sh status
```

### 4. Verify the Runner

- In GitHub: **Settings → Actions → Runners** — the runner should show
  **Idle** (green).
- On the host: `sudo ./svc.sh status` should show `active (running)`.

### 5. Apply Runner Labels

The workflow targets a runner with **all** of these labels:

```
self-hosted
linux
ollama
terraform
```

If you registered without them, reconfigure with
`--labels self-hosted,linux,ollama,terraform` or add them in the runner's
settings page. The job in `terraform-ai-heal.yml` uses:

```yaml
runs-on: [self-hosted, linux, ollama, terraform]
```

---

## Ollama Installation

Installed automatically by `scripts/setup-runner.sh`. To do it manually:

```bash
curl -fsSL https://ollama.com/install.sh | sh
sudo systemctl enable --now ollama
curl -s http://localhost:11434/api/tags   # should return JSON
```

The agent talks to the endpoint:

```
http://localhost:11434/api/generate
```

---

## Model Download

```bash
ollama pull qwen2.5-coder:7b
ollama list          # confirm qwen2.5-coder:7b is listed
```

Quick smoke test:

```bash
curl http://localhost:11434/api/generate -d '{
  "model": "qwen2.5-coder:7b",
  "prompt": "Reply with the single word OK.",
  "stream": false
}'
```

---

## GitHub Secrets

> **Repository → Settings → Secrets and variables → Actions → New repository secret**

| Secret | Required | Purpose |
|--------|----------|---------|
| `GITHUB_TOKEN` | Provided automatically by Actions | Post PR comments (needs `pull-requests: write`, already set in the workflow `permissions`). |
| `AWS_ACCESS_KEY_ID` | For `terraform plan` against real AWS | AWS auth |
| `AWS_SECRET_ACCESS_KEY` | For `terraform plan` | AWS auth |
| `AWS_REGION` | For `terraform plan` | Target region |

> If you only want `fmt`/`init`/`validate` (no live `plan`), you can omit the
> AWS secrets — `plan` will fail but the pipeline continues and the AI agent
> will still analyze the validate-stage output. Prefer **OIDC federation** over
> static AWS keys where possible (see [Security](#security-considerations)).

---

## Usage Guide

1. Create a branch and modify something under `route53/`.
2. Open a Pull Request.
3. The **AI Self-Healing Terraform Pipeline** check runs on your self-hosted
   runner.
4. Within a minute or two, a comment titled **🤖 AI Terraform Analysis**
   appears on the PR with:

   - **Root Cause**
   - **Confidence** (High / Medium / Low)
   - **Severity** (Critical / High / Medium / Low / Info)
   - **Affected Files**
   - **Recommended Fix**
   - **Summary**

5. Full logs and the structured `ai-analysis-report.json` are available under
   the run's **Artifacts**.

The PR status check **passes** when all Terraform stages pass, and **fails**
when any stage fails — but the AI comment is posted either way.

---

## Local Testing

Use the `Makefile` to mirror the pipeline locally:

```bash
make install      # install Python deps
make healthcheck  # verify terraform / python / ollama / model / token
make lint         # terraform fmt -check
make validate     # terraform init -backend=false + validate
make plan         # terraform plan (needs AWS creds)
make test-agent   # run the AI agent against captured logs (no PR posting)
make logs         # print the generated markdown report
```

`make test-agent` writes `.ai-logs/ai-analysis-report.json` and `.md` so you can
review the model's output without opening a PR.

---

## Troubleshooting

| Symptom | Likely Cause | Fix |
|---------|-------------|-----|
| Job stuck "Waiting for a runner" | Labels don't match | Ensure the runner has `self-hosted,linux,ollama,terraform`. |
| `Ollama server NOT reachable` | Service down | `sudo systemctl restart ollama`; check port 11434. |
| `Model 'qwen2.5-coder:7b' NOT available` | Model not pulled | `ollama pull qwen2.5-coder:7b`. |
| Model call times out | 7B model slow on first load / low RAM | Increase `timeout` in `ollama_client.py`, ensure ≥ 8 GB RAM, warm the model. |
| No PR comment, but artifacts exist | Missing/insufficient `GITHUB_TOKEN` | Confirm workflow `permissions: pull-requests: write`. |
| Model output not JSON | Model drifted from schema | The agent auto-falls back; raw output is saved to `model-raw-response.txt`. Lower `temperature`. |
| `terraform plan` fails with AccessDenied | IAM perms / missing AWS secrets | Add AWS secrets or scope the IAM policy (see `knowledge-base/aws_errors.yaml`). |

Run `python agent/healthcheck.py` on the runner for a detailed, per-component
diagnosis.

---

## Security Considerations

- **Local-only inference.** Code and logs are analyzed by a model running on
  your own runner. Nothing is sent to a third-party AI API.
- **Read-only Phase 1.** The workflow grants only `contents: read` and
  `pull-requests/issues: write`. It cannot push code or merge.
- **Least-privilege AWS.** Prefer GitHub OIDC + a scoped IAM role over static
  `AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY`. Scope the role to Route53 read
  actions for `plan`.
- **No remote backend during validate.** `terraform init -backend=false` avoids
  touching real state during analysis.
- **Plan with `-lock=false`** is used for analysis to avoid contending with real
  applies; do not reuse this pattern for actual `apply`.
- **Secret hygiene.** The agent never logs secret values. Treat the
  self-hosted runner as sensitive infrastructure — isolate it, keep it patched,
  and restrict who can trigger workflows (beware `pull_request` from forks).
- **Untrusted PRs.** For public repos, consider `pull_request_target` caveats
  and require approval before running workflows from forks, since self-hosted
  runners can be targeted by malicious PRs.

---

## Future Roadmap

### Phase 2 — Assisted Self-Healing *(documented only, not implemented)*

- Auto-generated Terraform fixes from the AI analysis.
- Automatic branch creation for proposed fixes.
- Automatic commits of the suggested changes.
- Automatic Pull Requests containing the fix.
- Automatic pipeline re-run to validate the fix.
- **Human approval workflow** gating any change before merge.

### Phase 3 — Multi-Domain & Multi-Platform Coverage *(documented only)*

Extend analysis beyond `route53/` to:

- EKS failures
- IAM failures
- VPC failures
- Security Group failures
- Route53 failures (expanded)

And beyond GitHub Actions to other CI/CD systems:

- GitHub Actions failures
- GitLab CI failures
- Jenkins failures

---

<sub>AI Self-Healing Terraform Pipeline · Phase 1 (analysis only). Always review AI recommendations before applying.</sub>
