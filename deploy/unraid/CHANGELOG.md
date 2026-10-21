# Changelog: deploy/unraid

Migration notes for agents maintaining this folder. Newest first. Each entry
says what changed and what an existing install must do.

## 2026-10-21: first version

Images: `openjarvis-unraid:0.0.1.dev88-cf464de-u1` (OpenJarvis commit
`cf464de1ff13d84dede80eb07e5e45c1d06708ba`), `cognee-unraid:1.6.5-u1`
(`cognee/cognee:1.6.5`).

- New install only; nothing to migrate.
- Spec deviations, all recorded in `docs/VERIFY.md` Part 1:
  - Cognee is reached through a native tool (`custom/cognee_tools.py`), not MCP,
    because upstream discovers MCP tools only at startup.
  - Cognee uses a thin layer over the official image (UID 99, data paths, key
    bootstrap, ingest), not the bare official image.
  - Cognee pinned to 1.6.5 (latest stable), not 1.6.1.
  - The API key command upstream is `jarvis auth create-key`; the entrypoint uses
    the same generator (`generate_api_key`) and stores the key in `/data/.api_key`.
  - `OPENJARVIS_PORT`, `OPENJARVIS_ENGINE_DEFAULT`, `OPENJARVIS_OLLAMA_HOST` do not
    exist upstream; the template keeps `OPENJARVIS_OLLAMA_HOST` and maps it to
    `OLLAMA_HOST`.
  - Default tools add `memory_manage` (agent edits `MEMORY.md` in the vault) to
    upstream's `think, calculator, web_search` and `cognee_recall`.
  - No `tini`: the entrypoint `exec`s Python, and uvicorn handles SIGTERM.

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
