# deploy/unraid: map for agents

Packaging only. Upstream OpenJarvis code is never edited here; this folder and
`.github/workflows/unraid-image.yml` are the whole footprint, so syncing the fork
with upstream never conflicts. If an upstream change is ever unavoidable, ship it
as a patch in `deploy/unraid/patches/` applied at build time, and say so in
`CHANGELOG.md`.

## Files

| Path | Role | Touch when |
|---|---|---|
| `versions.env` | Pinned OpenJarvis commit and version, Cognee version, layer versions. Read by CI. | Upgrading anything. One concern per commit. |
| `Dockerfile` | OpenJarvis layer `FROM` the upstream image. Build context is the repo root. | Adding packages or files to the image. |
| `entrypoint.sh` | First-start setup: volume checks, vault skeleton, memory-file symlinks, API key, config. Then `exec`s `lib/launch.py`. | Changing startup behaviour. |
| `lib/configure.py` | Writes the first-start `config.toml` (`--first-start`) and warns on drift (`--check`). Never rewrites an existing config. | Changing defaults: engine choice, tools, paths. |
| `lib/launch.py` | Custom code hook: imports `cognee_tools` (when `COGNEE_URL` is set) and `OPENJARVIS_CUSTOM_MODULES`, then runs `openjarvis.cli:main`. | Changing how user code loads. |
| `custom/cognee_tools.py` | `cognee_recall` (read only, enabled) and `cognee_remember` (disabled). | Changing Cognee recall. |
| `healthcheck.py` | Standard library only. Healthy = HTTP server answers `/health` (200 or 503). | Rarely. |
| `jarvis-backup` | SQLite online backup of every DB under `/data` into `/vault/_backups/openjarvis/`. | Rarely. |
| `vault-template/` | Skeleton copied into an empty `/vault`. | Changing the vault layout. Existing vaults are never touched. |
| `cognee/` | Cognee layer over `cognee/cognee:<version>`: `entrypoint.sh`, `bootstrap_api_key.py`, `cognee-ingest`. | Cognee changes. |
| `templates/*.xml` | dockerMan templates. Image tags must match `versions.env`. | Every version bump. |
| `compose/docker-compose.yml` | Same as the templates for Compose Manager. Tags must match. | Every version bump. |
| `docs/VERIFY.md` | Facts checked in upstream code (Part 1), assumptions (Part 2), test results (Part 3). | When a fact changes, or after a test run. |
| `README.md` | Install, env vars, first run, backup, upgrade, privacy, security. | User-visible changes. |
| `CHANGELOG.md` | Migration notes addressed to agents. | Every change. |

## Invariants (do not break)

- Runtime UID 99, GID 100. No root at runtime. Both containers work with
  `--cap-drop=ALL` and `no-new-privileges`.
- Databases only under `/data` (OpenJarvis) and `/cognee-data` (Cognee). No SQLite
  under `/vault` except `_backups/`.
- Never overwrite `/data/config.toml` or any note in `/vault`.
- No shell, code, file-write or HTTP tool in the default `[tools] enabled`.
- Cognee mounts `/vault` read-only and publishes no port by default.
- Analytics and telemetry off; litellm remote price list off
  (`LITELLM_LOCAL_MODEL_COST_MAP=True`).
- Pins by commit SHA and exact version tag. Never `main` or `latest`.

## Upstream facts this relies on (see docs/VERIFY.md)

- `memory_manage` writes `$OPENJARVIS_HOME/MEMORY.md` regardless of `[memory_files]`;
  hence the symlinks into `/vault`.
- Tools register at import time; there is no plugin entry point. Hence `launch.py`.
- MCP tools are discovered once at startup; hence a native tool, not MCP, for Cognee.
- `jarvis serve` exits when bound to 0.0.0.0 without a key; hence the generated key.
- Engines `uzu` and `vllm` default to `localhost:8000`, which is this server; the
  first-start config points them at a closed port.
- Cognee API keys live in its database; `cognee/bootstrap_api_key.py` registers
  `COGNEE_API_KEY` for the default user at each start.
- Cognee's `/health` loads the embedding model; `cognee/entrypoint.sh` fetches it
  before the server starts.

## Checks before committing

```
for f in deploy/unraid/entrypoint.sh deploy/unraid/cognee/entrypoint.sh; do bash -n "$f"; done
python3 -m py_compile deploy/unraid/lib/*.py deploy/unraid/custom/*.py \
  deploy/unraid/healthcheck.py deploy/unraid/jarvis-backup \
  deploy/unraid/cognee/cognee-ingest deploy/unraid/cognee/bootstrap_api_key.py
(cd deploy/unraid/compose && ANTHROPIC_API_KEY=x docker compose config -q)
```

Then run the workflow; its smoke tests cover UID, capabilities, health and auth.
