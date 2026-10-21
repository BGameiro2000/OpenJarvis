#!/bin/bash
# Cognee on Unraid: prepare /cognee-data, register the API key, then hand over
# to the official entrypoint (migrations, then gunicorn on port 8000).
set -euo pipefail

DATA=/cognee-data
log() { printf '[cognee-unraid] %s\n' "$*" >&2; }
die() { log "ERROR: $*"; exit 1; }

case "${1:-}" in
    cognee-ingest|bash|sh|python|/*) exec "$@" ;;
esac

umask 0077
[ -d "$DATA" ] && [ -w "$DATA" ] || die "$DATA is not writable by uid $(id -u). On Unraid run: chown -R 99:100 /mnt/cache/appdata/cognee"
mkdir -p "$HOME" "$SYSTEM_ROOT_DIRECTORY" "$DATA_ROOT_DIRECTORY" "$FASTEMBED_CACHE_PATH" "$HF_HOME" "$TIKTOKEN_CACHE_DIR"

# The official image pre-installs the graph DB JSON extension into /app/.lbdb
# (its HOME). Reuse it so nothing is downloaded at runtime.
if [ -d /app/.lbdb ] && [ ! -e "$HOME/.lbdb" ]; then
    cp -R /app/.lbdb "$HOME/.lbdb" 2>/dev/null || log "could not copy /app/.lbdb; the bundled extension will be installed on first use"
fi

# litellm ships tiktoken's encoding files; seed the cache so nothing is fetched.
python - <<'PY' || log "could not seed the tiktoken cache; it will download on first use"
import os, shutil
from pathlib import Path
import litellm
src = Path(litellm.__file__).parent / "litellm_core_utils" / "tokenizers"
dst = Path(os.environ["TIKTOKEN_CACHE_DIR"])
for f in src.iterdir():
    if len(f.name) == 40 and not (dst / f.name).exists():
        shutil.copyfile(f, dst / f.name)
PY

# Anthropic key: template passes ANTHROPIC_API_KEY; Cognee reads LLM_API_KEY.
export LLM_API_KEY="${LLM_API_KEY:-${ANTHROPIC_API_KEY:-}}"
export LLM_MODEL="${LLM_MODEL:-${COGNEE_LLM_MODEL:-claude-sonnet-5-5}}"
[ -n "$LLM_API_KEY" ] || log "WARNING: no ANTHROPIC_API_KEY; search works, but ingestion (cognify) will fail"

if [ -n "${COGNEE_ALLOW_VAULT_WRITE:-}" ]; then :; elif touch /vault/.cognee-write-test 2>/dev/null; then
    rm -f /vault/.cognee-write-test
    log "WARNING: /vault is writable. Mount it read-only (Access Mode: Read Only)."
fi

log "cognee $(python -c 'import importlib.metadata as m; print(m.version("cognee"))'), uid=$(id -u) gid=$(id -g), LLM ${LLM_PROVIDER}/${LLM_MODEL}, embeddings ${EMBEDDING_PROVIDER}/${EMBEDDING_MODEL}, TZ=${TZ:-unset}"

# Fetch the CPU embedding model once, before the server starts. Inside the
# server the download blocks the only worker (and /health) for minutes.
if [ "${EMBEDDING_PROVIDER}" = "fastembed" ] && [ -z "${COGNEE_SKIP_MODEL_PREFETCH:-}" ]; then
    if ! timeout "${COGNEE_MODEL_DOWNLOAD_TIMEOUT:-900}" python - <<'PY'
import os, sys
from cognee.infrastructure.databases.vector.embeddings.FastembedEmbeddingEngine import (
    fastembed_model_cached,
)
model = os.environ["EMBEDDING_MODEL"]
cached, cache_dir, size = fastembed_model_cached(model)
if cached:
    print(f"[cognee-unraid] embedding model {model} found in {cache_dir}", file=sys.stderr)
    sys.exit(0)
print(f"[cognee-unraid] downloading embedding model {model} ({size or 'size unknown'}) "
      f"to {cache_dir}; this happens once", file=sys.stderr, flush=True)
from fastembed import TextEmbedding
TextEmbedding(model_name=model, cache_dir=cache_dir)
print("[cognee-unraid] embedding model ready", file=sys.stderr)
PY
    then
        die "could not download the embedding model ${EMBEDDING_MODEL} (needs huggingface.co or storage.googleapis.com). The container will retry on restart."
    fi
fi

log "running migrations and registering the API key"
python /opt/cognee-unraid/bootstrap_api_key.py

exec /app/entrypoint.sh "$@"
