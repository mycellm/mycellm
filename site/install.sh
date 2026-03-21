#!/bin/sh
# mycellm installer
# Usage: curl -fsSL https://mycellm.dev/install.sh | sh
set -e

# Colors
R='\033[0;31m'    # Red (brand)
G='\033[0;32m'    # Green (spore)
Y='\033[0;33m'    # Yellow
B='\033[0;34m'    # Blue
P='\033[0;35m'    # Purple
C='\033[0;36m'    # Cyan
W='\033[1;37m'    # White bold
D='\033[0;90m'    # Dim
N='\033[0m'       # Reset

# ── Banner ──
echo ""
echo "    ${R}██████████████████████${N}"
echo "  ${R}██████████████████████████${N}"
echo "  ${R}████${W}██████${R}████${W}████${R}██████${N}"
echo "  ${R}████${W}██████${R}████${W}████${R}██████${N}"
echo "  ${R}██████████${W}██████${R}████████${N}"
echo "  ${R}██████████${W}██████${R}████████${N}"
echo "    ${Y}██████████████████████${N}"
echo "  ${Y}██████████████████████████${N}"
echo "  ${Y}████${D}████${Y}██████████${D}████${Y}████${N}"
echo "  ${Y}████${D}████${Y}██████████${D}████${Y}████${N}"
echo "  ${Y}██████████████████████████${N}"
echo "    ${Y}██████████████████████${N}"
echo ""
echo "  ${R}${W}mycellm${N}  ${D}Distributed LLM inference${N}"
echo "  ${D}Free AI, powered by the crowd.${N}"
echo ""

# ── System check ──
echo "  ${D}Checking system...${N}"

# Python
if ! command -v python3 >/dev/null 2>&1; then
    echo "  ${R}✗${N} Python 3.11+ not found"
    echo ""
    echo "  Install Python: ${C}https://www.python.org/downloads/${N}"
    exit 1
fi

PY_VERSION=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
PY_MAJOR=$(echo "$PY_VERSION" | cut -d. -f1)
PY_MINOR=$(echo "$PY_VERSION" | cut -d. -f2)

if [ "$PY_MAJOR" -lt 3 ] || ([ "$PY_MAJOR" -eq 3 ] && [ "$PY_MINOR" -lt 11 ]); then
    echo "  ${R}✗${N} Python $PY_VERSION found, but 3.11+ required"
    echo ""
    echo "  Upgrade: ${C}https://www.python.org/downloads/${N}"
    exit 1
fi
echo "  ${G}✓${N} Python $PY_VERSION"

# pip
if command -v pip3 >/dev/null 2>&1; then
    echo "  ${G}✓${N} pip available"
else
    echo "  ${R}✗${N} pip not found"
    echo "  Install: ${C}python3 -m ensurepip${N}"
    exit 1
fi

# GPU (informational, not required)
if command -v nvidia-smi >/dev/null 2>&1; then
    GPU=$(nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null | head -1)
    echo "  ${G}✓${N} GPU: $GPU ${D}(CUDA)${N}"
elif [ "$(uname)" = "Darwin" ] && [ "$(uname -m)" = "arm64" ]; then
    echo "  ${G}✓${N} GPU: Apple Silicon ${D}(Metal)${N}"
else
    echo "  ${D}○${N} No GPU detected ${D}(CPU inference available)${N}"
fi

echo ""

# ── Install ──
echo "  ${W}Installing mycellm...${N}"
echo ""

if pip3 install mycellm --quiet 2>/dev/null; then
    echo "  ${G}✓${N} mycellm installed"
else
    echo "  ${Y}Retrying with verbose output...${N}"
    pip3 install mycellm
fi

# Verify on PATH
if command -v mycellm >/dev/null 2>&1; then
    VERSION=$(mycellm --version 2>/dev/null | head -1)
    echo "  ${G}✓${N} $VERSION"
else
    echo ""
    echo "  ${Y}!${N} ${W}mycellm${N} not found on PATH"
    echo "  ${D}Add to your shell profile:${N}"
    echo "    ${G}export PATH=~/.local/bin:\$PATH${N}"
    echo ""
fi

echo ""

# ── Initialize ──
echo "  ${W}Initializing...${N}"
echo ""

# Node name
printf "  ${D}Node name ${N}${D}[${N}$(hostname)${D}]:${N} "
read NODE_NAME </dev/tty 2>/dev/null || NODE_NAME=""

# Telemetry
printf "  ${D}Share anonymous usage stats with the network? ${N}${D}[Y/n]:${N} "
read TELEMETRY </dev/tty 2>/dev/null || TELEMETRY="y"

# HuggingFace token (optional)
printf "  ${D}HuggingFace token ${N}${D}(optional, for gated models) [skip]:${N} "
read HF_TOKEN </dev/tty 2>/dev/null || HF_TOKEN=""

echo ""

# Write config
CONFIG_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/mycellm"
mkdir -p "$CONFIG_DIR"

{
    echo "MYCELLM_BOOTSTRAP_PEERS=bootstrap.mycellm.dev:8421"
    case "$TELEMETRY" in
        [Nn]*) echo "MYCELLM_TELEMETRY=false" ;;
        *)     echo "MYCELLM_TELEMETRY=true" ;;
    esac
    [ -n "$NODE_NAME" ] && echo "MYCELLM_NODE_NAME=$NODE_NAME"
    [ -n "$HF_TOKEN" ] && echo "MYCELLM_HF_TOKEN=$HF_TOKEN"
} > "$CONFIG_DIR/.env"

# Run init (creates keys + certs)
mycellm init --no-serve 2>/dev/null || true

echo "  ${G}✓${N} Identity created"
echo "  ${G}✓${N} Config written to $CONFIG_DIR/.env"
echo ""

# ── Done ──
echo "  ${G}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${N}"
echo ""
echo "  ${W}${G}Ready.${N}"
echo ""
echo "  ${W}Start the node${N}"
echo "    ${G}mycellm serve${N}"
echo ""
echo "  ${W}Chat now${N} ${D}(works immediately, even without a local model)${N}"
echo "    ${G}mycellm chat${N}"
echo ""
echo "  ${W}Open the dashboard${N}"
echo "    ${C}http://localhost:8420${N}"
echo ""
echo "  ${W}Auto-start on boot${N}"
echo "    ${G}mycellm serve --install-service${N}"
echo ""
echo "  ${D}Docs: ${C}https://mycellm.dev/quickstart/install/${N}"
echo "  ${D}Chat: ${C}https://mycellm.ai${N}"
echo ""
