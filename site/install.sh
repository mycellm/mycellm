#!/bin/sh
# mycellm_ installer
# Usage: curl -fsSL https://mycellm.dev/install.sh | sh
set -e

BOLD='\033[1m'
GREEN='\033[0;32m'
RED='\033[0;31m'
DIM='\033[2m'
RESET='\033[0m'

echo ""
echo "${RED}  ████████████████████${RESET}"
echo "${RED} ██${RESET}████${RED}████████${RESET}██${RED}████${RESET}"
echo "${RED} ████████${RESET}██████${RED}████${RESET}"
echo ""
echo "${RED}${BOLD}  mycellm_${RESET}${DIM}  Distributed LLM inference.${RESET}"
echo ""

# Check Python
if ! command -v python3 >/dev/null 2>&1; then
    echo "${RED}Error: Python 3.11+ is required.${RESET}"
    echo "Install Python: https://www.python.org/downloads/"
    exit 1
fi

PY_VERSION=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
PY_MAJOR=$(echo "$PY_VERSION" | cut -d. -f1)
PY_MINOR=$(echo "$PY_VERSION" | cut -d. -f2)

if [ "$PY_MAJOR" -lt 3 ] || ([ "$PY_MAJOR" -eq 3 ] && [ "$PY_MINOR" -lt 11 ]); then
    echo "${RED}Error: Python 3.11+ required (found $PY_VERSION)${RESET}"
    exit 1
fi

echo "${GREEN}Python $PY_VERSION${RESET}"
echo ""

# Install
echo "${BOLD}Installing mycellm...${RESET}"
pip3 install mycellm --quiet 2>/dev/null || pip3 install mycellm

# Verify
if ! command -v mycellm >/dev/null 2>&1; then
    echo ""
    echo "${RED}Warning: 'mycellm' not found on PATH.${RESET}"
    echo "${DIM}You may need to add ~/.local/bin to your PATH:${RESET}"
    echo "  export PATH=~/.local/bin:\$PATH"
    echo ""
fi

echo ""
echo "${GREEN}${BOLD}Installed!${RESET}"
echo ""

# Initialize
echo "${BOLD}Initializing...${RESET}"
mycellm init --no-serve 2>/dev/null || true

echo ""
echo "${GREEN}${BOLD}Ready.${RESET}"
echo ""
echo "  ${BOLD}Start the node:${RESET}"
echo "    mycellm serve"
echo ""
echo "  ${BOLD}Chat now:${RESET}"
echo "    mycellm chat"
echo ""
echo "  ${BOLD}Dashboard:${RESET}"
echo "    http://localhost:8420"
echo ""
echo "  ${BOLD}As a service (auto-start on boot):${RESET}"
echo "    mycellm serve --install-service"
echo ""
echo "${DIM}Docs: https://docs.mycellm.dev${RESET}"
