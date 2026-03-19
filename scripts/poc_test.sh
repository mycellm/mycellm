#!/usr/bin/env bash
# Mycellm 3-Node PoC Validation Script
# Validates the heterogeneous hardware test end-to-end.
#
# Prerequisites:
#   - Utopia (10.1.1.210) running as bootstrap + consumer (no model)
#   - Hokulea (10.1.1.11) running as seeder with tinyllama-1.1b loaded
#   - Aurora (10.1.1.81) running as seeder with tinyllama-1.1b loaded
#
# Usage: ./scripts/poc_test.sh

set -euo pipefail

# Node addresses
UTOPIA="10.1.1.210"
HOKULEA="10.1.1.11"
AURORA="10.1.1.81"
API_PORT=8420

PASS=0
FAIL=0
WARN=0

green()  { printf "\033[32m%s\033[0m\n" "$1"; }
red()    { printf "\033[31m%s\033[0m\n" "$1"; }
yellow() { printf "\033[33m%s\033[0m\n" "$1"; }
bold()   { printf "\033[1m%s\033[0m\n" "$1"; }

check_pass() { PASS=$((PASS + 1)); green "  ✓ $1"; }
check_fail() { FAIL=$((FAIL + 1)); red   "  ✗ $1"; }
check_warn() { WARN=$((WARN + 1)); yellow "  ⚠ $1"; }

# HTTP GET with timeout, returns body
api_get() {
    curl -sf --connect-timeout 5 --max-time 10 "http://${1}:${API_PORT}${2}" 2>/dev/null || echo ""
}

# HTTP POST with timeout, returns body
api_post() {
    curl -sf --connect-timeout 5 --max-time 60 -X POST \
        -H "Content-Type: application/json" \
        -d "$3" \
        "http://${1}:${API_PORT}${2}" 2>/dev/null || echo ""
}

echo ""
bold "╔══════════════════════════════════════════════╗"
bold "║   Mycellm 3-Node PoC Validation              ║"
bold "╚══════════════════════════════════════════════╝"
echo ""

# ─── Test 1: Health checks ───
bold "─── Test 1: Health Checks ───"
for node_name in "Utopia:$UTOPIA" "Hokulea:$HOKULEA" "Aurora:$AURORA"; do
    name="${node_name%%:*}"
    ip="${node_name##*:}"
    resp=$(api_get "$ip" "/health")
    if echo "$resp" | grep -q '"status":"ok"'; then
        peer_id=$(echo "$resp" | python3 -c "import sys,json; print(json.load(sys.stdin).get('peer_id','?')[:16])" 2>/dev/null || echo "?")
        check_pass "$name ($ip) healthy — peer: ${peer_id}..."
    else
        check_fail "$name ($ip) not responding"
    fi
done
echo ""

# ─── Test 2: Peer discovery ───
bold "─── Test 2: Peer Discovery ───"
peers_resp=$(api_get "$UTOPIA" "/v1/node/peers")
if [ -n "$peers_resp" ]; then
    peer_count=$(echo "$peers_resp" | python3 -c "import sys,json; print(len(json.load(sys.stdin).get('peers',[])))" 2>/dev/null || echo "0")
    if [ "$peer_count" -ge 2 ]; then
        check_pass "Utopia sees $peer_count peers"
    elif [ "$peer_count" -ge 1 ]; then
        check_warn "Utopia sees only $peer_count peer (expected 2)"
    else
        check_fail "Utopia sees 0 peers"
    fi
    # Show peer details
    echo "$peers_resp" | python3 -c "
import sys, json
data = json.load(sys.stdin)
for p in data.get('peers', []):
    models = ', '.join(p.get('models', [])) or 'none'
    print(f\"    peer: {p['peer_id'][:16]}... role={p.get('role','?')} models=[{models}] status={p.get('status','?')}\")
" 2>/dev/null || true
else
    check_fail "Could not query Utopia peers"
fi
echo ""

# ─── Test 3: Model discovery ───
bold "─── Test 3: Model Discovery ───"
models_resp=$(api_get "$UTOPIA" "/v1/models")
if [ -n "$models_resp" ]; then
    model_count=$(echo "$models_resp" | python3 -c "import sys,json; print(len(json.load(sys.stdin).get('data',[])))" 2>/dev/null || echo "0")
    if [ "$model_count" -ge 1 ]; then
        check_pass "Utopia sees $model_count model(s) across network"
        echo "$models_resp" | python3 -c "
import sys, json
data = json.load(sys.stdin)
for m in data.get('data', []):
    print(f\"    model: {m['id']}  owned_by: {m.get('owned_by','?')}\")
" 2>/dev/null || true
    else
        check_fail "No models visible on network"
    fi
else
    check_fail "Could not query models"
fi
echo ""

# ─── Test 4: Remote inference via consumer ───
bold "─── Test 4: Remote Inference (Consumer → Seeder) ───"
echo "  Sending chat request to Utopia (consumer, no local model) ..."
inference_resp=$(api_post "$UTOPIA" "/v1/chat/completions" '{
    "model": "tinyllama-1.1b",
    "messages": [{"role": "user", "content": "Say hello in exactly 5 words."}],
    "max_tokens": 64,
    "temperature": 0.3
}')
if [ -n "$inference_resp" ]; then
    # Check for actual content (not the "no model available" fallback)
    content=$(echo "$inference_resp" | python3 -c "
import sys, json
data = json.load(sys.stdin)
choices = data.get('choices', [])
if choices:
    msg = choices[0].get('message', {})
    print(msg.get('content', ''))
" 2>/dev/null || echo "")

    if [ -n "$content" ] && ! echo "$content" | grep -qi "no model available"; then
        check_pass "Inference routed successfully!"
        echo "    Response: $content"
        # Show model info
        echo "$inference_resp" | python3 -c "
import sys, json
data = json.load(sys.stdin)
usage = data.get('usage', {})
print(f\"    Model: {data.get('model','?')}  Tokens: {usage.get('prompt_tokens',0)}+{usage.get('completion_tokens',0)}\")
" 2>/dev/null || true
    else
        check_fail "Inference not routed (got fallback response)"
        echo "    Response: $content"
    fi
else
    check_fail "No response from inference request"
fi
echo ""

# ─── Test 5: Direct inference on seeders ───
bold "─── Test 5: Direct Inference on Seeders ───"
for node_name in "Hokulea:$HOKULEA" "Aurora:$AURORA"; do
    name="${node_name%%:*}"
    ip="${node_name##*:}"
    resp=$(api_post "$ip" "/v1/chat/completions" '{
        "model": "tinyllama-1.1b",
        "messages": [{"role": "user", "content": "What is 2+2?"}],
        "max_tokens": 32,
        "temperature": 0.1
    }')
    if [ -n "$resp" ]; then
        content=$(echo "$resp" | python3 -c "import sys,json; print(json.load(sys.stdin).get('choices',[{}])[0].get('message',{}).get('content',''))" 2>/dev/null || echo "")
        if [ -n "$content" ] && ! echo "$content" | grep -qi "no model"; then
            check_pass "$name direct inference works"
            echo "    Response: $(echo "$content" | head -1 | cut -c1-80)"
        else
            check_fail "$name direct inference failed"
        fi
    else
        check_fail "$name not responding to inference"
    fi
done
echo ""

# ─── Test 6: Credit accounting ───
bold "─── Test 6: Credit Accounting ───"
utopia_credits=$(api_get "$UTOPIA" "/v1/node/credits")
if [ -n "$utopia_credits" ]; then
    balance=$(echo "$utopia_credits" | python3 -c "import sys,json; print(json.load(sys.stdin).get('balance',0))" 2>/dev/null || echo "0")
    spent=$(echo "$utopia_credits" | python3 -c "import sys,json; print(json.load(sys.stdin).get('spent',0))" 2>/dev/null || echo "0")
    if python3 -c "exit(0 if float('$spent') > 0 else 1)" 2>/dev/null; then
        check_pass "Consumer (Utopia) spent credits: balance=$balance spent=$spent"
    else
        check_warn "Consumer hasn't spent credits yet (balance=$balance) — may need to wait for accounting"
    fi
else
    check_fail "Could not query Utopia credits"
fi

# Check seeder credits
for node_name in "Hokulea:$HOKULEA" "Aurora:$AURORA"; do
    name="${node_name%%:*}"
    ip="${node_name##*:}"
    credits=$(api_get "$ip" "/v1/node/credits")
    if [ -n "$credits" ]; then
        earned=$(echo "$credits" | python3 -c "import sys,json; print(json.load(sys.stdin).get('earned',0))" 2>/dev/null || echo "0")
        balance=$(echo "$credits" | python3 -c "import sys,json; print(json.load(sys.stdin).get('balance',0))" 2>/dev/null || echo "0")
        if python3 -c "exit(0 if float('$earned') > 0 else 1)" 2>/dev/null; then
            check_pass "$name earned credits: balance=$balance earned=$earned"
        else
            check_warn "$name hasn't earned credits yet (balance=$balance)"
        fi
    fi
done
echo ""

# ─── Test 7: Cross-architecture verification ───
bold "─── Test 7: Architecture Verification ───"
for node_name in "Utopia:$UTOPIA" "Hokulea:$HOKULEA" "Aurora:$AURORA"; do
    name="${node_name%%:*}"
    ip="${node_name##*:}"
    status=$(api_get "$ip" "/v1/node/status")
    if [ -n "$status" ]; then
        hw=$(echo "$status" | python3 -c "
import sys, json
data = json.load(sys.stdin)
hw = data.get('hardware', {})
print(f\"{hw.get('backend','?')} / {hw.get('gpu','?')} / {hw.get('vram_gb',0)}GB\")
" 2>/dev/null || echo "unknown")
        role=$(echo "$status" | python3 -c "import sys,json; print(json.load(sys.stdin).get('role','?'))" 2>/dev/null || echo "?")
        check_pass "$name: $hw (role=$role)"
    else
        check_fail "$name: could not get status"
    fi
done
echo ""

# ─── Summary ───
bold "═══════════════════════════════════════════════"
total=$((PASS + FAIL + WARN))
echo -n "  Results: "
green "  $PASS passed"
if [ "$WARN" -gt 0 ]; then yellow "  $WARN warnings"; fi
if [ "$FAIL" -gt 0 ]; then red "  $FAIL failed"; fi
echo ""

if [ "$FAIL" -eq 0 ]; then
    green "  PoC PASSED — Heterogeneous inference works!"
else
    red "  PoC has failures — check above for details."
fi
echo ""
