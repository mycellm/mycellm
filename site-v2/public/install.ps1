# mycellm installer for Windows
# Usage: irm https://mycellm.dev/install.ps1 | iex
$ErrorActionPreference = "Stop"

# ── Banner ──
Write-Host ""
Write-Host "    " -NoNewline; Write-Host "██████████████████████" -ForegroundColor Red
Write-Host "  " -NoNewline; Write-Host "██████████████████████████" -ForegroundColor Red
Write-Host "  " -NoNewline; Write-Host "████" -ForegroundColor Red -NoNewline; Write-Host "██████" -ForegroundColor White -NoNewline; Write-Host "████" -ForegroundColor Red -NoNewline; Write-Host "████" -ForegroundColor White -NoNewline; Write-Host "██████" -ForegroundColor Red
Write-Host "  " -NoNewline; Write-Host "████" -ForegroundColor Red -NoNewline; Write-Host "██████" -ForegroundColor White -NoNewline; Write-Host "████" -ForegroundColor Red -NoNewline; Write-Host "████" -ForegroundColor White -NoNewline; Write-Host "██████" -ForegroundColor Red
Write-Host "  " -NoNewline; Write-Host "██████████" -ForegroundColor Red -NoNewline; Write-Host "██████" -ForegroundColor White -NoNewline; Write-Host "████████" -ForegroundColor Red
Write-Host "  " -NoNewline; Write-Host "██████████" -ForegroundColor Red -NoNewline; Write-Host "██████" -ForegroundColor White -NoNewline; Write-Host "████████" -ForegroundColor Red
Write-Host "    " -NoNewline; Write-Host "██████████████████████" -ForegroundColor Yellow
Write-Host "  " -NoNewline; Write-Host "██████████████████████████" -ForegroundColor Yellow
Write-Host "  " -NoNewline; Write-Host "████" -ForegroundColor Yellow -NoNewline; Write-Host "████" -ForegroundColor DarkGray -NoNewline; Write-Host "██████████" -ForegroundColor Yellow -NoNewline; Write-Host "████" -ForegroundColor DarkGray -NoNewline; Write-Host "████" -ForegroundColor Yellow
Write-Host "  " -NoNewline; Write-Host "████" -ForegroundColor Yellow -NoNewline; Write-Host "████" -ForegroundColor DarkGray -NoNewline; Write-Host "██████████" -ForegroundColor Yellow -NoNewline; Write-Host "████" -ForegroundColor DarkGray -NoNewline; Write-Host "████" -ForegroundColor Yellow
Write-Host "  " -NoNewline; Write-Host "██████████████████████████" -ForegroundColor Yellow
Write-Host "    " -NoNewline; Write-Host "██████████████████████" -ForegroundColor Yellow
Write-Host ""
Write-Host "  " -NoNewline; Write-Host "mycellm" -ForegroundColor White -NoNewline; Write-Host "  Distributed LLM inference" -ForegroundColor DarkGray
Write-Host "  " -NoNewline; Write-Host "Free AI, powered by the crowd." -ForegroundColor DarkGray
Write-Host ""

# ── System check ──
Write-Host "  Checking system..." -ForegroundColor DarkGray

# Python
$py = Get-Command python -ErrorAction SilentlyContinue
if (-not $py) { $py = Get-Command python3 -ErrorAction SilentlyContinue }
if (-not $py) {
    Write-Host "  " -NoNewline; Write-Host "x" -ForegroundColor Red -NoNewline; Write-Host " Python 3.11+ not found"
    Write-Host ""
    Write-Host "  Install Python: " -NoNewline; Write-Host "https://www.python.org/downloads/" -ForegroundColor Cyan
    Write-Host "  " -NoNewline; Write-Host "(Check 'Add to PATH' during install)" -ForegroundColor DarkGray
    exit 1
}

$pyCmd = $py.Name
$pyVersion = & $pyCmd -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>$null
$parts = $pyVersion -split '\.'
$major = [int]$parts[0]; $minor = [int]$parts[1]

if ($major -lt 3 -or ($major -eq 3 -and $minor -lt 11)) {
    Write-Host "  " -NoNewline; Write-Host "x" -ForegroundColor Red -NoNewline; Write-Host " Python $pyVersion found, but 3.11+ required"
    Write-Host ""
    Write-Host "  Upgrade: " -NoNewline; Write-Host "https://www.python.org/downloads/" -ForegroundColor Cyan
    exit 1
}
Write-Host "  " -NoNewline; Write-Host "+" -ForegroundColor Green -NoNewline; Write-Host " Python $pyVersion"

# pip
$hasPip = & $pyCmd -m pip --version 2>$null
if ($hasPip) {
    Write-Host "  " -NoNewline; Write-Host "+" -ForegroundColor Green -NoNewline; Write-Host " pip available"
} else {
    Write-Host "  " -NoNewline; Write-Host "x" -ForegroundColor Red -NoNewline; Write-Host " pip not found"
    Write-Host "  Install: " -NoNewline; Write-Host "$pyCmd -m ensurepip" -ForegroundColor Cyan
    exit 1
}

# GPU (informational)
$nvsmi = Get-Command nvidia-smi -ErrorAction SilentlyContinue
if ($nvsmi) {
    $gpu = (nvidia-smi --query-gpu=name --format=csv,noheader 2>$null | Select-Object -First 1).Trim()
    Write-Host "  " -NoNewline; Write-Host "+" -ForegroundColor Green -NoNewline; Write-Host " GPU: $gpu " -NoNewline; Write-Host "(CUDA)" -ForegroundColor DarkGray
} else {
    Write-Host "  " -NoNewline; Write-Host "o" -ForegroundColor DarkGray -NoNewline; Write-Host " No GPU detected " -NoNewline; Write-Host "(CPU inference available)" -ForegroundColor DarkGray
}

Write-Host ""

# ── Install ──
Write-Host "  " -NoNewline; Write-Host "Installing mycellm..." -ForegroundColor White
Write-Host ""

try {
    & $pyCmd -m pip install mycellm --quiet 2>$null
    Write-Host "  " -NoNewline; Write-Host "+" -ForegroundColor Green -NoNewline; Write-Host " mycellm installed"
} catch {
    Write-Host "  " -NoNewline; Write-Host "Retrying with verbose output..." -ForegroundColor Yellow
    & $pyCmd -m pip install mycellm
}

# Verify on PATH
$mycellm = Get-Command mycellm -ErrorAction SilentlyContinue
if ($mycellm) {
    $ver = (mycellm --version 2>$null | Select-Object -First 1)
    Write-Host "  " -NoNewline; Write-Host "+" -ForegroundColor Green -NoNewline; Write-Host " $ver"
} else {
    Write-Host ""
    Write-Host "  " -NoNewline; Write-Host "!" -ForegroundColor Yellow -NoNewline; Write-Host " " -NoNewline; Write-Host "mycellm" -ForegroundColor White -NoNewline; Write-Host " not found on PATH"
    Write-Host "  " -NoNewline; Write-Host "You may need to restart your terminal or add Python Scripts to PATH." -ForegroundColor DarkGray
    Write-Host ""
}

Write-Host ""

# ── Initialize ──
Write-Host "  " -NoNewline; Write-Host "Initializing..." -ForegroundColor White
Write-Host ""

$defaultName = $env:COMPUTERNAME
$nodeName = Read-Host "  Node name [$defaultName]"
if ([string]::IsNullOrWhiteSpace($nodeName)) { $nodeName = "" }

$telemetry = Read-Host "  Share anonymous usage stats with the network? [Y/n]"
if ([string]::IsNullOrWhiteSpace($telemetry)) { $telemetry = "y" }

$hfToken = Read-Host "  HuggingFace token (optional, for gated models) [skip]"

Write-Host ""

# Write config
$configDir = if ($env:XDG_CONFIG_HOME) { "$env:XDG_CONFIG_HOME\mycellm" } else { "$env:USERPROFILE\.config\mycellm" }
New-Item -ItemType Directory -Path $configDir -Force | Out-Null

$envLines = @("MYCELLM_BOOTSTRAP_PEERS=bootstrap.mycellm.dev:8421")
if ($telemetry -match '^[Nn]') {
    $envLines += "MYCELLM_TELEMETRY=false"
} else {
    $envLines += "MYCELLM_TELEMETRY=true"
}
if (-not [string]::IsNullOrWhiteSpace($nodeName)) { $envLines += "MYCELLM_NODE_NAME=$nodeName" }
if (-not [string]::IsNullOrWhiteSpace($hfToken)) { $envLines += "MYCELLM_HF_TOKEN=$hfToken" }

$envLines | Set-Content -Path "$configDir\.env" -Encoding UTF8

# Run init
try { mycellm init --no-serve 2>$null } catch {}

Write-Host "  " -NoNewline; Write-Host "+" -ForegroundColor Green -NoNewline; Write-Host " Identity created"
Write-Host "  " -NoNewline; Write-Host "+" -ForegroundColor Green -NoNewline; Write-Host " Config written to $configDir\.env"
Write-Host ""

# ── Done ──
Write-Host "  " -NoNewline; Write-Host "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━" -ForegroundColor Green
Write-Host ""
Write-Host "  " -NoNewline; Write-Host "Ready." -ForegroundColor Green
Write-Host ""
Write-Host "  " -NoNewline; Write-Host "Start the node" -ForegroundColor White
Write-Host "    " -NoNewline; Write-Host "mycellm serve" -ForegroundColor Green
Write-Host ""
Write-Host "  " -NoNewline; Write-Host "Chat now" -ForegroundColor White -NoNewline; Write-Host " (works immediately, even without a local model)" -ForegroundColor DarkGray
Write-Host "    " -NoNewline; Write-Host "mycellm chat" -ForegroundColor Green
Write-Host ""
Write-Host "  " -NoNewline; Write-Host "Open the dashboard" -ForegroundColor White
Write-Host "    " -NoNewline; Write-Host "http://localhost:8420" -ForegroundColor Cyan
Write-Host ""
Write-Host "  " -NoNewline; Write-Host "Auto-start on boot" -ForegroundColor White
Write-Host "    " -NoNewline; Write-Host "mycellm serve --install-service" -ForegroundColor Green
Write-Host ""
Write-Host "  " -NoNewline; Write-Host "Docs: " -ForegroundColor DarkGray -NoNewline; Write-Host "https://mycellm.dev/quickstart/install/" -ForegroundColor Cyan
Write-Host "  " -NoNewline; Write-Host "Chat: " -ForegroundColor DarkGray -NoNewline; Write-Host "https://mycellm.ai" -ForegroundColor Cyan
Write-Host ""
