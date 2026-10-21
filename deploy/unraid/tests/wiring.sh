#!/bin/bash
# End-to-end tests for the two images, with no real API key.
#
#   deploy/unraid/tests/wiring.sh OPENJARVIS_IMAGE COGNEE_IMAGE [WORKDIR]
#
# Runs both containers on a private network with throwaway volumes under
# WORKDIR (default ./wiring-test), using the three linked notes in tests/notes.
# Cognee runs keyless (local GLiNER and fastembed), so it needs Hugging Face
# access but no LLM key. OpenJarvis gets a dummy Anthropic key: no chat answer
# is requested. Needs root (or sudo) to chown the volumes to 99:100.
#
# Covers acceptance tests 2, 3, 5 (auth only), 6, 9, 13, 14, 15, 16, 18, 19.
set -euo pipefail

OJ_IMAGE="${1:?openjarvis image}"
CG_IMAGE="${2:?cognee image}"
WORK="${3:-$PWD/wiring-test}"
HERE="$(cd "$(dirname "$0")" && pwd)"
NET=jarvis-wiring
OJ=wiring-openjarvis
CG=cognee            # OpenJarvis' default COGNEE_URL is http://cognee:8000
CG_KEY=wiring-cognee-key
SUDO=""; [ "$(id -u)" = 0 ] || SUDO=sudo

pass() { printf 'PASS  %s\n' "$*"; }
fail() { printf 'FAIL  %s\n' "$*"; docker logs "$OJ" 2>&1 | tail -40 || true; docker logs "$CG" 2>&1 | tail -60 || true; exit 1; }

wait_healthy() {  # name timeout_s
    local name="$1" limit="$2" start s
    start=$(date +%s)
    while :; do
        s=$(docker inspect -f '{{.State.Health.Status}}' "$name" 2>/dev/null || echo missing)
        [ "$s" = healthy ] && { echo "$name healthy after $(( $(date +%s) - start ))s"; return 0; }
        [ "$(docker inspect -f '{{.State.Running}}' "$name" 2>/dev/null)" = true ] || fail "$name stopped (state $s)"
        [ $(( $(date +%s) - start )) -lt "$limit" ] || fail "$name not healthy after ${limit}s (state $s)"
        sleep 3
    done
}

recall() {  # query -> prints "success|content"
    docker exec "$OJ" python3 -c '
import sys, cognee_tools
from openjarvis.core.registry import ToolRegistry
r = ToolRegistry.get("cognee_recall")().execute(query=sys.argv[1])
print(("OK" if r.success else "ERR") + "|" + r.content.replace("\n", " ")[:3000])
' "$1"
}

cleanup() { docker rm -f "$OJ" "$CG" >/dev/null 2>&1 || true; docker network rm "$NET" >/dev/null 2>&1 || true; }
trap cleanup EXIT
cleanup

$SUDO rm -rf "$WORK"
mkdir -p "$WORK/openjarvis" "$WORK/cognee" "$WORK/vault"
cp -R "$HERE/notes/." "$WORK/vault/"
$SUDO chown -R 99:100 "$WORK"
docker network create "$NET" >/dev/null

HARD=(--security-opt=no-new-privileges --cap-drop=ALL)

# --- Cognee -------------------------------------------------------------------
docker run -d --name "$CG" --network "$NET" "${HARD[@]}" \
    -e COGNEE_API_KEY="$CG_KEY" -e TZ=Europe/Madrid \
    -v "$WORK/cognee:/cognee-data" -v "$WORK/vault:/vault:ro" "$CG_IMAGE" >/dev/null
wait_healthy "$CG" 900
docker exec "$CG" id | grep -q 'uid=99 gid=100' && pass "13 cognee runs as 99:100" || fail "13 cognee uid"
docker exec "$CG" grep -q 'CapEff:.0000000000000000' /proc/1/status && pass "13 cognee capabilities dropped" || fail "13 cognee caps"
docker logs "$CG" 2>&1 | grep -q 'keyless ingestion' && pass "cognee started keyless (no LLM key)" || fail "keyless mode not detected"

# --- OpenJarvis ---------------------------------------------------------------
docker run -d --name "$OJ" --network "$NET" "${HARD[@]}" -p 127.0.0.1:18000:8000 \
    -e ANTHROPIC_API_KEY=sk-ant-wiring-dummy -e COGNEE_API_KEY="$CG_KEY" -e TZ=Europe/Madrid \
    -v "$WORK/openjarvis:/data" -v "$WORK/vault:/vault" "$OJ_IMAGE" >/dev/null
wait_healthy "$OJ" 90
pass "2/3 openjarvis started from env with one dummy key and is healthy within 90s"
KEY=$($SUDO cat "$WORK/openjarvis/.api_key")
code() { curl -s -o /dev/null -w '%{http_code}' "$@"; }
[ "$(code http://127.0.0.1:18000/v1/models)" = 401 ] && pass "5 request without key rejected (401)" || fail "5 no-key"
[ "$(code -H "Authorization: Bearer wrong" http://127.0.0.1:18000/v1/models)" = 401 ] && pass "5 wrong key rejected (401)" || fail "5 wrong key"
[ "$(code -H "Authorization: Bearer $KEY" http://127.0.0.1:18000/v1/models)" = 200 ] && pass "5 right key accepted (200)" || fail "5 right key"
[ "$(code http://127.0.0.1:18000/)" = 200 ] && pass "4 web UI served (200)" || fail "4 web UI"
docker exec "$OJ" id | grep -q 'uid=99 gid=100' && pass "9 openjarvis runs as 99:100" || fail "9 uid"
docker exec "$OJ" grep -q 'CapEff:.0000000000000000' /proc/1/status && pass "9 openjarvis capabilities dropped" || fail "9 caps"
docker exec "$OJ" python3 -c 'import os,httpx; r=httpx.get(os.environ["COGNEE_URL"]+"/api/v1/datasets", headers={"X-Api-Key": os.environ["COGNEE_API_KEY"]}, timeout=30); print(r.status_code); assert r.status_code == 200' \
    && pass "14 COGNEE_URL resolves from OpenJarvis and Cognee answers" || fail "14 wiring"

# --- Ingestion ----------------------------------------------------------------
est=$(docker exec "$CG" cognee-ingest --estimate)
echo "$est"
grep -q 'files: 3 new' <<<"$est" && grep -q 'nothing was sent' <<<"$est" && pass "15 estimate counts 3 notes and sends nothing" || fail "15 estimate"
docker exec "$CG" cognee-ingest || fail "16 cognee-ingest (keyless)"
pass "16 ingest of 3 linked notes (keyless)"
again=$(docker exec "$CG" cognee-ingest)
grep -q '3 unchanged' <<<"$again" && grep -q 'nothing to cognify' <<<"$again" && pass "16 unchanged notes skipped on re-run" || fail "16 re-run: $again"

# --- Recall through the OpenJarvis tool -----------------------------------------
out=$(recall "Who reviews the Unraid Jarvis project?")
echo "${out:0:600}"
[[ "$out" == OK\|* ]] && grep -qi 'Ana Ribeiro' <<<"$out" && pass "16 recall returns context naming Ana Ribeiro" || fail "16 recall: ${out:0:400}"

# --- Persistence -----------------------------------------------------------------
docker restart "$CG" >/dev/null; wait_healthy "$CG" 300
docker logs --since 5m "$CG" 2>&1 | grep -q 'embedding model .* found in' && pass "19 embedding model reused from /cognee-data" || fail "19 model downloaded again"
out=$(recall "Who reviews the Unraid Jarvis project?")
grep -qi 'Ana Ribeiro' <<<"$out" && pass "19 graph persists across a Cognee restart" || fail "19 graph after restart"

# --- Vault read-only, Cognee down -------------------------------------------------
docker exec "$CG" touch /vault/x 2>/dev/null && fail "18 cognee could write to /vault" || pass "18 cognee cannot write to /vault"
docker stop "$CG" >/dev/null
out=$(recall "anything")
[[ "$out" == ERR\|*unavailable* ]] && pass "18 tool returns a clear error with Cognee down" || fail "18 tool error: $out"
docker restart "$OJ" >/dev/null; wait_healthy "$OJ" 90
pass "18 openjarvis restarts and is healthy with Cognee stopped"

# --- docker kill ---------------------------------------------------------------
docker exec "$OJ" python3 -c 'from openjarvis.tools.memory_manage import MemoryManageTool as M; print(M().execute(action="add", entry="wiring test entry").success)' | grep -q True || fail "7 memory_manage"
grep -q 'wiring test entry' "$WORK/vault/MEMORY.md" && pass "7 agent memory tool writes /vault/MEMORY.md" || fail "7 MEMORY.md"
docker kill "$OJ" >/dev/null; docker start "$OJ" >/dev/null; wait_healthy "$OJ" 90
docker exec "$OJ" python3 -c '
import glob, sqlite3, sys
bad = [p for p in glob.glob("/data/*.db") if sqlite3.connect(p).execute("pragma integrity_check").fetchone()[0] != "ok"]
print("databases:", len(glob.glob("/data/*.db")), "bad:", bad); sys.exit(1 if bad else 0)' && pass "6 databases intact after docker kill" || fail "6 integrity"
[ "$($SUDO cat "$WORK/openjarvis/.api_key")" = "$KEY" ] && pass "6 API key survives restarts" || fail "6 key changed"
docker exec "$OJ" jarvis-backup | tail -1
[ -z "$($SUDO find "$WORK/vault" -name '*.db' -not -path '*/_backups/*')" ] && pass "10 no SQLite under /vault except backups" || fail "10 sqlite in vault"

echo "ALL WIRING TESTS PASSED"
