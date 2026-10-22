# OpenJarvis and Cognee on Unraid

Two containers on one Docker network:

- **openjarvis**: upstream OpenJarvis (`jarvis serve`, REST API and web UI on
  port 8000), built from this fork at a pinned commit, cloud models only.
- **cognee**: graph memory over your markdown vault, used by OpenJarvis through a
  read-only `cognee_recall` tool. OpenJarvis keeps working when Cognee is down.

Your notes stay plain markdown on the array (`/mnt/user/TheMachine`). Every
database lives on the cache pool and can be rebuilt from the notes.

## Contents

| Path | What it is |
|---|---|
| `Dockerfile`, `entrypoint.sh`, `lib/`, `healthcheck.py`, `jarvis-backup` | OpenJarvis image layer (over upstream `deploy/docker/Dockerfile`) |
| `custom/cognee_tools.py` | `cognee_recall` (on) and `cognee_remember` (off) tools |
| `custom/openjarvis_unraid_hooks.py`, `custom/examples/` | Startup hooks for code in `/data/custom` (event bus, routes, `floor.toml`) and a probe that exercises them |
| `tests/wiring.sh`, `tests/notes/` | End-to-end tests of both images with three linked notes, run in CI |
| `vault-template/` | Folder skeleton copied into an empty vault |
| `cognee/` | Cognee image layer (over official `cognee/cognee`) and `cognee-ingest` |
| `templates/` | dockerMan templates |
| `compose/docker-compose.yml` | Same setup for the Compose Manager plugin |
| `versions.env` | Pinned OpenJarvis commit and Cognee version |
| `patches/` | Build-time fixes to upstream files (`apply.sh` applies them) |
| `docs/VERIFY.md` | What the code really does, assumptions, test results |
| `docs/SERVER-TESTS.md` | Runbook for the tests that need the Unraid box |
| `docs/PERSONAS-DESIGN.md` | Plan for Finch and Root sharing one floor (not built) |

## Before you install

1. **Settings, Docker**: set "Preserve user defined networks" to **Yes**.
   Without it `jarvis-net` disappears on reboot and both containers fail to start.
2. Create the network once, from the Unraid terminal:
   `docker network create jarvis-net`
3. Create the share `TheMachine` with "Use cache pool: No" (array only).
4. Make the appdata folders, owned by nobody:users:
   ```
   mkdir -p /mnt/cache/appdata/openjarvis /mnt/cache/appdata/cognee
   chown -R 99:100 /mnt/cache/appdata/openjarvis /mnt/cache/appdata/cognee /mnt/user/TheMachine
   ```
   Use the pool path (`/mnt/cache/...`), not `/mnt/user/appdata`: `/mnt/user` goes
   through Unraid's FUSE layer (shfs), where SQLite locking and speed suffer. If your
   pool is not called `cache`, use its name. If the `appdata` share has exclusive
   access (Unraid 6.12+), `/mnt/user/appdata` already bypasses FUSE and also works.

## Images

The workflow `.github/workflows/unraid-image.yml` builds and pushes:

- `ghcr.io/bgameiro2000/openjarvis-unraid:<version>-<commit>-u<n>` (and `sha-<commit>`)
- `ghcr.io/bgameiro2000/cognee-unraid:<cognee version>-u<n>`

On a fork, GitHub Actions is **disabled by default**: open the Actions tab and
enable workflows. Then run "unraid-image" by hand (or push to `main`). After the
first push, make both packages **public** (GitHub, Packages, package settings),
or log Unraid in to GHCR: `docker login ghcr.io` with a token that has
`read:packages`.

Local build on the Unraid box, no registry needed (from a clone of this fork; the
layer is built against the pinned commit's `uv.lock`, as in CI):

```
. deploy/unraid/versions.env
git worktree add /tmp/oj-pinned "$OPENJARVIS_COMMIT"
cp -a deploy/unraid /tmp/oj-pinned/deploy/unraid
cd /tmp/oj-pinned
sh deploy/unraid/patches/apply.sh .
docker build -f deploy/docker/Dockerfile -t openjarvis-base:local .
docker build -f deploy/unraid/Dockerfile --build-arg BASE_IMAGE=openjarvis-base:local \
  --build-arg OPENJARVIS_COMMIT="$OPENJARVIS_COMMIT" \
  --build-arg OPENJARVIS_VERSION="$OPENJARVIS_VERSION" -t openjarvis-unraid:local .
docker build --build-arg COGNEE_VERSION="$COGNEE_VERSION" -t cognee-unraid:local deploy/unraid/cognee
```

Then set the template Repository to `openjarvis-unraid:local` / `cognee-unraid:local`.

## Install (dockerMan)

Copy both templates to the flash drive, then add them from Docker, Add Container:

```
cp deploy/unraid/templates/*.xml /boot/config/plugins/dockerMan/templates-user/
```

Order: network (above), **Cognee first**, then OpenJarvis.

1. **cognee**: set `ANTHROPIC_API_KEY` or `MISTRAL_API_KEY`. Leave `COGNEE_API_KEY` blank to have one
   generated; copy it from the container log (shown once, also in
   `/mnt/cache/appdata/cognee/.api_key`). First start downloads the CPU embedding
   model (about 67 MB) into `/cognee-data`; the container becomes healthy after that.
2. **openjarvis**: set one provider key (`ANTHROPIC_API_KEY`, or `MISTRAL_API_KEY`,
   or `OPENAI_API_KEY`) and paste the Cognee key into `COGNEE_API_KEY`.
   Leave `OPENJARVIS_API_KEY` blank to have one generated.
3. Open the log of `openjarvis`: the generated API key is printed once (also in
   `/mnt/cache/appdata/openjarvis/.api_key`, mode 600).
4. Open `http://SERVER_IP:8000/`, go to Settings, paste the API key. API clients send
   `Authorization: Bearer <key>`.

### Environment variables

OpenJarvis:

| Variable | Default | Meaning |
|---|---|---|
| `OPENJARVIS_API_KEY` | blank | Server key. Blank: generate once, keep in `/data/.api_key`. |
| `ANTHROPIC_API_KEY` | blank | Claude. First choice for the default model. |
| `MISTRAL_API_KEY` | blank | Mistral through litellm. Used when no Anthropic key. |
| `OPENAI_API_KEY` | blank | OpenAI. Used when neither of the above is set. |
| `COGNEE_URL` | `http://cognee:8000` | Cognee API. Blank turns the recall tool off. |
| `COGNEE_API_KEY` | blank | Must match the Cognee container. |
| `TZ` | `Europe/Madrid` | Time zone. |
| `OPENJARVIS_OLLAMA_HOST` | blank | Optional remote Ollama, passed on as `OLLAMA_HOST`. |
| `OPENJARVIS_CUSTOM_MODULES` | blank | Modules in `/data/custom` imported before the server starts. They can use `openjarvis_unraid_hooks` (see below). |

Cognee: `ANTHROPIC_API_KEY` or `MISTRAL_API_KEY` (Anthropic wins if both are
set; `COGNEE_LLM_PROVIDER=mistral` forces Mistral), `COGNEE_LLM_MODEL` (blank:
`claude-sonnet-5-5` or `mistral-medium-latest`), `COGNEE_API_KEY`,
`COGNEE_EXCLUDE`, `TZ`. For `--estimate` with Mistral prices, set
`COGNEE_PRICE_IN_PER_MTOK` and `COGNEE_PRICE_OUT_PER_MTOK`.

## First run: what happens

OpenJarvis, only when `/data/config.toml` is missing:

- Copies `vault-template/` into `/vault` if `/vault` is empty (never overwrites).
- Links `/data/SOUL.md`, `MEMORY.md`, `USER.md` and `/data/personas` into `/vault`.
  Upstream's `memory_manage` tool always writes `$OPENJARVIS_HOME/MEMORY.md`, so the
  link is what puts agent memory in the vault.
- Runs `jarvis init --no-analytics` unattended, then sets: `[server]` 0.0.0.0:8000,
  `[analytics] enabled = false`, `[skills] auto_sync = false`, `[memory_files]`
  under `/vault`, a cloud default engine from the key you set, and the tool list
  `think, calculator, web_search, memory_manage, cognee_recall` (no shell, code,
  file-write or HTTP tools). Hardware detection never picks a local model.
- Edit `/mnt/cache/appdata/openjarvis/config.toml` afterwards as you like; it is
  never overwritten. The log warns at each start if a risky setting appears.

## Cognee ingestion

Ingestion is manual first. It reads `*.md` under the vault and skips `_backups/`,
`personas/`, hidden folders (`.obsidian`, `.trash`), `AGENTS.md` files, and any
folder in `COGNEE_EXCLUDE` (for example `inbox,daily/private`).

```
docker exec cognee cognee-ingest --estimate   # file count, tokens, cost; sends nothing
docker exec cognee cognee-ingest              # add new, update changed, build graph
```

**Privacy**: with an Anthropic or Mistral key, ingestion sends the text of every
new or changed note to that provider for entity extraction. With **no key**,
Cognee extracts entities with a local GLiNER model and nothing leaves the server
(slower, simpler graph; force it with `GRAPH_EXTRACTOR=gliner_demo`). Embeddings
always run on your CPU.
Asking questions (recall) sends nothing to the LLM from Cognee: OpenJarvis asks Cognee for context only, then
makes its own single LLM call. Exclude folders you do not want sent with
`COGNEE_EXCLUDE`.

Unchanged files are skipped without upload (state in
`/mnt/cache/appdata/cognee/ingest-state.json`). Deleted notes stay in the graph
until you run `cognee-ingest --prune`. A failed file is retried on the next run.

Graph view: with the optional host port set, or from the Unraid terminal:

```
KEY=$(cat /mnt/cache/appdata/cognee/.api_key)   # or your COGNEE_API_KEY
ID=$(docker exec cognee curl -s -H "X-Api-Key: $KEY" http://localhost:8000/api/v1/datasets | python3 -c 'import json,sys;print([d["id"] for d in json.load(sys.stdin) if d["name"]=="vault"][0])')
docker exec cognee curl -s -H "X-Api-Key: $KEY" "http://localhost:8000/api/v1/visualize?dataset_id=$ID" > /mnt/user/TheMachine/reports/graph.html
```

## Custom code

Put Python modules in `/mnt/cache/appdata/openjarvis/custom/` and list them in
`OPENJARVIS_CUSTOM_MODULES`. At import they can register tools and agents
(`@ToolRegistry.register`, `@AgentRegistry.register`). For anything that needs
the running server, `openjarvis_unraid_hooks.on_server_ready(fn)` gives `fn` the
app with `app.state.bus`, `.engine`, `.agent` and `.config`;
`publish_custom` / `subscribe_custom` handle events outside upstream's list
(for example `persona.interject`); `add_routes` mounts routes (behind the API key under
`/api/`); `load_floor_config()` reads `/data/custom/floor.toml`. See
`custom/examples/floor_probe.py` and `docs/PERSONAS-DESIGN.md`.

## Scheduled jobs (User Scripts plugin)

Nothing runs on a timer inside the containers. Add these with the User Scripts plugin:

- **jarvis-backup**, daily (custom schedule `30 3 * * *`):
  ```
  #!/bin/bash
  docker exec openjarvis jarvis-backup
  ```
  Writes consistent SQLite copies (SQLite online backup, integrity checked) and the
  config to `/mnt/user/TheMachine/_backups/openjarvis/<UTC time>/`, keeping the last 7.
- **cognee-ingest**, nightly, only after a few manual runs look right
  (custom schedule `0 4 * * *`):
  ```
  #!/bin/bash
  docker exec cognee cognee-ingest
  ```

## Upgrade

1. GitHub, this fork: "Sync fork" with upstream.
   If the build log says a patch is "already" upstream, delete it from `patches/`.
2. In one commit, set `OPENJARVIS_COMMIT` and `OPENJARVIS_VERSION` in
   `deploy/unraid/versions.env` (bump `UNRAID_LAYER_VERSION` for packaging changes).
3. The workflow builds and smoke tests. Update the template Repository tag, recreate
   the container, and run the checks in `docs/VERIFY.md`.
4. Never auto-deploy an untested upstream change.

Data survives a recreate: everything lives in `/data` and `/vault`.

## Notes for this server

- **Array spin-down**: the vault is on the array. After an idle period the first
  read waits for the disk to spin up (several seconds), so the first message of a
  conversation can be slow. To avoid it, exclude that disk from spin-down
  (Settings, Disk Settings, or the disk's own spin-down delay set to Never).
- **Unclean shutdowns**: SQLite runs in WAL mode; a `docker kill` test left every
  database passing `integrity_check`. Backups are the safety net.
- **Permissions**: notes are written 666 and folders 777 as nobody:users (Unraid's
  usual "New Permissions" result), so you can edit them over SMB and in Obsidian.
  `/data` is 700; keys are 600.

## Security

- Never port-forward these containers. Use the LAN or Tailscale.
- Both containers run as UID 99 with `--cap-drop=ALL` and `no-new-privileges`, no
  privileged mode, no host network, no `docker.sock`, no devices.
- OpenJarvis requires its API key on `/v1`, `/api`, `/metrics` and the API docs, for
  every client. `/health` and the web UI files are open; the UI needs the key to do
  anything.
- Cognee has no host port by default, requires `X-Api-Key`, and mounts the vault
  read-only. Telemetry is off in both (`[analytics]`, `TELEMETRY_DISABLED`), and
  litellm's remote price list fetch is off.
- Secrets come only from env or files under `/data` / `/cognee-data` with mode 600.
  The only secrets ever logged are the two generated keys, once each.

## Test results

Full CI output: `docs/VERIFY.md`, "CI wiring run". Server tests: `docs/SERVER-TESTS.md`.


Recorded in a cloud sandbox on 2026-10-21. Docker Hub, GHCR, crates.io, Hugging
Face and Mistral were unreachable there, so the OpenJarvis layer was built on a
stand-in for the upstream image (same package and layout, built from the pinned
commit, but Ubuntu Python 3.12.3 and no Rust extension) and Cognee on a stand-in
for `cognee/cognee:1.6.5`. No real provider key was available. Full output and the
tests left for the Unraid box are in `docs/VERIFY.md`.

| # | Test | Result |
|---|---|---|
| 1 | Both build steps at the pinned SHA | Pass in GitHub Actions with the Rust patch: base 555 MB, Unraid image 777 MB; Cognee image built on the real 1.6.5. |
| 2 | Starts from the template with one key | Pass (Anthropic only; Mistral only). |
| 3 | Healthy within 90 s, no flapping | Pass: healthy at 16 to 22 s; FailingStreak 0. |
| 4 | Web UI from another machine | Pass: `GET /` 200 from another container on the network. |
| 5 | Key required; chat through cloud | Pass: 401 without/wrong key, 200 with it. Chat reached Anthropic, which rejected the test key (`invalid x-api-key`). Real answer: run on the box. |
| 6 | Stop/start, `docker kill` | Pass in CI on the real image: databases `ok` after `docker kill`, key unchanged. Repeat on the pool: SERVER-TESTS S6. |
| 7 | MEMORY.md in vault, backup | Pass in CI: `memory_manage` writes `/vault/MEMORY.md`; backup of 6 databases. Agent-driven edit: on the box. |
| 8 | Spin-down delay | Not measurable here. Run on the box. |
| 9 | UID 99, caps dropped | Pass: `uid=99 gid=100`, all capability sets 0, NoNewPrivs 1. |
| 10 | Config and storage layout | Pass: analytics off, auto_sync off, memory files in `/vault`, no SQLite in `/vault` except backups. |
| 11 | `jarvis doctor` | Saved in `docs/VERIFY.md`. 0 failures. |
| 12 | Upgrade, data intact | Pass: recreated from a newer image on the same volumes. |
| 13 | Cognee on jarvis-net, healthy, UID 99 | Pass: CI on the real image, healthy in 52 s with the real embedding model. |
| 14 | `COGNEE_URL` resolves from OpenJarvis | Pass: `http://cognee:8000 -> 200`. |
| 15 | `--estimate` sends nothing | Pass in CI: 6 files, 146 tokens, "nothing was sent". |
| 16 | Ingest three linked notes, recall | Pass in CI, keyless: ingest in 28 s, recall through OpenJarvis names Ana Ribeiro. With a real key: SERVER-TESTS S4. |
| 17 | Agent answers from the notes | Run on the box. |
| 18 | Vault read-only; Cognee down | Pass in CI: read-only vault; clear tool error; OpenJarvis restarts healthy with Cognee stopped. |
| 19 | Restart keeps graph and model | Pass in CI: graph persists, embedding model reused from `/cognee-data`. |
| 20 | Network survives reboot | Run on the box. |
| 21 | Graph view screenshot | Endpoint found (`/api/v1/visualize`). Screenshot: run on the box. |
