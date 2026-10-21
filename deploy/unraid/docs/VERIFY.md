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
- Found in CI: at the pinned commit this Dockerfile does **not build**. It
  installs Rust 1.88, but upstream #1106 raised the workspace to Rust 1.89
  (`rust/rust-toolchain.toml`, `uuid 1.27`), and maturin runs from `/app`, where
  the toolchain file is not seen: `rustc 1.88.0 is not supported by the following
  package: uuid@1.27.0 requires rustc 1.89.0`. Fixed at build time by
  `deploy/unraid/patches/0001-docker-rust-1.89.patch` (two lines, applied by
  `patches/apply.sh`); `Dockerfile.gpu` and `Dockerfile.gpu.rocm` have the same
  bug but are not used here. Worth an upstream fix.

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
- Found in testing: with litellm installed, `serve` builds a multi-engine and
  routes the configured Claude model to `litellm` (which lists the default
  model) rather than `cloud`. Both read `ANTHROPIC_API_KEY`; the request reached
  Anthropic either way.
- Found in testing: engines `uzu` (`localhost:8000`, no prefix) and `vllm`
  (`localhost:8000/v1`) default to this server's own port. `uzu` then probes
  OpenJarvis itself and reports "Reachable". The first-start config points both
  at `http://127.0.0.1:9` (closed); `--check` warns on older configs.
- Found in testing: importing litellm fetches a model price list from
  `raw.githubusercontent.com` (retries add about 10 s when blocked). Both images
  set `LITELLM_LOCAL_MODEL_COST_MAP=True` to use the bundled copy.

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
  `HASH_API_KEY` is left off: it runs 600,000 PBKDF2 rounds on every request. The
  key sits in Cognee's SQLite under `/cognee-data` (mode 700) and in env anyway.
- Found in testing: plain `/health` runs only the storage checks (no LLM call),
  but the vector check loads the embedding model. On a fresh volume that
  triggers the model download inside the single gunicorn worker, whose retry
  sleeps block the whole server (health timeouts, API stalls). The Cognee
  entrypoint therefore downloads the model before starting the server and exits
  with a clear error if it cannot (the restart policy retries).
- `/health/detailed` does make a live LLM call (30 s timeout); not used.
- Because the stock image runs as UID 1000 with `HOME=/app` and has no ingest
  tool, a thin layer `deploy/unraid/cognee/Dockerfile` is built `FROM
  cognee/cognee:1.6.5`. It sets UID 99, moves `HOME` and caches into
  `/cognee-data`, registers the API key from env at start, and adds
  `cognee-ingest`.

**V11. LLM and embeddings env**

- LLM: `LLM_PROVIDER` (`anthropic` or `mistral` among others), `LLM_MODEL`
  (Anthropic: prefix optional; Mistral: `mistral/<model>`, sent through litellm),
  `LLM_API_KEY`.
- Embeddings: `EMBEDDING_PROVIDER`, `EMBEDDING_MODEL`, `EMBEDDING_DIMENSIONS`.
  Default is OpenAI. CPU option with no Ollama: `EMBEDDING_PROVIDER=fastembed`
  (fastembed is a core dependency). Cognee's own local default is
  `BAAI/bge-small-en-v1.5`, 384 dims; `sentence-transformers/all-MiniLM-L6-v2`
  (384) also works. Chosen: `BAAI/bge-small-en-v1.5`.
- Model cache: `FASTEMBED_CACHE_PATH`, else `$HF_HOME/hub`. Set both under
  `/cognee-data` so the download persists. Sources: `huggingface.co`, then
  `storage.googleapis.com`.
- tiktoken's `cl100k_base` file is bundled in litellm; the entrypoint seeds
  `TIKTOKEN_CACHE_DIR` (`/cognee-data/models/tiktoken`) from it, so nothing is fetched.

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
- Found in testing: `update` re-runs the graph build for that document, so it
  needs the LLM like `cognify`. Before either, Cognee runs an LLM connection test
  (30 s timeout). `external_metadata` must be a JSON list, one object per file.
- `cognee-ingest` skips `AGENTS.md` files (folder maps, not knowledge).
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
3. Cognee LLM: Anthropic `claude-sonnet-5-5` (mid tier) when `ANTHROPIC_API_KEY` is
   set, else Mistral `mistral/mistral-medium-latest` (Cognee's native `mistral`
   provider, through litellm; the official image has the `mistral` extra).
   `COGNEE_LLM_PROVIDER` and `COGNEE_LLM_MODEL` override.
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
10. File modes follow Unraid's "New Permissions" (files 666, folders 777) in the
    vault so SMB and Obsidian can edit notes; `/data` is 700 and keys 600.
    Backups are 700 (personal databases).
11. Cognee: one dataset named `vault`; each note's top folder is its `node_set`.
    Upload names are the vault path with `/` replaced by `__`.
12. Both templates add `--restart=unless-stopped` to ExtraParams so containers
    come back after an unclean shutdown.

## Part 3: numbered checks

How this session tested: Docker Hub, GHCR, crates.io, Hugging Face, Mistral and
`raw.githubusercontent.com` were blocked, and no real provider key existed.

- OpenJarvis layer built on a **stand-in** for the upstream image: the pinned
  commit installed from `uv.lock` (server extra) with the web UI built, user
  `openjarvis` UID 10001, `jarvis` and `uv` on `/usr/local/bin`, same entrypoint.
  Differences: Ubuntu Python 3.12.3 instead of Debian 3.12.13, and no
  `openjarvis_rust` (crates were unreachable). Without Rust the rate limiter and
  the SQLite memory backend are inactive; neither affects these checks.
- Cognee layer built on a **stand-in** for `cognee/cognee:1.6.5`: `cognee[anthropic]==1.6.5`
  in `/app/.venv`, user UID 1000, `HOME=/app`, same storage env, the official
  `entrypoint.sh` and healthcheck. The embedding model could not be downloaded,
  so Cognee ran with `MOCK_EMBEDDING=true` and an OpenAI-shaped embedding config.
- Containers ran with the exact `docker run` arguments derived from the
  templates (all variables passed, empty ones as `NAME=`), on `jarvis-net`.

Each check: what to run, how it can fail, how to recover, and what was recorded.

### OpenJarvis

**1. Build both steps at the pinned SHA.**
Run: the workflow, or the local build in README.
Fails if: the pinned commit no longer builds upstream (Rust toolchain, npm
engines), or `uv export` cannot read `uv.lock` (the layer needs the lock of the
same commit; CI overlays `deploy/unraid` onto the pinned checkout).
Recover: pin the previous SHA in `versions.env`.
Recorded in GitHub Actions (run 39591087906, real upstream base at the pinned
commit plus `patches/0001-docker-rust-1.89.patch`): both steps built.
`openjarvis-base:cf464de` 555 MB, `openjarvis-unraid` 777 MB (the layer adds
about 220 MB: anthropic, litellm, zoneinfo). Smoke test on the real image:
healthy after 16 s, `uid=99 gid=100`, `CapEff 0`, `/v1/models` 401 without key
and 200 with it, `GET /` 200, `Memory: active` (Rust extension present),
`jarvis-backup` copied 6 databases with integrity ok, vault skeleton created.
Without the patch the base build fails (run 39585139246): `uuid@1.27.0 requires rustc 1.89.0`.
Cognee job on the real `cognee/cognee:1.6.5`: built, embedding model prefetched
and loaded from `/cognee-data/models/fastembed`, healthy after 52 s, `uid=99`,
`CapEff 0`, datasets 401/200, `--estimate` sent nothing, `touch /vault/...`:
`Read-only file system`.

**2. Start from the template with one key.**
Recorded, Anthropic only: `config written: engine=cloud model=claude-sonnet-4-6`.
Mistral only: `engine=litellm model=mistral/mistral-medium-latest`; doctor shows
`Default model mistral/mistral-medium-latest (on litellm)`.
Fails if: no provider key (the server exits with "No inference engine available").
Recover: set a key, restart.

**3. Healthy within 90 s, no flapping.**
Recorded: healthy at 22 s (first start, includes `jarvis init`), 16 s (fresh,
Mistral), 18 s after `docker kill`. FailingStreak 0 across all runs (see the end of
this file).
Fails if: the server dies (container stops) or hangs (unhealthy). A missing key
gives `/health` 503, which the check reports but treats as up.

**4. Web UI from another machine.**
Recorded: `GET / -> 200 text/html` from a separate container on the network.
On the box: open `http://SERVER_IP:8000/` from a laptop.

**5. Key required; chat via the cloud engine.**
Recorded:
```
GET /v1/models [no header]             -> 401
GET /v1/models [Bearer wrong]          -> 401
GET /v1/models [Bearer <generated>]    -> 200
POST /v1/chat/completions (dummy key)  -> AnthropicException {"type":"authentication_error","message":"invalid x-api-key"}
```
The last line proves the request left the container and reached Anthropic.
Upstream answers it with HTTP 500. On the box, with a real key, expect an answer.

**6. Stop/start and `docker kill`.**
Recorded: `docker stop` took 2.1 s, exit code 0, uvicorn logged "Application
shutdown complete". After `docker kill` and `docker start`: `config.toml` md5
unchanged, key unchanged ("loaded from /data/.api_key"), and
```
/data/agents.db ok   /data/audit.db ok   /data/digest.db ok
/data/telemetry.db ok   /data/traces.db ok
```
Fails if: a database reports anything but `ok`. Recover: stop the container,
copy the matching file from the newest `/vault/_backups/openjarvis/<time>/`.

**7. MEMORY.md in the vault; backup opens cleanly.**
Recorded: the agent's own tool, `memory_manage add`, wrote through
`/data/MEMORY.md -> /vault/MEMORY.md`:
```
# Agent Memory
- Bernardo prefers answers in plain English (test entry)
```
After a restart the system prompt built exactly as `serve` builds it contained
the entry (`memory entry in prompt: True`). `jarvis-backup`:
```
agents.db: 53248 bytes, integrity ok
audit.db: 8192 bytes, integrity ok
digest.db: 12288 bytes, integrity ok
telemetry.db: 16384 bytes, integrity ok
traces.db: 36864 bytes, integrity ok
backup: /vault/_backups/openjarvis/20261021T102758Z (5 databases, keeping 7)
```
Each copy reopened read-only on the host: `integrity_check` ok.
On the box: ask the agent "remember that I prefer short answers", check
`MEMORY.md` in Obsidian, restart, ask in a new conversation.

**8. Spin-down delay.** Not measurable here. On the box: spin the vault disk down
(Main, click the disk's status dot), start a new conversation, note the delay.

**9. UID and capabilities.**
Recorded (both containers):
```
uid=99 gid=100(users) groups=100(users)
CapPrm: 0000000000000000  CapEff: 0000000000000000  CapBnd: 0000000000000000
NoNewPrivs: 1
```

**10. Config and storage layout.**
Recorded first-start `config.toml` (after `jarvis init`):
```
[engine]  default = "cloud"; uzu, vllm host = "http://127.0.0.1:9"
[intelligence]  default_model = fallback_model = "claude-sonnet-4-6"
[tools]  enabled = ["think", "calculator", "web_search", "memory_manage", "cognee_recall"]
[analytics]  enabled = false, choice_recorded = true
[server]  host = "0.0.0.0", port = 8000
[skills]  auto_sync = false
[memory_files]  /vault/SOUL.md, /vault/MEMORY.md, /vault/USER.md
[security]  allowed_dirs = ["/vault"]
```
SQLite files under `/vault` other than `_backups/`: none. Databases in `/data`
(`agents.db`, `audit.db`, `digest.db`, `telemetry.db`, `traces.db`), which the
template maps to `/mnt/cache/appdata/openjarvis`.

**11. `jarvis doctor`** (fresh container, Mistral key only, stand-in base):
```
OpenJarvis Doctor
┏━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃ St… ┃ Check                             ┃ Result                             ┃
┡━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┩
│  ✓  │ Python version                    │ 3.12.3                             │
│  ✓  │ Config file                       │ /data/config.toml                  │
│  ✓  │ Config parsing                    │ Config loaded successfully         │
│  !  │ Engine: apple_fm                  │ Unreachable                        │
│  !  │ Engine: cloud                     │ Unreachable                        │
│  !  │ Engine: exo                       │ Unreachable                        │
│  !  │ Engine: gemma_cpp                 │ Unreachable                        │
│  !  │ Engine: lemonade                  │ Unreachable                        │
│  ✓  │ Engine: litellm                   │ Reachable                          │
│  !  │ Engine: llamacpp                  │ Unreachable                        │
│  !  │ Engine: lmstudio                  │ Unreachable                        │
│  !  │ Engine: mlx                       │ Unreachable                        │
│  !  │ Engine: nexa                      │ Unreachable                        │
│  !  │ Engine: nim                       │ Unreachable                        │
│  !  │ Engine: ollama                    │ Unreachable                        │
│  !  │ Engine: sglang                    │ Unreachable                        │
│  !  │ Engine: uzu                       │ Unreachable                        │
│  !  │ Engine: vllm                      │ Unreachable                        │
│  ✓  │ Models: litellm                   │ mistral/mistral-medium-latest      │
│  ✓  │ Default model                     │ mistral/mistral-medium-latest (on  │
│     │                                   │ litellm)                           │
│  ✓  │ Optional: REST API server         │ Installed                          │
│  !  │ Optional: SFT/GRPO training       │ Not installed (pip install torch)  │
│  ✓  │ Optional: NVIDIA energy           │ Installed                          │
│     │ monitoring                        │                                    │
│  !  │ Optional: AMD energy monitoring   │ Not installed (openjarvis)         │
│  !  │ Optional: ColBERT memory backend  │ Not installed (openjarvis)         │
│  !  │ Optional: Apple Silicon energy    │ Not installed (openjarvis)         │
│     │ monitoring                        │                                    │
│  !  │ Optional: Apple Foundation Models │ Not installed (openjarvis)         │
│     │ (AFM 3)                           │                                    │
│  !  │ Speech backend                    │ Not configured                     │
│     │                                   │   Install desktop dependencies     │
│     │                                   │ with `uv sync --extra desktop`.    │
│  !  │ Node.js                           │ Not found                          │
│     │                                   │   Node.js 22+ is required for      │
│     │                                   │ ClaudeCodeAgent and the WhatsApp   │
│     │                                   │ Baileys channel bridge.            │
│  !  │ Security profile                  │ No security profile set            │
│     │                                   │   Recommended: add                 │
│     │                                   │ security.profile = 'personal' to   │
│     │                                   │ config.toml                        │
└─────┴───────────────────────────────────┴────────────────────────────────────┘
  8 passed, 22 warnings, 0 failures
Background tasks
  … Rust extension: building (run in background)
  no model downloads tracked
```
"Rust extension: building" appears only because the stand-in lacks
`openjarvis_rust`; the real image ships it. Rerun on the box and replace this.

**12. Upgrade.**
Recorded: rebuilt the layer as a newer image (`0.0.1.dev88-unraid.2`),
removed the container, created it from the new image on the same volumes. The
md5 of every config file, key and note was unchanged and every database passed
`integrity_check`. The log warned about the older config's `uzu`/`vllm` hosts,
as designed. On the box: bump `OPENJARVIS_COMMIT` to a newer SHA and repeat.

### Cognee and wiring

**13. Cognee on jarvis-net, healthy, UID 99, caps dropped.**
Recorded: healthy in about 90 s; `uid=99 gid=100`, capability sets 0,
NoNewPrivs 1. API key bootstrap:
```
[cognee-unraid] API key: taken from COGNEE_API_KEY
[cognee-unraid] API key registered for default_user@example.com
GET /api/v1/datasets [no key] -> 401   [wrong] -> 401   [right] -> 200
```
Blank key: "Generated Cognee API key (shown once, stored in /cognee-data/.api_key)".
Fails if: the embedding model cannot be downloaded on first start (container
exits: "could not download the embedding model ... retry on restart").
Recover: allow `huggingface.co` or `storage.googleapis.com`, restart.

**14. `COGNEE_URL` from inside OpenJarvis.**
Recorded: `http://cognee:8000 -> 200 []`.

**15. `cognee-ingest --estimate`.**
Recorded (fresh vault from the template plus two sample notes; `AGENTS.md`
folder maps are skipped):
```
vault: /vault  dataset: vault
skipped folders: _backups, personas and hidden folders
files: 6 new, 0 changed, 0 unchanged, 0 deleted
tokens to process: 177
estimated LLM usage: 531 input + 88 output tokens
estimated cost: $0.00 at $3.0/M in, $15.0/M out (x3.0 input passes; set COGNEE_PRICE_*_PER_MTOK for other models)
nothing was sent
```
`COGNEE_EXCLUDE=ideas` removed the `ideas/` notes. Cognee's access log showed no
`POST /api` during the estimate. The cost model is a stated guess (input read
about 3 times, output half the input); compare with the Anthropic console after
the first real run and tune `COGNEE_ESTIMATE_PASSES`.

**16. Ingest three linked notes, then recall.**
Recorded: upload of new notes, the id mapping, `--prune` of a deleted note, and
skipping of unchanged files all work:
```
added   wiki/Ana Ribeiro.md   added   projects/Unraid Jarvis.md   ...
files: 0 new, 1 changed, 6 unchanged (skipped), 0 deleted
removed ideas/Graph recall.md
```
`cognify` and `update` stop at Cognee's LLM connection test without a real key:
`FAILED cognify: HTTP 500 ... LLM connection test timed out after 30s`; the run
lists failures and retries them next time. Recall before cognify returns
`HTTP 404 ... knowledge graph is empty; run cognify`.
On the box: put three notes with `[[wikilinks]]` in the vault, run
`cognee-ingest`, then `docker exec openjarvis python3 -c 'import cognee_tools;
from openjarvis.core.registry import ToolRegistry as R;
print(R.get("cognee_recall")().execute(query="...").content)'`.

**17. The agent uses recall.** Needs a real key. On the box: ask a question only
the notes answer; the server log shows a `cognee_recall` tool call.

**18. Read-only vault; Cognee down.**
Recorded: `touch: cannot touch '/vault/x': Read-only file system`. With Cognee
stopped, OpenJarvis restarted, loaded `cognee_tools`, became healthy, and the
tool returned:
```
Cognee graph memory is unavailable (http://cognee:8000: ConnectError: ...).
Continue without graph memory and tell the user it could not be consulted.
```
Wrong key: `Cognee returned HTTP 401 ... Check that COGNEE_API_KEY matches in both containers.`

**19. Restart keeps graph and model.**
Recorded: after a Cognee restart the `vault` dataset, the ingest state and the
key ("already registered") persisted. The model check logs "embedding model ...
found in /cognee-data/models/fastembed" when cached. On the box: restart Cognee
after the first ingest and confirm that line and no download.

**20. Host reboot.** On the box: reboot, then `docker network ls | grep jarvis-net`
and check both containers are running. Fails if "Preserve user defined networks"
is No. Recover: set it to Yes, `docker network create jarvis-net`, start Cognee
then OpenJarvis.

**21. Graph view.** Endpoint: `GET /api/v1/visualize?dataset_id=<id>` (HTML).
Command in README. Screenshot: on the box, after test 16.

### Health transitions logged during the session

Both containers (started 11:04:51 UTC) were still `healthy` with FailingStreak 0
at 11:27:46 UTC, about 46 checks each. The `docker events` watch meant to log
every transition did not run, so this is two snapshots, not a continuous record.
