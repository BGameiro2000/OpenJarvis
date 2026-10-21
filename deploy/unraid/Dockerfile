# Thin Unraid layer over the upstream OpenJarvis image.
#
# Build context: the repository root (needs pyproject.toml and uv.lock).
# BASE_IMAGE is the image built from deploy/docker/Dockerfile at the pinned
# commit. See .github/workflows/unraid-image.yml and deploy/unraid/README.md.
#
#   docker build -f deploy/docker/Dockerfile -t openjarvis-base:<sha> .
#   docker build -f deploy/unraid/Dockerfile \
#       --build-arg BASE_IMAGE=openjarvis-base:<sha> \
#       --build-arg OPENJARVIS_COMMIT=<sha> -t openjarvis-unraid:local .

ARG BASE_IMAGE=openjarvis-base:local
FROM ${BASE_IMAGE}

ARG OPENJARVIS_COMMIT=unknown
ARG OPENJARVIS_VERSION=unknown
ARG UNRAID_LAYER_VERSION=dev
# Set to 1 only when building behind a TLS-intercepting proxy (uv then uses
# the system CA store). Unset by default: uv rejects an empty UV_NATIVE_TLS.
ARG USE_SYSTEM_CERTS

LABEL org.opencontainers.image.title="openjarvis-unraid" \
      org.opencontainers.image.description="OpenJarvis (jarvis serve) packaged for Unraid: UID 99, /data on cache, markdown memory in /vault" \
      org.opencontainers.image.source="https://github.com/BGameiro2000/OpenJarvis" \
      org.opencontainers.image.url="https://github.com/BGameiro2000/OpenJarvis/tree/main/deploy/unraid" \
      org.opencontainers.image.licenses="Apache-2.0" \
      org.opencontainers.image.revision="${OPENJARVIS_COMMIT}" \
      org.opencontainers.image.version="${OPENJARVIS_VERSION}-unraid.${UNRAID_LAYER_VERSION}" \
      io.github.bgameiro2000.openjarvis.commit="${OPENJARVIS_COMMIT}"

USER root

# Cloud inference extras, resolved from the same uv.lock as the base image
# (hash checked, no re-resolution). Packages the base already has are no-ops.
COPY pyproject.toml uv.lock README.md /tmp/oj-lock/
RUN set -eu; export LITELLM_LOCAL_MODEL_COST_MAP=True; \
    if [ -n "${USE_SYSTEM_CERTS:-}" ]; then export UV_NATIVE_TLS=1; fi; \
    cd /tmp/oj-lock; \
    py="$(python3 -c 'import sys; print(sys.prefix)')/bin/python3"; \
    uv export --frozen --no-dev --no-emit-project \
        --extra server --extra inference-cloud --extra inference-litellm \
        -o requirements.txt >/dev/null; \
    uv pip install --python "$py" --no-deps -r requirements.txt; \
    python3 -c "import anthropic, litellm; print('cloud extras ok')"; \
    # Debian slim has no zoneinfo; reuse the files shipped in the tzdata wheel.
    if [ ! -e /usr/share/zoneinfo/UTC ]; then \
        python3 -c "import tzdata" 2>/dev/null || uv pip install --python "$py" "tzdata==2025.3"; \
        tz_src="$(python3 -c 'import os, tzdata; print(os.path.join(os.path.dirname(tzdata.__file__), "zoneinfo"))')"; \
        mkdir -p /usr/share/zoneinfo && cp -a "$tz_src"/. /usr/share/zoneinfo/; \
    fi; \
    rm -rf /tmp/oj-lock /root/.cache

COPY deploy/unraid/entrypoint.sh /usr/local/bin/openjarvis-unraid-entrypoint
COPY deploy/unraid/healthcheck.py /usr/local/bin/openjarvis-healthcheck
COPY deploy/unraid/jarvis-backup /usr/local/bin/jarvis-backup
COPY deploy/unraid/lib/ /opt/openjarvis-unraid/lib/
COPY deploy/unraid/custom/ /opt/openjarvis-unraid/custom/
COPY deploy/unraid/vault-template/ /opt/openjarvis-unraid/vault-template/

RUN set -eu; export LITELLM_LOCAL_MODEL_COST_MAP=True; \
    chmod 0755 /usr/local/bin/openjarvis-unraid-entrypoint \
               /usr/local/bin/openjarvis-healthcheck /usr/local/bin/jarvis-backup; \
    chmod -R a+rX /opt/openjarvis-unraid; \
    mkdir -p /data /vault; \
    chown 99:100 /data /vault; \
    printf '%s\n' "${OPENJARVIS_COMMIT}" > /opt/openjarvis-unraid/COMMIT

ENV OPENJARVIS_HOME=/data \
    HOME=/data/home \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/opt/openjarvis-unraid/custom:/data/custom \
    OPENJARVIS_NO_UPDATE_CHECK=1 \
    LITELLM_LOCAL_MODEL_COST_MAP=True \
    OPENJARVIS_COMMIT=${OPENJARVIS_COMMIT} \
    COGNEE_URL=http://cognee:8000

# Unraid nobody:users. Prove the base install is readable by this user.
USER 99:100
RUN jarvis --version && python3 -c "import openjarvis.server.app, anthropic, litellm"

VOLUME ["/data", "/vault"]
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD ["python3", "/usr/local/bin/openjarvis-healthcheck"]

ENTRYPOINT ["/usr/local/bin/openjarvis-unraid-entrypoint"]
CMD ["serve"]
