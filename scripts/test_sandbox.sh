#!/usr/bin/env bash
#
# Quick test for sandbox HTTP service + Docker containerization process
#
# Usage:
#   cd LiveClaw-500
#   bash scripts/test_sandbox.sh
#
# Prerequisites:
#   Docker daemon running (docker ps works) — Phase 2/3 requires, Phase 1 does not
#
set -euo pipefail
cd "$(dirname "$0")/.."

GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m'

pass() { echo -e "${GREEN}[PASS]${NC} $1"; }
fail() { echo -e "${RED}[FAIL]${NC} $1"; exit 1; }
info() { echo -e "${YELLOW}[INFO]${NC} $1"; }

# Keep localhost sandbox traffic off any corporate/global HTTP proxy.
LOCAL_BYPASS="localhost,127.0.0.1,::1"
export no_proxy="${no_proxy:+$no_proxy,}${LOCAL_BYPASS}"
export NO_PROXY="${NO_PROXY:+$NO_PROXY,}${LOCAL_BYPASS}"

# ====================================================================
# Phase 0: Create venv and install dependencies
# ====================================================================
info "Phase 0: Create venv and install dependencies"
if [ ! -d ".venv" ]; then
    uv venv --python 3.11
    info "Created .venv with Python 3.11"
fi
source .venv/bin/activate
uv pip install -r requirements-sandbox-server.txt -q 2>&1 | tail -1
uv pip install -e ".[mock,sandbox]" -q 2>&1 | tail -1
pass "Dependencies installed"

echo ""
# ====================================================================
# Phase 1: Start sandbox server locally (no Docker required)
# ====================================================================
info "Phase 1: Local sandbox server smoke test"

# Start sandbox server in the background
uv run python src/liveclaw_500/sandbox/server.py --port 18080 &
SERVER_PID=$!

# Verify the process is still running
if ! kill -0 $SERVER_PID 2>/dev/null; then
    fail "Failed to start sandbox server"
fi

for _ in $(seq 1 30); do
    if HEALTH=$(curl -s http://localhost:18080/health 2>/dev/null); then
        break
    fi
    sleep 0.5
done

if [ -z "${HEALTH:-}" ]; then
    fail "Sandbox server did not become ready on localhost:18080"
fi

# 1.1 health check
echo "$HEALTH" | uv run python -c "import sys,json; d=json.load(sys.stdin); assert d['status']=='ok'" \
    && pass "/health returned ok" \
    || fail "/health error: $HEALTH"

# 1.2 exec
EXEC_RESULT=$(curl -s -X POST http://localhost:18080/exec \
    -H 'Content-Type: application/json' \
    -d '{"command":"echo hello-sandbox"}')
echo "$EXEC_RESULT" | uv run python -c "
import sys, json
d = json.load(sys.stdin)
assert d['exit_code'] == 0
assert 'hello-sandbox' in d['stdout']
" && pass "/exec echo hello-sandbox" \
  || fail "/exec error: $EXEC_RESULT"

# 1.3 write + read
curl -s -X POST http://localhost:18080/write \
    -H 'Content-Type: application/json' \
    -d '{"path":"/tmp/sandbox_test.txt","content":"test-content-12345"}' > /dev/null

READ_RESULT=$(curl -s -X POST http://localhost:18080/read \
    -H 'Content-Type: application/json' \
    -d '{"path":"/tmp/sandbox_test.txt"}')
echo "$READ_RESULT" | uv run python -c "
import sys, json
d = json.load(sys.stdin)
assert d['content'] == 'test-content-12345'
" && pass "/write + /read file I/O" \
  || fail "/read error: $READ_RESULT"

# 1.4 glob
GLOB_RESULT=$(curl -s -X POST http://localhost:18080/glob \
    -H 'Content-Type: application/json' \
    -d '{"pattern":"/tmp/sandbox_test*"}')
echo "$GLOB_RESULT" | uv run python -c "
import sys, json
d = json.load(sys.stdin)
assert len(d['files']) >= 1
" && pass "/glob file matching" \
  || fail "/glob error: $GLOB_RESULT"

# 1.5 exec timeout
TIMEOUT_RESULT=$(curl -s -X POST http://localhost:18080/exec \
    -H 'Content-Type: application/json' \
    -d '{"command":"sleep 10","timeout_seconds":1}')
echo "$TIMEOUT_RESULT" | uv run python -c "
import sys, json
d = json.load(sys.stdin)
assert d['exit_code'] == -1
assert 'Timed out' in d['stderr']
" && pass "/exec timeout handling" \
  || fail "/exec timeout error: $TIMEOUT_RESULT"

# 1.6 Read a non-existent file
READ_404=$(curl -s -X POST http://localhost:18080/read \
    -H 'Content-Type: application/json' \
    -d '{"path":"/tmp/nonexistent_file_xyz.txt"}')
echo "$READ_404" | uv run python -c "
import sys, json
d = json.load(sys.stdin)
assert 'error' in d
assert 'not found' in d['error'].lower()
" && pass "/read returns error for missing file" \
  || fail "/read 404 error: $READ_404"

# Clean up the local server
kill $SERVER_PID 2>/dev/null || true
wait $SERVER_PID 2>/dev/null || true
rm -f /tmp/sandbox_test.txt
pass "Phase 1 complete: all local sandbox server smoke tests passed"

echo ""

# ====================================================================
# Phase 2: Build Docker image + start container (requires Docker daemon)
# ====================================================================
if ! command -v docker &> /dev/null; then
    info "Phase 2: Skipped (docker command not available)"
    exit 0
fi

if ! docker info &> /dev/null 2>&1; then
    info "Phase 2: Skipped (Docker daemon not running)"
    exit 0
fi

info "Phase 2: Docker image build + container smoke test"

# 2.1 Build image
info "Building liveclaw-500-agent:latest ..."
docker build -f Dockerfile.agent -t liveclaw-500-agent:latest . -q \
    && pass "Docker image built successfully" \
    || fail "Docker image build failed"

# 2.2 Start container
CONTAINER_ID=$(docker run -d --rm -p 28080:8080 --name claw-sandbox-test liveclaw-500-agent:latest)
info "Container started: $CONTAINER_ID"
sleep 3

# 2.3 health check
HEALTH=$(curl -s http://localhost:28080/health || echo '{}')
echo "$HEALTH" | uv run python -c "import sys,json; d=json.load(sys.stdin); assert d.get('status')=='ok'" \
    && pass "Container /health returned ok" \
    || fail "Container /health error: $HEALTH"

# 2.4 exec inside container
EXEC_RESULT=$(curl -s -X POST http://localhost:28080/exec \
    -H 'Content-Type: application/json' \
    -d '{"command":"python --version"}')
echo "$EXEC_RESULT" | uv run python -c "
import sys, json
d = json.load(sys.stdin)
assert d['exit_code'] == 0
assert 'Python' in d['stdout'] or 'Python' in d['stderr']
" && pass "Container /exec python --version" \
  || fail "Container /exec error: $EXEC_RESULT"

# 2.5 Isolation check: container should not include grader/mock/scoring code
ISOLATION=$(curl -s -X POST http://localhost:28080/exec \
    -H 'Content-Type: application/json' \
    -d '{"command":"find / -name grader.py 2>/dev/null | head -5"}')
echo "$ISOLATION" | uv run python -c "
import sys, json
d = json.load(sys.stdin)
assert d['stdout'].strip() == '', f'grader.py found: {d[\"stdout\"]}'
" && pass "Isolation check: no grader.py in container" \
  || fail "Isolation check failed: $ISOLATION"

ISOLATION2=$(curl -s -X POST http://localhost:28080/exec \
    -H 'Content-Type: application/json' \
    -d '{"command":"find / -name \"*.yaml\" -path \"*/tasks/*\" 2>/dev/null | head -5"}')
echo "$ISOLATION2" | uv run python -c "
import sys, json
d = json.load(sys.stdin)
assert d['stdout'].strip() == '', f'task YAML found: {d[\"stdout\"]}'
" && pass "Isolation check: no task YAML in container" \
  || fail "Isolation check failed: $ISOLATION2"

# 2.6 Write + read in container
curl -s -X POST http://localhost:28080/write \
    -H 'Content-Type: application/json' \
    -d '{"path":"/workspace/test_output.txt","content":"hello from host"}' > /dev/null

READ_CTR=$(curl -s -X POST http://localhost:28080/read \
    -H 'Content-Type: application/json' \
    -d '{"path":"/workspace/test_output.txt"}')
echo "$READ_CTR" | uv run python -c "
import sys, json
d = json.load(sys.stdin)
assert d['content'] == 'hello from host'
" && pass "Container /write + /read" \
  || fail "Container /write+/read error: $READ_CTR"

# Clean up container
docker stop claw-sandbox-test 2>/dev/null || true
pass "Phase 2 complete: all Docker container smoke tests passed"

echo ""

# ====================================================================
# Phase 3: Python API smoke test (SandboxRunner)
# ====================================================================
info "Phase 3: SandboxRunner Python API test"

uv run python -c "
from liveclaw_500.runner.sandbox_runner import SandboxRunner, ContainerHandle
from liveclaw_500.config import SandboxConfig

cfg = SandboxConfig(enabled=True)
runner = SandboxRunner(cfg)

# start container
handle = runner.start_container(run_id='smoke-test')
print(f'  Container started at {handle.sandbox_url}')

# verify via HTTP
import httpx
resp = httpx.get(f'{handle.sandbox_url}/health', timeout=5)
assert resp.json()['status'] == 'ok', 'health check failed'
print('  Health check passed')

resp = httpx.post(f'{handle.sandbox_url}/exec', json={'command': 'whoami'}, timeout=5)
print(f'  whoami: {resp.json()[\"stdout\"].strip()}')

# stop container
runner.stop_container(handle)
print('  Container stopped')
" && pass "SandboxRunner start/stop flow" \
  || fail "SandboxRunner test failed"

echo ""
echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}  All tests passed!${NC}"
echo -e "${GREEN}========================================${NC}"
