# Machine HUD: Svelte SPA built at image time, served by a small FastAPI
# backend that holds the OpenJarvis and Cognee keys. Build context: this
# directory. Base images are pinned in ../versions.env.
ARG HUD_NODE_IMAGE=node:22.22.0-bookworm-slim
ARG HUD_PYTHON_IMAGE=python:3.12.12-slim-bookworm

FROM ${HUD_NODE_IMAGE} AS web
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm test && npm run build

FROM ${HUD_PYTHON_IMAGE}
ARG HUD_VERSION=dev
LABEL org.opencontainers.image.title="machine-hud" \
      org.opencontainers.image.version="${HUD_VERSION}" \
      org.opencontainers.image.source="https://github.com/BGameiro2000/OpenJarvis" \
      org.opencontainers.image.url="https://github.com/BGameiro2000/OpenJarvis/tree/main/deploy/unraid/hud" \
      org.opencontainers.image.description="Machine HUD for OpenJarvis and Cognee on Unraid"
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/opt/hud \
    HUD_STATIC_DIR=/app \
    HUD_PORT=8080 \
    OPENJARVIS_URL=http://openjarvis:8000 \
    COGNEE_URL=http://cognee:8000
COPY backend/requirements.txt /opt/hud/requirements.txt
RUN pip install --no-cache-dir -r /opt/hud/requirements.txt
COPY backend/machine_hud /opt/hud/machine_hud
COPY --from=web /web/dist /app
USER 99:100
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=4).status == 200 else 1)"]
CMD ["python", "-m", "machine_hud.app"]
