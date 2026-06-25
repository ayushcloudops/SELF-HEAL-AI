#!/usr/bin/env bash
# =============================================================================
# setup-runner.sh
# -----------------------------------------------------------------------------
# Provisions an Ubuntu 22.04 host as a self-hosted GitHub Actions runner for
# the AI Self-Healing Terraform Pipeline.
#
# It installs and validates every dependency the pipeline needs:
#   * a dedicated `github-runner` user
#   * Terraform
#   * Python 3.11
#   * Docker
#   * Ollama (+ systemd service)
#   * the qwen2.5-coder:7b model
#
# It does NOT register the runner with GitHub — that step requires a
# repository-specific token and is documented in the README. Run this first,
# then follow the "Registering Runner" instructions.
#
# Usage:
#   sudo bash scripts/setup-runner.sh
#
# Idempotent: safe to re-run. Requires root (sudo).
# =============================================================================

set -euo pipefail

# ----------------------------------------------------------------------------
# Configuration
# ----------------------------------------------------------------------------
RUNNER_USER="github-runner"
RUNNER_HOME="/home/${RUNNER_USER}"
TERRAFORM_VERSION="1.9.5"
PYTHON_VERSION="3.11"
OLLAMA_MODEL="qwen2.5-coder:7b"

# Colored logging helpers
log()  { echo -e "\033[1;34m[INFO]\033[0m  $*"; }
ok()   { echo -e "\033[1;32m[ OK ]\033[0m  $*"; }
warn() { echo -e "\033[1;33m[WARN]\033[0m  $*"; }
err()  { echo -e "\033[1;31m[FAIL]\033[0m  $*" >&2; }

require_root() {
  if [[ "${EUID}" -ne 0 ]]; then
    err "This script must be run as root. Use: sudo bash scripts/setup-runner.sh"
    exit 1
  fi
}

# ----------------------------------------------------------------------------
# 0. Base packages
# ----------------------------------------------------------------------------
install_base() {
  log "Updating apt and installing base packages..."
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -y
  apt-get install -y \
    ca-certificates curl wget gnupg lsb-release unzip git jq \
    software-properties-common apt-transport-https
  ok "Base packages installed."
}

# ----------------------------------------------------------------------------
# 1. Create the github-runner user
# ----------------------------------------------------------------------------
create_runner_user() {
  if id "${RUNNER_USER}" &>/dev/null; then
    ok "User '${RUNNER_USER}' already exists."
  else
    log "Creating user '${RUNNER_USER}'..."
    useradd -m -s /bin/bash "${RUNNER_USER}"
    ok "User '${RUNNER_USER}' created."
  fi
  # Allow the runner to use Docker (group added later if needed).
  usermod -aG sudo "${RUNNER_USER}" || true
}

# ----------------------------------------------------------------------------
# 2. Install Terraform
# ----------------------------------------------------------------------------
install_terraform() {
  if command -v terraform &>/dev/null && \
     terraform version | grep -q "${TERRAFORM_VERSION}"; then
    ok "Terraform ${TERRAFORM_VERSION} already installed."
    return
  fi
  log "Installing Terraform ${TERRAFORM_VERSION}..."
  local arch="amd64"
  case "$(uname -m)" in
    aarch64|arm64) arch="arm64" ;;
  esac
  local url="https://releases.hashicorp.com/terraform/${TERRAFORM_VERSION}/terraform_${TERRAFORM_VERSION}_linux_${arch}.zip"
  local tmp
  tmp="$(mktemp -d)"
  wget -q "${url}" -O "${tmp}/terraform.zip"
  unzip -o -q "${tmp}/terraform.zip" -d "${tmp}"
  install -m 0755 "${tmp}/terraform" /usr/local/bin/terraform
  rm -rf "${tmp}"
  ok "Terraform installed: $(terraform version | head -n1)"
}

# ----------------------------------------------------------------------------
# 3. Install Python 3.11
# ----------------------------------------------------------------------------
install_python() {
  if command -v python${PYTHON_VERSION} &>/dev/null; then
    ok "Python ${PYTHON_VERSION} already installed."
  else
    log "Installing Python ${PYTHON_VERSION}..."
    add-apt-repository -y ppa:deadsnakes/ppa
    apt-get update -y
    apt-get install -y \
      python${PYTHON_VERSION} \
      python${PYTHON_VERSION}-venv \
      python${PYTHON_VERSION}-distutils
    ok "Python ${PYTHON_VERSION} installed."
  fi

  # Ensure pip is available for 3.11.
  if ! python${PYTHON_VERSION} -m pip --version &>/dev/null; then
    log "Bootstrapping pip for Python ${PYTHON_VERSION}..."
    curl -sS https://bootstrap.pypa.io/get-pip.py | python${PYTHON_VERSION}
  fi
  ok "pip ready: $(python${PYTHON_VERSION} -m pip --version)"
}

# ----------------------------------------------------------------------------
# 4. Install Docker
# ----------------------------------------------------------------------------
install_docker() {
  if command -v docker &>/dev/null; then
    ok "Docker already installed: $(docker --version)"
  else
    log "Installing Docker..."
    install -m 0755 -d /etc/apt/keyrings
    curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
      | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
    chmod a+r /etc/apt/keyrings/docker.gpg
    echo \
      "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
https://download.docker.com/linux/ubuntu $(lsb_release -cs) stable" \
      > /etc/apt/sources.list.d/docker.list
    apt-get update -y
    apt-get install -y docker-ce docker-ce-cli containerd.io \
      docker-buildx-plugin docker-compose-plugin
    ok "Docker installed: $(docker --version)"
  fi
  systemctl enable --now docker || warn "Could not enable docker service."
  usermod -aG docker "${RUNNER_USER}" || true
  ok "Added '${RUNNER_USER}' to docker group (re-login required to take effect)."
}

# ----------------------------------------------------------------------------
# 5. Install Ollama + start service
# ----------------------------------------------------------------------------
install_ollama() {
  if command -v ollama &>/dev/null; then
    ok "Ollama already installed: $(ollama --version 2>/dev/null || echo present)"
  else
    log "Installing Ollama..."
    curl -fsSL https://ollama.com/install.sh | sh
    ok "Ollama installed."
  fi

  log "Enabling and starting the Ollama systemd service..."
  systemctl enable ollama 2>/dev/null || true
  systemctl restart ollama 2>/dev/null || warn "Could not restart ollama via systemd; starting manually."

  # Wait for the API to come up.
  log "Waiting for Ollama API on http://localhost:11434 ..."
  for i in $(seq 1 30); do
    if curl -sf http://localhost:11434/api/tags >/dev/null 2>&1; then
      ok "Ollama API is up."
      return
    fi
    sleep 2
  done
  warn "Ollama API did not respond within 60s. Check: systemctl status ollama"
}

# ----------------------------------------------------------------------------
# 6. Pull the model
# ----------------------------------------------------------------------------
pull_model() {
  log "Pulling model '${OLLAMA_MODEL}' (this can take several minutes)..."
  if ollama list 2>/dev/null | grep -q "${OLLAMA_MODEL%%:*}"; then
    ok "Model '${OLLAMA_MODEL}' already present."
  else
    ollama pull "${OLLAMA_MODEL}"
    ok "Model '${OLLAMA_MODEL}' pulled."
  fi
}

# ----------------------------------------------------------------------------
# 7. Validate the whole installation
# ----------------------------------------------------------------------------
validate_install() {
  log "Validating installation..."
  local failed=0

  command -v terraform &>/dev/null && ok "terraform: $(terraform version | head -n1)" || { err "terraform missing"; failed=1; }
  command -v python${PYTHON_VERSION} &>/dev/null && ok "python: $(python${PYTHON_VERSION} --version)" || { err "python ${PYTHON_VERSION} missing"; failed=1; }
  command -v docker &>/dev/null && ok "docker: $(docker --version)" || { err "docker missing"; failed=1; }
  command -v ollama &>/dev/null && ok "ollama: present" || { err "ollama missing"; failed=1; }

  if curl -sf http://localhost:11434/api/tags >/dev/null 2>&1; then
    ok "Ollama API reachable."
  else
    err "Ollama API NOT reachable."
    failed=1
  fi

  if ollama list 2>/dev/null | grep -q "${OLLAMA_MODEL%%:*}"; then
    ok "Model '${OLLAMA_MODEL}' available."
  else
    err "Model '${OLLAMA_MODEL}' NOT available."
    failed=1
  fi

  if [[ "${failed}" -eq 0 ]]; then
    ok "All components validated successfully."
  else
    err "Validation found problems. Review the messages above."
    return 1
  fi
}

# ----------------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------------
main() {
  require_root
  log "Starting self-hosted runner provisioning on $(lsb_release -ds 2>/dev/null || echo Ubuntu)"
  install_base
  create_runner_user
  install_terraform
  install_python
  install_docker
  install_ollama
  pull_model
  validate_install

  cat <<EOF

============================================================================
 Provisioning complete.
============================================================================
 Next steps (run as the '${RUNNER_USER}' user):

   1. Go to your GitHub repository:
        Settings -> Actions -> Runners -> New self-hosted runner (Linux x64)

   2. Follow the download/configure commands GitHub shows you, e.g.:
        su - ${RUNNER_USER}
        mkdir -p ~/actions-runner && cd ~/actions-runner
        curl -o actions-runner.tar.gz -L <URL_FROM_GITHUB>
        tar xzf actions-runner.tar.gz
        ./config.sh --url https://github.com/<owner>/<repo> \\
                    --token <REGISTRATION_TOKEN> \\
                    --labels self-hosted,linux,ollama,terraform \\
                    --name ollama-tf-runner

   3. Install and start as a service:
        sudo ./svc.sh install ${RUNNER_USER}
        sudo ./svc.sh start
        sudo ./svc.sh status

   4. Verify the runner shows as "Idle" in GitHub with the labels:
        self-hosted, linux, ollama, terraform
============================================================================
EOF
}

main "$@"
