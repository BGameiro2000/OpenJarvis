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

# LLM for ingestion: Anthropic or Mistral, from whichever key is set
# (COGNEE_LLM_PROVIDER forces one). With no key at all Cognee runs keyless:
# entities are extracted by a local GLiNER model and no note text leaves the
# box. Cognee itself reads LLM_PROVIDER, LLM_MODEL and LLM_API_KEY.
provider="${COGNEE_LLM_PROVIDER:-${LLM_PROVIDER:-}}"
if [ -z "$provider" ]; then
    if [ -n "${ANTHROPIC_API_KEY:-}" ]; then provider=anthropic
    elif [ -n "${MISTRAL_API_KEY:-}" ]; then provider=mistral
    else provider=none
    fi
fi
case "$provider" in
    anthropic)
        key="${LLM_API_KEY:-${ANTHROPIC_API_KEY:-}}"
        default_model=claude-sonnet-5-5 ;;
    mistral)
        key="${LLM_API_KEY:-${MISTRAL_API_KEY:-}}"
        [ -n "$key" ] && export MISTRAL_API_KEY="${MISTRAL_API_KEY:-$key}"
        default_model=mistral/mistral-medium-latest ;;
    none) key="" ;;
    *) die "COGNEE_LLM_PROVIDER must be anthropic or mistral, not '$provider'" ;;
esac
# Empty LLM_* values would count as configured; set them only with a key.
unset LLM_PROVIDER LLM_MODEL LLM_API_KEY
if [ -n "$key" ]; then
    export LLM_PROVIDER="$provider" LLM_API_KEY="$key"
    export LLM_MODEL="${COGNEE_LLM_MODEL:-$default_model}"
    if [ "$provider" = mistral ] && [ "${LLM_MODEL#mistral/}" = "$LLM_MODEL" ]; then
        export LLM_MODEL="mistral/$LLM_MODEL"
    fi
    llm_desc="LLM ${LLM_PROVIDER}/${LLM_MODEL}"
else
    [ "$provider" = none ] || log "WARNING: COGNEE_LLM_PROVIDER=$provider but its key is empty"
    llm_desc="no LLM key: keyless ingestion with local GLiNER (${GRAPH_EXTRACTOR:-auto})"
fi

if [ -n "${COGNEE_ALLOW_VAULT_WRITE:-}" ]; then :; elif touch /vault/.cognee-write-test 2>/dev/null; then
    rm -f /vault/.cognee-write-test
    log "WARNING: /vault is writable. Mount it read-only (Access Mode: Read Only)."
fi

log "cognee $(python -c 'import importlib.metadata as m; print(m.version("cognee"))'), uid=$(id -u) gid=$(id -g), ${llm_desc}, embeddings ${EMBEDDING_PROVIDER}/${EMBEDDING_MODEL}, TZ=${TZ:-unset}"

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

# Keyless ingestion (or GRAPH_EXTRACTOR=gliner_demo) loads a GLiNER model from
# Hugging Face on first cognify; fetch it now, into /cognee-data, for the
# same reason as the embedding model.
if { [ -z "$key" ] || [ "${GRAPH_EXTRACTOR:-}" = gliner_demo ]; } && [ -z "${COGNEE_SKIP_MODEL_PREFETCH:-}" ]; then
    if ! timeout "${COGNEE_MODEL_DOWNLOAD_TIMEOUT:-900}" python - <<'PY'
import os, sys
from cognee.tasks.graph.gliner_demo.extractor import DEFAULT_MODEL
from huggingface_hub import snapshot_download
model = DEFAULT_MODEL
print(f"[cognee-unraid] GLiNER model {model}: checking cache", file=sys.stderr, flush=True)
path = snapshot_download(model)
print(f"[cognee-unraid] GLiNER model ready in {path}", file=sys.stderr)
PY
    then
        log "WARNING: could not fetch the GLiNER model; keyless ingestion will fail until it can"
    fi
fi

log "running migrations and registering the API key"
python /opt/cognee-unraid/bootstrap_api_key.py

exec /app/entrypoint.sh "$@"
