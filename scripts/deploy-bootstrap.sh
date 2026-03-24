#!/usr/bin/env bash
# deploy-bootstrap.sh — Blue-green deploy to bootstrap.mycellm.dev
#
# Usage:
#   ./scripts/deploy-bootstrap.sh              # from utopia (local machine)
#   ./scripts/deploy-bootstrap.sh --rollback   # revert to previous version
#
# How it works:
#   1. Rsync source from local to docker-box:/srv/docker/mycellm/app/
#   2. Build new Docker image (tagged with git hash)
#   3. Start "green" instance on port 8430 (alongside live "blue" on 8420)
#   4. Health check green — if it fails, abort (blue stays live)
#   5. Swap Caddy upstream from 8420 → 8430
#   6. Stop old blue instance
#   7. Relabel green as blue (port 8420) for next deploy cycle
#
# Requirements:
#   - SSH access: ssh docker-box (root@96.126.98.204)
#   - Caddy running as system service on docker-box
#   - Source code at ~/projects/mycellm/app/ on local machine

set -euo pipefail

HOST="docker-box"
REMOTE_DIR="/srv/docker/mycellm"
CADDY_CONFIG="/etc/caddy/Caddyfile"
DOMAIN="api.mycellm.dev"
COMPOSE_FILE="$REMOTE_DIR/docker-compose.yml"

BLUE_PORT=8420
GREEN_PORT=8430
HEALTH_RETRIES=15
HEALTH_DELAY=3

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

log() { echo -e "${GREEN}▸${NC} $*"; }
warn() { echo -e "${YELLOW}▸${NC} $*"; }
fail() { echo -e "${RED}✗${NC} $*" >&2; exit 1; }

# ── Rollback ──
if [[ "${1:-}" == "--rollback" ]]; then
    log "Rolling back Caddy to blue (port $BLUE_PORT)..."
    ssh "$HOST" "sed -i 's|reverse_proxy localhost:$GREEN_PORT|reverse_proxy localhost:$BLUE_PORT|' $CADDY_CONFIG && caddy reload --config $CADDY_CONFIG"
    log "Stopping green instance..."
    ssh "$HOST" "docker stop mycellm-green 2>/dev/null; docker rm mycellm-green 2>/dev/null" || true
    log "Rollback complete. Blue is live on :$BLUE_PORT"
    exit 0
fi

# ── Step 1: Get local version ──
VERSION=$(python3 -c "import tomllib; print(tomllib.load(open('pyproject.toml','rb'))['project']['version'])" 2>/dev/null || grep 'version' pyproject.toml | head -1 | grep -oP '"\K[^"]+')
GIT_HASH=$(git rev-parse --short HEAD 2>/dev/null || echo "unknown")
log "Deploying mycellm v${VERSION} (${GIT_HASH}) to ${DOMAIN}"

# ── Step 2: Build dashboard if needed ──
if [[ -d "web" ]] && [[ -f "web/package.json" ]]; then
    log "Building dashboard..."
    (cd web && npm run build --silent 2>&1 | tail -1)
fi

# ── Step 3: Rsync to remote ──
log "Syncing source to ${HOST}..."
rsync -az --delete \
    --exclude '.git' --exclude '.venv' --exclude 'node_modules' \
    --exclude '__pycache__' --exclude '.pytest_cache' --exclude 'dist' \
    --exclude 'web/node_modules' \
    ./ "${HOST}:${REMOTE_DIR}/app/"

# Verify sync
REMOTE_VERSION=$(ssh "$HOST" "grep '__version__' ${REMOTE_DIR}/app/src/mycellm/__init__.py | grep -oP '\"[^\"]+\"'")
log "Remote source: ${REMOTE_VERSION}"

# ── Step 4: Build Docker image ──
log "Building Docker image..."
ssh "$HOST" "cd ${REMOTE_DIR} && docker build --no-cache -t mycellm:${GIT_HASH} -t mycellm:latest -f app/docker/Dockerfile app/" 2>&1 | tail -3

# ── Step 5: Start green instance ──
log "Starting green instance on :${GREEN_PORT}..."
ssh "$HOST" "docker stop mycellm-green 2>/dev/null; docker rm mycellm-green 2>/dev/null" 2>/dev/null || true

# Read env vars from current blue instance
BLUE_ENV=$(ssh "$HOST" "docker inspect mycellm-mycellm-1 --format '{{range .Config.Env}}{{println .}}{{end}}'" 2>/dev/null | grep '^MYCELLM_' | sed 's/^/-e /' | tr '\n' ' ')

ssh "$HOST" "docker run -d --name mycellm-green \
    --network mycellm_default \
    -p 127.0.0.1:${GREEN_PORT}:8420 \
    -p 8431:8421/udp \
    -v mycellm_mycellm-data:/data/mycellm \
    ${BLUE_ENV} \
    -e MYCELLM_API_PORT=8420 \
    mycellm:${GIT_HASH}"

# ── Step 6: Health check green ──
log "Health checking green on :${GREEN_PORT}..."
for i in $(seq 1 $HEALTH_RETRIES); do
    if ssh "$HOST" "curl -sf http://localhost:${GREEN_PORT}/health" >/dev/null 2>&1; then
        REMOTE_V=$(ssh "$HOST" "curl -sf http://localhost:${GREEN_PORT}/health | python3 -c \"import json,sys; print(json.load(sys.stdin)['version'])\"")
        log "Green is healthy (v${REMOTE_V}) — attempt ${i}/${HEALTH_RETRIES}"
        break
    fi
    if [[ $i -eq $HEALTH_RETRIES ]]; then
        fail "Green failed health check after ${HEALTH_RETRIES} attempts. Aborting."
        ssh "$HOST" "docker stop mycellm-green; docker rm mycellm-green" 2>/dev/null
        exit 1
    fi
    warn "Waiting... (${i}/${HEALTH_RETRIES})"
    sleep $HEALTH_DELAY
done

# ── Step 7: Swap Caddy upstream ──
log "Swapping Caddy: ${DOMAIN} → :${GREEN_PORT}"
ssh "$HOST" "sed -i 's|reverse_proxy localhost:${BLUE_PORT}|reverse_proxy localhost:${GREEN_PORT}|' ${CADDY_CONFIG} && caddy reload --config ${CADDY_CONFIG}"

# Verify swap
sleep 2
if ssh "$HOST" "curl -sf http://localhost:${GREEN_PORT}/health" >/dev/null 2>&1; then
    log "Caddy swapped successfully"
else
    warn "Green not responding after swap — rolling back!"
    ssh "$HOST" "sed -i 's|reverse_proxy localhost:${GREEN_PORT}|reverse_proxy localhost:${BLUE_PORT}|' ${CADDY_CONFIG} && caddy reload --config ${CADDY_CONFIG}"
    fail "Rollback complete. Blue is still live."
fi

# ── Step 8: Stop old blue, promote green ──
log "Stopping old blue instance..."
ssh "$HOST" "cd ${REMOTE_DIR} && docker compose stop mycellm 2>/dev/null" || true

# Update compose to use new image and port mapping
log "Promoting green → blue (updating compose for next cycle)..."
ssh "$HOST" "cd ${REMOTE_DIR} && docker compose rm -f mycellm 2>/dev/null" || true

# Swap Caddy back to blue port (green will become the new blue)
ssh "$HOST" "sed -i 's|reverse_proxy localhost:${GREEN_PORT}|reverse_proxy localhost:${BLUE_PORT}|' ${CADDY_CONFIG}"

# Stop green, restart via compose (now on blue port with new image)
ssh "$HOST" "docker stop mycellm-green; docker rm mycellm-green" 2>/dev/null || true
ssh "$HOST" "cd ${REMOTE_DIR} && docker compose up -d mycellm" 2>&1 | tail -3

# Reload Caddy to point back to blue port
ssh "$HOST" "caddy reload --config ${CADDY_CONFIG}"

# Final health check
sleep 8
FINAL_V=$(ssh "$HOST" "curl -sf http://localhost:${BLUE_PORT}/health | python3 -c \"import json,sys; print(json.load(sys.stdin)['version'])\"" 2>/dev/null || echo "unknown")
log "Deploy complete: ${DOMAIN} → v${FINAL_V}"
echo ""
echo -e "${GREEN}✓${NC} mycellm v${VERSION} (${GIT_HASH}) is live at https://${DOMAIN}"
