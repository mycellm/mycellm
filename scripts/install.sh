#!/usr/bin/env bash
set -euo pipefail

# mycellm install script
# Detects OS/GPU, installs mycellm, generates keypair, optionally installs systemd service

BOLD='\033[1m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

info()  { echo -e "${GREEN}[mycellm]${NC} $*"; }
warn()  { echo -e "${YELLOW}[mycellm]${NC} $*"; }
error() { echo -e "${RED}[mycellm]${NC} $*" >&2; }

# Detect OS
detect_os() {
    if [[ "$OSTYPE" == "linux-gnu"* ]]; then
        echo "linux"
    elif [[ "$OSTYPE" == "darwin"* ]]; then
        echo "macos"
    else
        echo "unknown"
    fi
}

# Detect GPU
detect_gpu() {
    if command -v nvidia-smi &>/dev/null; then
        echo "cuda"
    elif [[ "$(detect_os)" == "macos" ]]; then
        echo "metal"
    else
        echo "cpu"
    fi
}

# Check Python version
check_python() {
    if command -v python3 &>/dev/null; then
        local ver
        ver=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
        if python3 -c "import sys; exit(0 if sys.version_info >= (3, 11) else 1)"; then
            info "Python $ver detected"
            return 0
        fi
        error "Python 3.11+ required, found $ver"
        return 1
    fi
    error "Python 3 not found"
    return 1
}

# Install mycellm
install_mycellm() {
    local gpu
    gpu=$(detect_gpu)
    info "Detected GPU backend: $gpu"

    if [[ "$gpu" == "cuda" ]]; then
        info "Installing with CUDA support..."
        CMAKE_ARGS="-DGGML_CUDA=on" pip install mycellm 2>/dev/null || \
            pip install mycellm
    else
        info "Installing (CPU mode)..."
        pip install mycellm
    fi
}

# Generate identity
setup_identity() {
    if mycellm account show &>/dev/null; then
        info "Account already exists"
    else
        info "Generating account keypair..."
        mycellm account create
    fi

    if mycellm device list 2>/dev/null | grep -q "default"; then
        info "Device certificate already exists"
    else
        info "Generating device certificate..."
        mycellm device create --name default
    fi
}

# Install systemd service
install_systemd() {
    if [[ "$(detect_os)" != "linux" ]]; then
        warn "Systemd service only available on Linux"
        return
    fi

    local service_file="/etc/systemd/system/mycellm.service"
    local user
    user=$(whoami)

    cat > "$service_file" <<EOF
[Unit]
Description=mycellm distributed inference node
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$user
ExecStart=$(command -v mycellm) serve
Restart=on-failure
RestartSec=5
Environment=MYCELLM_DATA_DIR=$HOME/.local/share/mycellm
Environment=MYCELLM_CONFIG_DIR=$HOME/.config/mycellm

[Install]
WantedBy=multi-user.target
EOF

    systemctl daemon-reload
    systemctl enable mycellm
    info "Systemd service installed. Start with: sudo systemctl start mycellm"
}

main() {
    echo -e "${GREEN}"
    echo "    ████████████████████████"
    echo "   ██████  ████████  ██████"
    echo "   ██████████████████████"
    echo "      ██████████████"
    echo "       ████████████"
    echo -e "${NC}"
    echo -e "${BOLD}mycellm installer${NC}"
    echo ""

    local os
    os=$(detect_os)
    info "OS: $os"

    check_python || exit 1

    install_mycellm
    setup_identity

    if [[ "${1:-}" == "--systemd" ]]; then
        install_systemd
    fi

    echo ""
    info "Installation complete!"
    echo ""
    echo "  Start the daemon:    mycellm serve"
    echo "  Interactive chat:    mycellm chat"
    echo "  Check status:        mycellm status"
    echo ""
}

main "$@"
