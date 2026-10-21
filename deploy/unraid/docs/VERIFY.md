# Verification notes

Part 1 records what the code and images actually do (spec section 3), checked
against the fork at commit `cf464de1ff13d84dede80eb07e5e45c1d06708ba` and
Cognee 1.6.5. Part 2 lists the assumptions made. Part 3 holds the numbered
acceptance checks with their failure modes and recovery, plus the recorded
test output.

## Part 1: findings

### OpenJarvis

**V1. Upstream Dockerfile** (`deploy/docker/Dockerfile`)

- Three stages: `node:22.23.0-slim` builds the web UI; `python:3.12.13-slim-bookworm`
  builder installs the locked `server` extra with `uv`, installs the project and
  compiles the mandatory Rust extension `openjarvis_rust` with maturin; a
  runtime stage on the same Python image copies `/usr/local` and `/app`.
- Base images are pinned by digest.
- Runtime user `openjarvis`, UID 10001, GID 10001, `HOME=/home/openjarvis`.
  With no `OPENJARVIS_HOME`, data would go to `/home/openjarvis/.openjarvis`.
- Installed extras: only `server` (fastapi, uvicorn, pydantic, python-multipart)
  plus core dependencies. `openai` is a core dependency. `anthropic` and
  `litellm` are NOT installed.
- `uv` is left in `/usr/local/bin` (installed into the builder's `/usr/local`,
  which is copied whole). The thin layer uses it to add extras from `uv.lock`.
- The web UI is built into `openjarvis/server/static/` and served by
  `jarvis serve` on the same port (SPA fallback to `index.html`). No extra step.
- `ENTRYPOINT ["jarvis"]`, `CMD ["serve", "--host", "0.0.0.0", "--port", "8000"]`.
- No `tini`, no `HEALTHCHECK`, no `sqlite3` CLI, and Debian slim ships no
  `/usr/share/zoneinfo`.
- Python dependencies come from `uv.lock` with hashes, so a build at a pinned
  SHA is reproducible.

**V2. Auth**

- `server/auth_middleware.py`. The key comes from env `OPENJARVIS_API_KEY`,
  else from `[server.auth] api_key` in `config.toml`.
- `jarvis serve` exits with an error if it binds a non-loopback address with no
  key (`check_bind_safety`). So a key is mandatory in this container.
- With a key set, `Authorization: Bearer <key>` is required on `/v1/*`,
  `/api/*`, `/metrics`, `/openapi.json`, `/docs`, `/redoc`, for every client,
  loopback included. `/health`, the web UI files and webhook routes are open.
  This is stricter than "required for any request not from loopback".
- WebSockets check the key themselves (header, or a subprotocol the browser UI uses).
- The command is `jarvis auth create-key` (the spec's `generate-key` does not
  exist). It writes the key into `config.toml`. The generator is
  `generate_api_key()`: `oj_sk_` plus 32 random bytes, url-safe base64.
- The web UI has no login. The user pastes the key into the UI Settings page;
  it is kept in the browser's localStorage and sent as a Bearer header.

**V3. Health**

- `GET /health` calls `engine.health()`: 200 when healthy, 503 "Engine unhealthy".
- With the cloud engine, `health()` is true whenever a provider client exists,
  which needs only a key in env and the SDK installed. No network call. With
  litellm installed, the litellm engine is always healthy too. So `/health`
  does not flap on network loss, but it does report 503 if no key is set.
- Choice: `healthcheck.py` treats any HTTP answer from `/health` (200 or 503) as
  "server is up" and fails only on connection errors, timeouts or other status
  codes. A 503 is printed so it is visible in `docker inspect`.

**V4. Env and config names**

| Spec name | Reality |
|---|---|
| `OPENJARVIS_HOME` | Exists. Root for config, databases, skills, logs. Takes precedence over XDG. |
| `OPENJARVIS_CONFIG` | Exists. Explicit config file path. Not needed: `$OPENJARVIS_HOME/config.toml` is the default. |
| `OPENJARVIS_PORT` | Does not exist. Port is `[server] port` or `--port`. |
| `OPENJARVIS_ENGINE_DEFAULT` | Does not exist. Engine is `[engine] default` or `-e`. |
| `OPENJARVIS_OLLAMA_HOST` | Does not exist. Upstream reads `OLLAMA_HOST` or `[engine.ollama] host`. The entrypoint maps `OPENJARVIS_OLLAMA_HOST` to `OLLAMA_HOST`. |
| `ANTHROPIC_API_KEY`, `OPENAI_API_KEY` | Read by the `cloud` engine (and by litellm). |
| `OPENJARVIS_API_KEY` | Server API key (V2). |
| `OPENJARVIS_CORS_ORIGINS` | Exists, comma separated. Not needed for same-origin UI. |

- Engines: `cloud` (native SDKs: OpenAI, Anthropic, Google, OpenRouter,
  DeepSeek, MiniMax, Atlas Cloud) and `litellm` (any litellm provider).
- Anthropic needs the `anthropic` package (`inference-cloud` extra). Not in the
  upstream image. Added by the thin layer.
- Mistral has no native client in the `cloud` engine. It works through the
  `litellm` engine with model `mistral/<model>` and `MISTRAL_API_KEY`
  (`inference-litellm` extra, pinned upstream to `litellm==1.104.2`). Not in the
  upstream image. Added by the thin layer. The OpenAI-compatible engines
  (`vllm` and friends) take a host but no API key, so they cannot reach
  `https://api.mistral.ai/v1`.
- `jarvis serve` exits if no engine is usable or no model is found. The cloud
  engine is usable only with a key, so at least one provider key is required.

**V5. Analytics**

- `[analytics] enabled` defaults to `false` already (PostHog, opt-in).
- `jarvis init --no-analytics` records the refusal (`enabled = false`,
  `choice_recorded = true`) with no prompt.
- `jarvis init` has no `cloud` engine choice. Unattended init that works:
  `jarvis init --engine ollama --no-analytics --no-download --no-scan </dev/null`.
  It writes `config.toml`, `SOUL.md`, `MEMORY.md`, `USER.md`, `skills/`.
  It also enables `code_interpreter` and `shell_exec` in `[tools] enabled`,
  so the entrypoint rewrites that list.

**V6. Paths under `OPENJARVIS_HOME`**

All defaults are relative to `OPENJARVIS_HOME`, so mounting `/data` there keeps
every database on the cache pool.

| File | Config key |
|---|---|
| `config.toml`, `credentials.toml` | (fixed) |
| `memory.db` | `[memory] db_path` (also `[tools.storage]`, same dataclass) |
| `memory_facts.jsonl` | `[memory] facts_path` |
| `telemetry.db` | `[telemetry] db_path` (some routes hardcode the default path) |
| `traces.db` | `[traces] db_path` (some CLI paths hardcode the default) |
| `sessions.db` | `[sessions] db_path` (server session store hardcodes default) |
| `agents.db` | `[agent_manager] db_path` |
| `audit.db` | `[security] audit_log_path` |
| `optimize.db` | `[optimize] db_path` |
| `scheduler.db`, `knowledge.db`, `knowledge_graph.db`, `digest.db`, `attachments.db`, `approvals.db`, `chat.db`, `sync_state.db`, `index.db` | no key, fixed under `OPENJARVIS_HOME` |
| `skills/`, `skill-index/`, `learning/`, `operators/`, `anon_id`, `.vault_key`, logs | under `OPENJARVIS_HOME` |
| `SOUL.md`, `MEMORY.md`, `USER.md` | `[memory_files] soul_path`, `memory_path`, `user_path` |
| `personas/<name>/{SOUL,MEMORY,USER}.md` | none; always `OPENJARVIS_HOME/personas/<name>/` |

- `[memory_files]` paths are plain paths read by the prompt builder; they may
  point anywhere, including `/vault`.
- Catch: the `memory_manage` tool (the one the agent uses to edit `MEMORY.md`)
  ignores `[memory_files]` and always writes `OPENJARVIS_HOME/MEMORY.md`.
  `jarvis init` also seeds the three files in `OPENJARVIS_HOME`. Fix used:
  `/data/SOUL.md`, `/data/MEMORY.md`, `/data/USER.md` and `/data/personas` are
  symlinks into `/vault`, and `[memory_files]` points at `/vault` too.

**V7. Registering user code**

- Registries in `openjarvis.core.registry` (`AgentRegistry`, `ToolRegistry`,
  `EngineRegistry`, `MemoryRegistry`, speech and others) are filled by
  decorators such as `@ToolRegistry.register("name")` at import time.
- No entry-point plugin discovery exists. Code must be imported in the same
  process before `serve` builds the agent. Hence the launcher imports
  `OPENJARVIS_CUSTOM_MODULES` and then calls `openjarvis.cli:main`.
- A registered tool is given to the agent only if its name is in
  `[tools] enabled` (when that list is set).

**V8. External MCP servers**

- `[tools.mcp] servers` takes a JSON list (inline or a file path). Each entry has
  `name`, and either `url` (+ optional `token`, sent as Bearer) for Streamable
  HTTP, or `command` + `args` for stdio. `include_tools`, `exclude_tools` filter.
- Tools are discovered once, at startup. If the server is down then, its tools
  are skipped with a warning and never come back until a restart.
- Decision: do not use MCP for Cognee. Test 18 needs a tool that exists and
  returns a clear error while Cognee is down. Cognee's MCP server is also a
  third container, uses its own auth, and exposes write tools (`cognify`,
  `prune`) and LLM-backed search types. A small read-only tool in
  `custom/cognee_tools.py` loaded by the custom code hook is used instead.

**V9. Scheduler**

- `jarvis serve` runs the agent scheduler (`AgentScheduler`, cron and interval
  agents stored in `agents.db`) in-process when `[agent_manager] enabled`
  (default true).
- The separate task scheduler (`jarvis scheduler`, `scheduler.db`) is not
  started by `serve`; it needs `jarvis scheduler start` (foreground) or
  `jarvis scheduler run-task`.

### Cognee

**V10. Images**

- Official API image `cognee/cognee`. Latest stable on 2026-10-21 is `1.6.5`
  (amd64 and arm64), newer than the 1.6.1 in the spec. Pinned to `1.6.5`.
- Official MCP image `cognee/cognee-mcp` has only `latest`, `main` and
  `main-<sha>` tags, no versions. Not used (V8).
- Image facts: venv `/app/.venv`, user `cognee` UID 1000, `HOME=/app`,
  `SYSTEM_ROOT_DIRECTORY=/cognee-storage/system`,
  `DATA_ROOT_DIRECTORY=/cognee-storage/data`, entrypoint `/app/entrypoint.sh`
  (runs migrations then gunicorn with one uvicorn worker on `HTTP_PORT`, default
  8000), healthcheck `curl -f http://localhost:8000/health`.
- Includes the `anthropic` extra.
- Health: `GET /health` returns 503 only when a critical store (relational,
  vector, graph, file storage) is unhealthy. LLM or embedding problems give
  "degraded" with 200. Safe as a healthcheck.
- Auth: `ENABLE_BACKEND_ACCESS_CONTROL` defaults to true, which forces
  `REQUIRE_AUTHENTICATION`. Clients send `X-Api-Key: <key>`. Keys are created
  per user through the API and stored in the relational DB (hashed when
  `HASH_API_KEY=true`). There is no env var for a static key. The default user
  `default_user@example.com` has no password unless `DEFAULT_USER_PASSWORD` is set.
- Because the stock image runs as UID 1000 with `HOME=/app` and has no ingest
  tool, a thin layer `deploy/unraid/cognee/Dockerfile` is built `FROM
  cognee/cognee:1.6.5`. It sets UID 99, moves `HOME` and caches into
  `/cognee-data`, registers the API key from env at start, and adds
  `cognee-ingest`.

**V11. LLM and embeddings env**

- LLM: `LLM_PROVIDER=anthropic`, `LLM_MODEL` (the `anthropic/` prefix is
  optional), `LLM_API_KEY`.
- Embeddings: `EMBEDDING_PROVIDER`, `EMBEDDING_MODEL`, `EMBEDDING_DIMENSIONS`.
  Default is OpenAI. CPU option with no Ollama: `EMBEDDING_PROVIDER=fastembed`
  (fastembed is a core dependency). Cognee's own local default is
  `BAAI/bge-small-en-v1.5`, 384 dims; `sentence-transformers/all-MiniLM-L6-v2`
  (384) also works. Chosen: `BAAI/bge-small-en-v1.5`.
- Model cache: `FASTEMBED_CACHE_PATH`, else `$HF_HOME/hub`. Set both under
  `/cognee-data` so the download persists.

**V12. Embedded storage**

- Relational SQLite (aiosqlite), vector LanceDB, graph Ladybug (embedded, a
  Kuzu successor). All file based under `SYSTEM_ROOT_DIRECTORY`. No extra
  service.
- The graph store is an embedded DB held by the server process, so ingestion
  goes through the HTTP API, not a second Python process.

**V13. Ingestion**

- `add` accepts files, text or a local directory (recursive). Over HTTP it is
  `POST /api/v1/add` with file uploads; the vault is read, not written, so a
  read-only mount is fine.
- No exclude option. `cognee-ingest` walks the vault itself and skips folders.
- Identical content is deduplicated by content hash (re-add is a no-op). A file
  whose content changed is refused by `add` (HTTP 409) and must go through
  `PATCH /api/v1/update?data_id=..&dataset_id=..`. `cognee-ingest` keeps a
  manifest (`/cognee-data/ingest-state.json`: path, sha256, data id) so
  unchanged files are skipped without an upload, and changed files are updated.
- `cognify` is incremental by default (`incremental_loading=true`).
- No built-in estimator. `cognee-ingest --estimate` counts tokens with
  `tiktoken` (in the image) and applies a stated multiplier and price table.

**V14. Search types**

- Retrieval only, no LLM: `CHUNKS`, `CHUNKS_LEXICAL`, `SUMMARIES`.
- Any completion type with `only_context=true` returns the context the LLM
  would have seen and makes no LLM call. Recall uses
  `GRAPH_COMPLETION` with `only_context=true` (graph triplets plus chunks), so
  one question costs one LLM call (OpenJarvis's).

**V15. Graph view**

- `GET /api/v1/visualize?dataset_id=<uuid>` returns an interactive HTML page
  (needs `X-Api-Key`). From the LAN with no published port:
  `docker exec cognee curl -s -H "X-Api-Key: $KEY" "http://localhost:8000/api/v1/visualize?dataset_id=<id>" > /mnt/user/TheMachine/_backups/graph.html`.
  With the optional host port, the same request works from another machine.

**V16. Telemetry**

- Cognee posts usage events to `https://test.prometh.ai` unless
  `TELEMETRY_DISABLED` is set (any value) or `ENV` is `test`/`dev`. The image
  sets `TELEMETRY_DISABLED=1`.

## Part 2: assumptions

1. Pinned OpenJarvis commit: `cf464de1ff13d84dede80eb07e5e45c1d06708ba`
   (fork `main` on 2026-10-21). Version string `0.0.1.dev88+gcf464de1f`.
2. Default OpenJarvis engine: `cloud` with `claude-sonnet-4-6` when
   `ANTHROPIC_API_KEY` is set; else `litellm` with `mistral/mistral-medium-latest`
   when `MISTRAL_API_KEY` is set; else `cloud` with `gpt-5` when only
   `OPENAI_API_KEY` is set. Model IDs are upstream's known ones; change
   `[intelligence] default_model` in `/data/config.toml` to pick another.
3. Cognee LLM default: `claude-sonnet-5-5` (mid tier). Override with `COGNEE_LLM_MODEL`.
4. `[tools] enabled` is set to `think, calculator, web_search, memory_manage,
   cognee_recall`. No shell, code, file write or HTTP tools.
5. `[server] agent` stays `orchestrator` (upstream default, tool capable).
6. The cache pool is named `cache`. If it is not, change the template paths.
7. Time zone data: Debian slim has none; the thin layer copies the zoneinfo
   files from the `tzdata` Python package so `TZ` works.
8. No `tini`: the entrypoint `exec`s Python, which becomes PID 1; uvicorn
   installs its own SIGTERM handler, so `docker stop` shuts down cleanly.
9. SQLite backups use Python's `sqlite3.Connection.backup`, the same online
   backup API as the `sqlite3 .backup` command, so no `sqlite3` CLI is needed.

## Part 3: numbered checks

See the sections below for each acceptance test: what it checks, how it can
fail, how to recover, and the output recorded in this session.
