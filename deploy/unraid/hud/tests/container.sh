#!/usr/bin/env bash
# Container smoke test for the Machine HUD, in demo mode (no upstream, no key).
# Usage: container.sh IMAGE WORKDIR
# Checks: health, UID 99, no capabilities, read-only root and vault, login
# required when a password is set, CSP header, a demo turn with Root cutting
# in, and no key in any response.
set -euo pipefail
IMAGE=${1:?image}
WORK=${2:?workdir}
NAME=hud-smoke
PORT=18080
mkdir -p "$WORK/vault"
echo "note" > "$WORK/vault/a.md"
fail() { echo "FAIL: $*"; docker logs "$NAME" 2>&1 | tail -30 || true; exit 1; }
trap 'docker rm -f "$NAME" >/dev/null 2>&1 || true' EXIT

docker rm -f "$NAME" >/dev/null 2>&1 || true
docker run -d --name "$NAME" -p "127.0.0.1:$PORT:8080" \
  --user 99:100 --cap-drop=ALL --security-opt=no-new-privileges --read-only --tmpfs /tmp:size=16m \
  -v "$WORK/vault:/vault:ro" \
  -e HUD_DEMO=1 -e HUD_PASSWORD=smoke-pass -e OPENJARVIS_API_KEY=oj-dummy-key-not-real \
  "$IMAGE" >/dev/null

for _ in $(seq 1 30); do
  curl -fsS "http://127.0.0.1:$PORT/healthz" >/dev/null 2>&1 && break
  sleep 1
done
curl -fsS "http://127.0.0.1:$PORT/healthz" >/dev/null || fail "healthz"
echo "ok   healthz"

[ "$(docker exec "$NAME" id -u)" = 99 ] || fail "uid is not 99"
cap=$(docker exec "$NAME" sh -c 'grep CapEff /proc/1/status' | awk '{print $2}')
[ "$cap" = 0000000000000000 ] || fail "capabilities left: $cap"
docker exec "$NAME" sh -c 'touch /vault/x 2>/dev/null' && fail "vault is writable"
docker exec "$NAME" sh -c 'touch /opt/hud/x 2>/dev/null' && fail "root is writable"
echo "ok   uid 99, CapEff 0, vault and root read-only"

code=$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:$PORT/api/status")
[ "$code" = 401 ] || fail "status without login gave $code"
AUTH=(-u bernardo:smoke-pass)
hdrs=$(curl -fsS -D - -o "$WORK/index.html" "${AUTH[@]}" "http://127.0.0.1:$PORT/")
echo "$hdrs" | grep -ci "content-security-policy: default-src 'self'" >/dev/null || fail "no CSP"
grep -c '<div id="app">' "$WORK/index.html" >/dev/null || fail "index not served"
echo "ok   login required, CSP set, SPA served"

curl -fsS "${AUTH[@]}" -H 'Content-Type: application/json' \
  -d '{"text":"Should I get the A2000 now, or RAM first?"}' \
  "http://127.0.0.1:$PORT/api/chat" > "$WORK/turn.sse"
grep -c '"kind":"interject"' "$WORK/turn.sse" >/dev/null || fail "no interjection in demo turn"
grep -c '"kind":"yield"' "$WORK/turn.sse" >/dev/null || fail "no yield in demo turn"
echo "ok   demo turn: Root cuts in, Finch yields"

curl -fsS "${AUTH[@]}" "http://127.0.0.1:$PORT/api/memory/search?q=RAM+upgrade" > "$WORK/search.json"
python3 - "$WORK/search.json" <<'PY' || fail "hybrid search"
import json, sys
r = json.load(open(sys.argv[1]))
assert r["hits"] and "BOTH" in {h["tag"] for h in r["hits"]}, r
assert r["ms"] < 1000, r["ms"]
PY
curl -fsS "${AUTH[@]}" "http://127.0.0.1:$PORT/api/graph?path=wiki/home-server.md" | grep -c '"nodes":\[{' >/dev/null || fail "graph"
curl -fsS "${AUTH[@]}" "http://127.0.0.1:$PORT/api/council/2026-10-21-0381" | grep -c '"decision":"WAIT"' >/dev/null || fail "council replay"
curl -fsS "${AUTH[@]}" -X POST -H 'Content-Type: application/json' -d '{"apply":false}' \
  "http://127.0.0.1:$PORT/api/intake/scan" | grep -c '"dry_run":true' >/dev/null || fail "intake dry run"
echo "ok   memory search (BOTH tags, under a second), graph, council replay, intake dry run"

curl -fsS "${AUTH[@]}" "http://127.0.0.1:$PORT/api/status" > "$WORK/status.json"
if cat "$WORK/status.json" "$WORK/turn.sse" "$WORK/index.html" | grep -c "oj-dummy-key-not-real" >/dev/null; then
  fail "key leaked to the browser"
fi
if docker exec "$NAME" sh -c 'grep -rl "oj-dummy-key-not-real" /app' >/dev/null 2>&1; then
  fail "key in the static files"
fi
echo "ok   no key in any response or static file"
echo "HUD container smoke: all passed"
