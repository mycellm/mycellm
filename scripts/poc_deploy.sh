#!/usr/bin/env bash
# Deploy mycellm to a remote node for the 3-node PoC test.
# Usage: ./scripts/poc_deploy.sh <ssh-host> <remote-path> [--skip-llama]
#
# Examples:
#   ./scripts/poc_deploy.sh hokulea /Volumes/Scratch/dev/mycellm
#   ./scripts/poc_deploy.sh aurora ~/mycellm --skip-llama
#   ./scripts/poc_deploy.sh aurora ~/mycellm  # full install with llama-cpp-python

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

SSH_HOST="${1:?Usage: $0 <ssh-host> <remote-path> [--skip-llama]}"
REMOTE_PATH="${2:?Usage: $0 <ssh-host> <remote-path> [--skip-llama]}"
SKIP_LLAMA="${3:-}"

echo "=== Mycellm PoC Deploy ==="
echo "Host:   $SSH_HOST"
echo "Path:   $REMOTE_PATH"
echo ""

# Step 1: Rsync source (exclude heavy/local-only dirs)
echo "[1/4] Syncing source to $SSH_HOST:$REMOTE_PATH ..."
rsync -az --delete \
    --exclude '.venv/' \
    --exclude '.git/' \
    --exclude '__pycache__/' \
    --exclude '*.pyc' \
    --exclude '.pytest_cache/' \
    --exclude '.ruff_cache/' \
    --exclude 'node_modules/' \
    --exclude 'web/dist/' \
    --exclude '.env' \
    "$PROJECT_DIR/" "$SSH_HOST:$REMOTE_PATH/"
echo "  Done."

# Step 2: Create venv and install deps
echo "[2/4] Setting up Python venv and installing dependencies ..."
ssh "$SSH_HOST" bash -l <<REMOTE_SCRIPT
set -euo pipefail
cd "$REMOTE_PATH"

# Find python3.11+ (prefer python3.13, then 3.12, then 3.11, then python3)
PYTHON=""
for p in python3.13 python3.12 python3.11 python3; do
    if command -v "\$p" &>/dev/null; then
        PYTHON="\$p"
        break
    fi
done
if [ -z "\$PYTHON" ]; then
    echo "ERROR: No python3.11+ found on $SSH_HOST"
    exit 1
fi
echo "  Using \$PYTHON (\$(\$PYTHON --version))"

# Create venv if missing
if [ ! -d .venv ]; then
    echo "  Creating venv ..."
    \$PYTHON -m venv .venv
fi

source .venv/bin/activate

# Upgrade pip
pip install --quiet --upgrade pip

# Install project deps
if [ "$SKIP_LLAMA" = "--skip-llama" ]; then
    echo "  Installing deps (skipping llama-cpp-python) ..."
    # Install everything except llama-cpp-python
    pip install --quiet -e ".[dev]" 2>/dev/null || {
        # If llama-cpp-python fails to build, install deps manually without it
        pip install --quiet \
            "cryptography>=43.0" "aioquic>=1.0" "kademlia>=2.2" "cbor2>=5.6" \
            "fastapi>=0.110" "uvicorn>=0.30" "sse-starlette>=2.0" "aiosqlite>=0.20" \
            "typer>=0.12" "rich>=13.0" "pydantic>=2.0" "pydantic-settings>=2.0" \
            "pyyaml>=6.0" "httpx>=0.27"
        pip install --quiet --no-deps -e .
    }
else
    echo "  Installing all deps (including llama-cpp-python) ..."
    pip install --quiet -e ".[dev]"
fi

echo "  Verifying install ..."
mycellm --help >/dev/null 2>&1 && echo "  mycellm CLI: OK" || echo "  WARN: mycellm CLI not in PATH"
REMOTE_SCRIPT
echo "  Done."

# Step 3: Create identity if missing
echo "[3/4] Ensuring account + device identity ..."
ssh "$SSH_HOST" bash -l <<REMOTE_SCRIPT
set -euo pipefail
cd "$REMOTE_PATH"
source .venv/bin/activate

# Check if account exists
if [ ! -f "\$HOME/.local/share/mycellm/keys/account.key" ] && \
   [ ! -f "\${XDG_DATA_HOME:-\$HOME/.local/share}/mycellm/keys/account.key" ]; then
    echo "  Creating account ..."
    mycellm account create
else
    echo "  Account already exists."
fi

if [ ! -f "\$HOME/.local/share/mycellm/keys/device-default.key" ] && \
   [ ! -f "\${XDG_DATA_HOME:-\$HOME/.local/share}/mycellm/keys/device-default.key" ]; then
    echo "  Creating device ..."
    mycellm device create
else
    echo "  Device already exists."
fi
REMOTE_SCRIPT
echo "  Done."

# Step 4: Verify
echo "[4/4] Verifying deployment ..."
ssh "$SSH_HOST" bash -l <<REMOTE_SCRIPT
set -euo pipefail
cd "$REMOTE_PATH"
source .venv/bin/activate
echo "  Python: \$(python --version)"
echo "  mycellm: \$(mycellm --help 2>&1 | head -1)"
echo "  Platform: \$(uname -ms)"
REMOTE_SCRIPT

echo ""
echo "=== Deploy complete: $SSH_HOST ==="
echo ""
echo "Next steps:"
echo "  1. Download model:"
echo "     ssh $SSH_HOST \"curl -L -o ${REMOTE_PATH}/tinyllama.gguf https://huggingface.co/TheBloke/TinyLlama-1.1B-Chat-v1.0-GGUF/resolve/main/tinyllama-1.1b-chat-v1.0.Q4_K_M.gguf\""
echo "  2. Start node:"
echo "     ssh $SSH_HOST \"cd $REMOTE_PATH && source .venv/bin/activate && MYCELLM_BOOTSTRAP_PEERS=10.1.1.210:8421 mycellm serve --host 0.0.0.0 --no-dht\""
echo "  3. Load model:"
echo "     curl -X POST http://<node-ip>:8420/v1/node/models/load -H 'Content-Type: application/json' -d '{\"model_path\":\"${REMOTE_PATH}/tinyllama.gguf\",\"name\":\"tinyllama-1.1b\"}'"
