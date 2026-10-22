# Thin Unraid layer over the official Cognee API image.
#
#   docker build --build-arg COGNEE_VERSION=1.6.5 -t cognee-unraid:local deploy/unraid/cognee
#
# Changes from the official image: runs as 99:100, keeps every store, the
# embedding model cache and HOME under /cognee-data, local CPU embeddings,
# telemetry off, an API key taken from COGNEE_API_KEY, and cognee-ingest.

ARG COGNEE_VERSION=1.6.5
FROM cognee/cognee:${COGNEE_VERSION}

ARG COGNEE_VERSION=1.6.5
ARG COGNEE_LAYER_VERSION=dev

LABEL org.opencontainers.image.title="cognee-unraid" \
      org.opencontainers.image.description="Cognee graph memory for OpenJarvis on Unraid: UID 99, stores on /cognee-data, read-only vault" \
      org.opencontainers.image.source="https://github.com/BGameiro2000/OpenJarvis" \
      org.opencontainers.image.url="https://github.com/BGameiro2000/OpenJarvis/tree/main/deploy/unraid/cognee" \
      org.opencontainers.image.version="${COGNEE_VERSION}-unraid.${COGNEE_LAYER_VERSION}" \
      org.opencontainers.image.base.name="docker.io/cognee/cognee:${COGNEE_VERSION}"

USER root
COPY entrypoint.sh /usr/local/bin/cognee-unraid-entrypoint
COPY bootstrap_api_key.py /opt/cognee-unraid/bootstrap_api_key.py
COPY cognee-ingest /usr/local/bin/cognee-ingest
RUN chmod 0755 /usr/local/bin/cognee-unraid-entrypoint /usr/local/bin/cognee-ingest && \
    chmod -R a+rX /opt/cognee-unraid && \
    mkdir -p /cognee-data /vault && chown 99:100 /cognee-data

ENV HOME=/cognee-data/home \
    SYSTEM_ROOT_DIRECTORY=/cognee-data/system \
    DATA_ROOT_DIRECTORY=/cognee-data/data \
    FASTEMBED_CACHE_PATH=/cognee-data/models/fastembed \
    HF_HOME=/cognee-data/models/huggingface \
    TIKTOKEN_CACHE_DIR=/cognee-data/models/tiktoken \
    TELEMETRY_DISABLED=1 \
    LITELLM_LOCAL_MODEL_COST_MAP=True \
    EMBEDDING_PROVIDER=fastembed \
    EMBEDDING_MODEL=BAAI/bge-small-en-v1.5 \
    EMBEDDING_DIMENSIONS=384 \
    ENABLE_BACKEND_ACCESS_CONTROL=true \
    REQUIRE_AUTHENTICATION=true \
    COGNEE_DATASET=vault \
    GLINER_AUTO_INSTALL=false

# Unraid nobody:users. Fails the build if the official venv is not readable.
USER 99:100
RUN python -c "import cognee, fastembed, anthropic; print('cognee', cognee.__version__ if hasattr(cognee, '__version__') else 'ok')"

VOLUME ["/cognee-data"]
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD ["curl", "-fsS", "-o", "/dev/null", "http://localhost:8000/health"]

ENTRYPOINT ["/usr/local/bin/cognee-unraid-entrypoint"]
