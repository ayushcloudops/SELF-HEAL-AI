# =============================================================================
# Makefile — local development & testing for the AI Self-Healing Terraform
# Pipeline. Mirrors what the GitHub Actions workflow does, so you can validate
# everything before pushing.
# =============================================================================

# ---- Configuration ----------------------------------------------------------
PYTHON         ?= python3
PIP            ?= $(PYTHON) -m pip
TF_DIR         ?= route53
LOG_DIR        ?= .ai-logs
OLLAMA_MODEL   ?= qwen2.5-coder:7b
OLLAMA_ENDPOINT?= http://localhost:11434/api/generate

export LOG_DIR
export OLLAMA_MODEL
export OLLAMA_ENDPOINT
export TF_WORKING_DIR = $(TF_DIR)

.DEFAULT_GOAL := help
.PHONY: help install lint validate plan test-agent healthcheck logs clean

## help: Show this help message.
help:
	@echo "AI Self-Healing Terraform Pipeline — make targets:"
	@grep -E '^## ' $(MAKEFILE_LIST) | sed 's/## /  /'

## install: Install Python dependencies for the agent.
install:
	$(PIP) install --upgrade pip
	$(PIP) install -r requirements.txt
	@echo "Dependencies installed."

## lint: Run terraform fmt check on the route53 folder.
lint:
	@mkdir -p $(LOG_DIR)
	terraform -chdir=$(TF_DIR) fmt -check -recursive -diff \
		2>&1 | tee $(LOG_DIR)/terraform-fmt.log || true

## validate: terraform init (no backend) + validate.
validate:
	@mkdir -p $(LOG_DIR)
	terraform -chdir=$(TF_DIR) init -backend=false -no-color \
		2>&1 | tee $(LOG_DIR)/terraform-init.log || true
	terraform -chdir=$(TF_DIR) validate -no-color \
		2>&1 | tee $(LOG_DIR)/terraform-validate.log || true

## plan: terraform plan (requires AWS credentials in the environment).
plan:
	@mkdir -p $(LOG_DIR)
	terraform -chdir=$(TF_DIR) plan -no-color -input=false -lock=false \
		2>&1 | tee $(LOG_DIR)/terraform-plan.log || true

## healthcheck: Verify terraform, python, ollama, model and token presence.
healthcheck:
	$(PYTHON) agent/healthcheck.py

## test-agent: Run the AI agent locally against captured logs (no PR posting).
##             Generates $(LOG_DIR)/ai-analysis-report.json and .md.
test-agent:
	@mkdir -p $(LOG_DIR)
	@# Synthesize a status file if a real pipeline run did not create one.
	@if [ ! -f $(LOG_DIR)/terraform-status.txt ]; then \
		printf "fmt=%s\ninit=%s\nvalidate=%s\nplan=%s\n" \
			success success success success > $(LOG_DIR)/terraform-status.txt; \
	fi
	EVENT_NAME=local $(PYTHON) agent/main.py
	@echo "Report written to $(LOG_DIR)/ai-analysis-report.md"

## logs: Print the generated markdown analysis report.
logs:
	@cat $(LOG_DIR)/ai-analysis-report.md 2>/dev/null || echo "No report yet. Run 'make test-agent'."

## clean: Remove generated logs and Terraform working files.
clean:
	rm -rf $(LOG_DIR)
	rm -rf $(TF_DIR)/.terraform $(TF_DIR)/.terraform.lock.hcl
	@echo "Cleaned."
