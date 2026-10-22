# Changelog: deploy/unraid

Migration notes for agents maintaining this folder. Newest first. Each entry
says what changed and what an existing install must do.

## 2026-10-22: OpenJarvis u2, Cognee u2

Images: `openjarvis-unraid:0.0.1.dev88-cf464de-u2`, `cognee-unraid:1.6.5-u2`.
Same upstream commit and Cognee version as u1; the Unraid layers changed:

- OpenJarvis: startup hooks for custom code in `/data/custom`.
- Cognee: keyless ingestion (local GLiNER), Mistral, the embedding model
  fetched at start, and a fixed `cognee-ingest` message.

Upgrading from u1: follow [docs/UPGRADE.md](docs/UPGRADE.md). In short, change
both tags to `-u2`, copy the custom package, then add the HUD. No data
migration.

## 2026-10-22: Machine HUD 0.2.0

Image: `machine-hud:0.2.0`. Memory search and graph, intake board, council
view. Nothing changes in the other two images.

- Update the template tag to `0.2.0` and Apply.
- Writes (intake scan with apply, escalation decisions, uploads, council runs)
  need the latest `home_jarvis` from branch `home-jarvis` in `/data/custom`
  (intake sorter and council). Copy it again and restart OpenJarvis.
- Optional: `COGNEE_DATASET` (default `vault`).
- Privacy: chats with people are filed to `reference/chats/`. If Cognee runs
  with a cloud key, add `reference/chats` to its `COGNEE_EXCLUDE`.

## 2026-10-21: Machine HUD 0.1.0

Image: `machine-hud:0.1.0` (new, third container). Nothing changes in the
other two images.

- New install only: add `templates/machine-hud.xml`, set
  `OPENJARVIS_API_KEY`, `COGNEE_API_KEY` and `HUD_PASSWORD`.
- Finch and Root on one floor need the `home_jarvis` package from branch
  `home-jarvis` in `/data/custom` and `OPENJARVIS_CUSTOM_MODULES=home_jarvis`.
  Without it the HUD runs in plain mode (Finch only).

## 2026-10-21: first version

Images: `openjarvis-unraid:0.0.1.dev88-cf464de-u1` (OpenJarvis commit
`cf464de1ff13d84dede80eb07e5e45c1d06708ba`), `cognee-unraid:1.6.5-u1`
(`cognee/cognee:1.6.5`).

- New install only; nothing to migrate.
- Patch `patches/0001-docker-rust-1.89.patch`: upstream `deploy/docker/Dockerfile`
  at the pinned commit installs Rust 1.88 while the crates need 1.89 (upstream
  #1106), so the upstream image does not build. The patch bumps the two rustup
  lines at build time. Delete it when upstream fixes the Dockerfile.
- Spec deviations, all recorded in `docs/VERIFY.md` Part 1:
  - Cognee is reached through a native tool (`custom/cognee_tools.py`), not MCP,
    because upstream discovers MCP tools only at startup.
  - Cognee uses a thin layer over the official image (UID 99, data paths, key
    bootstrap, ingest), not the bare official image.
  - Cognee pinned to 1.6.5 (latest stable), not 1.6.1.
  - Cognee can use Mistral as well as Anthropic for ingestion (spec: Anthropic only).
  - The API key command upstream is `jarvis auth create-key`; the entrypoint uses
    the same generator (`generate_api_key`) and stores the key in `/data/.api_key`.
  - `OPENJARVIS_PORT`, `OPENJARVIS_ENGINE_DEFAULT`, `OPENJARVIS_OLLAMA_HOST` do not
    exist upstream; the template keeps `OPENJARVIS_OLLAMA_HOST` and maps it to
    `OLLAMA_HOST`.
  - Default tools add `memory_manage` (agent edits `MEMORY.md` in the vault) to
    upstream's `think, calculator, web_search` and `cognee_recall`.
  - No `tini`: the entrypoint `exec`s Python, and uvicorn handles SIGTERM.
  - Cognee runs keyless (local GLiNER extraction) when no LLM key is set.
- Custom code hooks (`openjarvis_unraid_hooks`) for the later floor manager:
  event bus access, custom events, routes, `/data/custom/floor.toml`.

## How to write the next entry

```
## YYYY-MM-DD: <short title>

Images: <new tags>

- What changed.
- What an existing install must do (recreate, edit config.toml, re-ingest, nothing).
```

Typical cases:

- **OpenJarvis bump**: new `OPENJARVIS_COMMIT` in `versions.env`, new template and
  compose tags. Existing configs are not rewritten: if upstream renamed a config key
  this layer sets, say which key to edit in `/data/config.toml`.
- **Cognee bump**: new `COGNEE_VERSION`. Cognee runs its own migrations at start.
  If its embedding model or dimensions change, the vector store must be rebuilt:
  stop Cognee, move `/cognee-data/system` aside, delete `ingest-state.json`, start,
  re-run `cognee-ingest`.
- **Layer change**: bump `UNRAID_LAYER_VERSION` or `COGNEE_LAYER_VERSION`.
