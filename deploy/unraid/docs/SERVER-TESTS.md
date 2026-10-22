# Server runbook: install and acceptance tests on the Unraid box

For a Claude Code session running **on the Unraid server**, plus the steps
Bernardo does by hand. Everything that could be tested without the server was
tested in CI (`deploy/unraid/tests/wiring.sh`); this file covers the rest.

Record every result (command output, timings, screenshots) in a new section
"Part 4: server results" at the end of `deploy/unraid/docs/VERIFY.md`, and
paste the summary table into the "Test results" section of `README.md`.

## 0. Boundaries for the server session

The session may touch only:

| Allowed | What |
|---|---|
| Containers | `openjarvis`, `cognee` (create, start, stop, restart, kill, logs, exec, remove and recreate) |
| Network | `jarvis-net` (create, inspect) |
| Paths | `/mnt/cache/appdata/openjarvis/`, `/mnt/cache/appdata/cognee/`, `/mnt/user/TheMachine/` (the share's contents; create test notes only under `TheMachine/inbox/_test/` and delete them afterwards), `/boot/config/plugins/dockerMan/templates-user/my-openjarvis.xml` and `my-cognee.xml` |
| Images | `ghcr.io/bgameiro2000/openjarvis-unraid:*`, `ghcr.io/bgameiro2000/cognee-unraid:*`, local tags `openjarvis-unraid:local`, `cognee-unraid:local` |
| Repo | a clone of `github.com/BGameiro2000/OpenJarvis`, branch `unraid-packaging`, to read files and commit results under `deploy/unraid/` only |

It must not touch anything else: no other containers, shares, disks, array
operations, Docker or Unraid settings, plugins, VMs, users, or files outside
the paths above. It must not run `docker system prune`, `docker volume prune`
or any `rm -rf` outside the two appdata folders. Never print real API keys
into logs or commits. A reboot (test S8) and spinning a disk down (test S5)
are done by Bernardo, not by the session.

## 1. Done by hand first (Bernardo)

1. **Settings, Docker**: stop Docker, set "Preserve user defined networks" to
   **Yes**, start Docker.
2. **Shares**: create `TheMachine`, primary storage Array, "Use cache pool: No"
   (Unraid 6.12+: secondary storage none). Existing share: check this setting.
3. **GitHub, fork**: Actions tab, enable workflows if GitHub asks. Merge the PR
   from `unraid-packaging` into `main`; the push to `main` builds, tests and
   publishes the images.
4. **GitHub, Packages**: open `openjarvis-unraid` and `cognee-unraid`, Package
   settings, Change visibility, **Public**. Or keep them private and run
   `docker login ghcr.io -u BGameiro2000` on the server with a token that has
   `read:packages`.
5. Have keys ready (do not paste them into the session): an Anthropic or
   Mistral key for OpenJarvis; for Cognee the same, or none for keyless local
   ingestion.

## 2. Install (server session, in order)

```bash
# Network (persists across reboots only with step 1.1 done)
docker network inspect jarvis-net >/dev/null 2>&1 || docker network create jarvis-net

# Appdata on the cache pool, owned by nobody:users
mkdir -p /mnt/cache/appdata/openjarvis /mnt/cache/appdata/cognee
chown -R 99:100 /mnt/cache/appdata/openjarvis /mnt/cache/appdata/cognee /mnt/user/TheMachine

# Templates
B=https://raw.githubusercontent.com/BGameiro2000/OpenJarvis/main/deploy/unraid/templates
wget -q "$B/cognee.xml"     -O /boot/config/plugins/dockerMan/templates-user/my-cognee.xml
wget -q "$B/openjarvis.xml" -O /boot/config/plugins/dockerMan/templates-user/my-openjarvis.xml

# Images
docker pull ghcr.io/bgameiro2000/cognee-unraid:1.6.5-u1
docker pull ghcr.io/bgameiro2000/openjarvis-unraid:0.0.1.dev88-cf464de-u1
```

Expected: `docker network ls` lists `jarvis-net`; both pulls end with
"Status: Downloaded". If a pull says `denied`, step 1.4 is not done.

Then Bernardo, in the web UI (keys are typed by him, not by the session):

1. Docker, Add Container, template **cognee**. Set `ANTHROPIC_API_KEY` or
   `MISTRAL_API_KEY` (or neither for keyless), leave `COGNEE_API_KEY` blank,
   Apply. Wait for "healthy" (first start downloads about 67 MB, plus the
   GLiNER model when keyless).
2. Copy the key: `cat /mnt/cache/appdata/cognee/.api_key`.
3. Template **openjarvis**: a provider key, `COGNEE_API_KEY` = that key, leave
   `OPENJARVIS_API_KEY` blank, Apply.
4. `cat /mnt/cache/appdata/openjarvis/.api_key`, open
   `http://SERVER_IP:8000/`, Settings, paste it.

## 3. Tests that need the server

Set these once in the session:

```bash
OJ_KEY=$(cat /mnt/cache/appdata/openjarvis/.api_key)
CG_KEY=$(cat /mnt/cache/appdata/cognee/.api_key)
IP=$(ip -4 -o addr show br0 2>/dev/null | awk '{print $4}' | cut -d/ -f1); echo "$IP"
```

### S1. Paths: databases on the pool, notes on the array (spec test 10)

```bash
docker inspect openjarvis --format '{{range .Mounts}}{{.Source}} -> {{.Destination}}{{"\n"}}{{end}}'
docker inspect cognee     --format '{{range .Mounts}}{{.Source}} -> {{.Destination}} {{.RW}}{{"\n"}}{{end}}'
ls /mnt/cache/appdata/openjarvis/*.db
find /mnt/user/TheMachine -name '*.db' -not -path '*/_backups/*'
grep -E 'enabled|auto_sync|memory_path' /mnt/cache/appdata/openjarvis/config.toml
```

Expected: `/mnt/cache/appdata/openjarvis -> /data`,
`/mnt/user/TheMachine -> /vault`; Cognee's `/vault` with `false` (read only);
databases listed under `/mnt/cache/...`; the `find` prints nothing; analytics
`enabled = false`, `auto_sync = false`, `memory_path = "/vault/MEMORY.md"`.
If it fails: a path points at `/mnt/user/appdata` instead of `/mnt/cache/...`
(edit the template; if the pool is not named `cache`, use its name).

### S2. Real chat through the cloud engine (spec test 5)

```bash
curl -s -m 120 http://127.0.0.1:8000/v1/chat/completions \
  -H "Authorization: Bearer $OJ_KEY" -H 'Content-Type: application/json' \
  -d '{"model":"claude-sonnet-4-6","messages":[{"role":"user","content":"Reply with the word ready."}]}' | head -c 600
```

Expected: JSON with `"content"` containing "ready". With a Mistral-only setup
use `"model":"mistral/mistral-medium-latest"`. If it fails: `docker logs
openjarvis | tail -50`; "invalid x-api-key" means the provider key is wrong;
"No model available" means no provider key reached the container.

### S3. LAN access from another machine (spec test 4)

From a laptop on the LAN (Bernardo, or the session via `ssh` to it only if he
allows): open `http://SERVER_IP:8000/`, then

```bash
curl -s -o /dev/null -w '%{http_code}\n' http://SERVER_IP:8000/v1/models                         # 401
curl -s -o /dev/null -w '%{http_code}\n' -H "Authorization: Bearer KEY" http://SERVER_IP:8000/v1/models  # 200
```

Expected: the UI loads; 401 then 200. Over Tailscale the same with the
Tailscale IP. If the page does not load: port 8000 taken by another container
(`docker ps --format '{{.Names}} {{.Ports}}' | grep 8000`), or a firewall.

### S4. Real ingestion and recall on three notes (spec tests 16, 17)

```bash
mkdir -p /mnt/user/TheMachine/inbox/_test
cat > "/mnt/user/TheMachine/inbox/_test/Ana Ribeiro.md" <<'EOF'
# Ana Ribeiro
Neutron physicist at [[CERN]]. She reviews the [[Unraid Jarvis]] project.
EOF
cat > "/mnt/user/TheMachine/inbox/_test/Unraid Jarvis.md" <<'EOF'
# Unraid Jarvis
Home assistant on [[OpenJarvis]] with [[Cognee]]. Reviewer: [[Ana Ribeiro]].
EOF
cat > "/mnt/user/TheMachine/inbox/_test/Graph recall.md" <<'EOF'
# Graph recall
Ask [[Cognee]] for context only. Related: [[Unraid Jarvis]].
EOF
chown -R 99:100 /mnt/user/TheMachine/inbox/_test && chmod 666 /mnt/user/TheMachine/inbox/_test/*.md
docker exec cognee cognee-ingest --estimate
docker exec cognee cognee-ingest
docker exec openjarvis python3 -c 'import cognee_tools; from openjarvis.core.registry import ToolRegistry as R; r=R.get("cognee_recall")().execute(query="Who reviews the Unraid Jarvis project?"); print(r.success, r.content[:800])'
```

Expected: estimate lists the new files and "nothing was sent"; ingest ends with
"cognify: done" and "0 error(s)"; recall prints `True` and text naming Ana
Ribeiro. Then in the web UI ask "Who reviews my Unraid Jarvis project?" and
check `docker logs openjarvis | grep -i cognee_recall` shows a tool call (17).
If ingest fails with an LLM error: the provider key in the Cognee template.
Afterwards: `rm -r /mnt/user/TheMachine/inbox/_test` and
`docker exec cognee cognee-ingest --prune`.

### S5. Array spin-down delay (spec test 8, Bernardo spins the disk down)

1. Find the disk holding the share: `ls -d /mnt/disk*/TheMachine`.
2. Main tab, click the green dot of that disk to spin it down (Bernardo).
3. Then:

```bash
time curl -s -m 120 http://127.0.0.1:8000/v1/chat/completions \
  -H "Authorization: Bearer $OJ_KEY" -H 'Content-Type: application/json' \
  -d '{"model":"claude-sonnet-4-6","messages":[{"role":"user","content":"What do you remember about me?"}]}' -o /dev/null
```

Repeat once with the disk spinning. Report both `real` times and the
difference in the README ("Array spin-down"). Expected: a few seconds more when
asleep. If it is too slow: set that disk's spin-down delay to Never.

### S6. `docker kill` corruption check (spec test 6, on the real pool)

```bash
docker exec openjarvis jarvis-backup
docker kill openjarvis && docker start openjarvis
sleep 30; docker inspect -f '{{.State.Health.Status}}' openjarvis
docker exec openjarvis python3 -c 'import glob,sqlite3; [print(p, sqlite3.connect(p).execute("pragma integrity_check").fetchone()[0]) for p in sorted(glob.glob("/data/*.db"))]'
docker kill cognee && docker start cognee
sleep 90; docker inspect -f '{{.State.Health.Status}}' cognee
docker exec openjarvis python3 -c 'import cognee_tools; from openjarvis.core.registry import ToolRegistry as R; print(R.get("cognee_recall")().execute(query="Unraid Jarvis").success)'
```

Expected: `healthy` twice, every database `ok`, recall `True`. If a database
is not `ok`: stop the container, copy that file from the newest
`/mnt/user/TheMachine/_backups/openjarvis/<time>/` to
`/mnt/cache/appdata/openjarvis/`, `chown 99:100`, start.

### S7. Backup restore drill and User Scripts (spec test 7)

```bash
ls /mnt/user/TheMachine/_backups/openjarvis/
python3 -c 'import glob,sqlite3; d=sorted(glob.glob("/mnt/user/TheMachine/_backups/openjarvis/*/"))[-1]; [print(p, sqlite3.connect(p).execute("pragma integrity_check").fetchone()[0]) for p in glob.glob(d+"*.db")]'
```

Expected: timestamped folders, at most 7; every copy `ok`. Bernardo adds the
two User Scripts entries from README "Scheduled jobs" (backup daily now;
ingest nightly only after a few trusted manual runs).

### S8. Reboot survival (spec test 20, Bernardo reboots)

After the reboot:

```bash
docker network ls | grep jarvis-net
docker ps --format '{{.Names}} {{.Status}}' | grep -E 'openjarvis|cognee'
curl -s http://127.0.0.1:8000/health
```

Expected: `jarvis-net` listed, both containers `Up ... (healthy)`,
`{"status":"ok"}`. If the network is missing: step 1.1 was not saved; recreate
it and start Cognee, then OpenJarvis. If the containers are not running:
check their autostart toggle on the Docker tab.

### S9. Graph view screenshot (spec test 21)

```bash
ID=$(docker exec cognee curl -s -H "X-Api-Key: $CG_KEY" http://localhost:8000/api/v1/datasets | python3 -c 'import json,sys; print([d["id"] for d in json.load(sys.stdin) if d["name"]=="vault"][0])')
docker exec cognee curl -s -H "X-Api-Key: $CG_KEY" "http://localhost:8000/api/v1/visualize?dataset_id=$ID" > /mnt/user/TheMachine/reports/graph.html
ls -l /mnt/user/TheMachine/reports/graph.html
```

Expected: an HTML file of tens of kB. Bernardo opens it from the share in a
browser and saves a screenshot as `deploy/unraid/docs/graph.png` (commit it on
`unraid-packaging`). If the file is tiny: the dataset is empty (run S4 first)
or the key is wrong.

### S10. Upgrade (spec test 12, when the next pinned commit exists)

Change the template tag to the new one, Apply, then repeat S1, S2 and S6.
Data must be intact: `config.toml`, `.api_key`, the notes and every database.

### S11. Optional: rerun the wiring tests on the server images

```bash
git clone -b unraid-packaging https://github.com/BGameiro2000/OpenJarvis /tmp/oj-tests
bash /tmp/oj-tests/deploy/unraid/tests/wiring.sh \
  ghcr.io/bgameiro2000/openjarvis-unraid:0.0.1.dev88-cf464de-u1 \
  ghcr.io/bgameiro2000/cognee-unraid:1.6.5-u1 /tmp/oj-wiring
rm -rf /tmp/oj-tests /tmp/oj-wiring
```

Uses its own network (`jarvis-wiring`) and throwaway folders; it does not touch
the real containers or data, but it uses the container name `cognee` and port
18000, so stop the real `cognee` container first and start it again after.
Expected: "ALL WIRING TESTS PASSED".

### S12. Machine HUD

Install `templates/machine-hud.xml` with `HUD_DEMO=1` first.

1. Open `http://<server>:8080`, log in, send "Should I get the A2000 now?".
   Expected: Root cuts in, Finch's last sentence shows struck through, the
   log shows INTERJ and TURN end.
2. `docker exec machine-hud id -u` prints 99;
   `docker exec machine-hud sh -c 'grep CapEff /proc/1/status'` shows all zeros;
   `docker exec machine-hud touch /vault/x` fails.
3. In the browser dev tools, search the page source and network responses
   for the OpenJarvis key. Expected: not found.
4. Set `HUD_DEMO=0`, Apply. Without the floor package the top bar shows
   FLOOR grey and mode PLAIN; a question gets THINKING, a TOOL line with a
   duration if a tool ran, SPEAKING, then LISTENING (real key, real cost).
5. Copy `home_jarvis` from branch `home-jarvis` to
   `/mnt/cache/appdata/openjarvis/custom/`, set
   `OPENJARVIS_CUSTOM_MODULES=home_jarvis` on openjarvis, restart it.
   Expected in its log: `[home_jarvis] floor ready`. The HUD shows FLOOR
   green and mode FLOOR; both cards change state during a turn.
6. `docker stop cognee`: the HUD keeps working, COGNEE turns red.
   `docker start cognee` after.
7. On the phone over Tailscale: the page fits the width, the feed is first.

### S13. HUD memory, intake and council (HUD 0.2.0)

Needs the latest `home_jarvis` in `/mnt/cache/appdata/openjarvis/custom/` and
OpenJarvis restarted. Expected in its log: `floor ready`, `intake ready`.

1. Memory: search a word you know is in a note. Expected: hits in under a
   second, tagged VAULT, GRAPH or BOTH (GRAPH needs `cognee-ingest` first). Pick
   a hit: the graph shows its neighbours; pick it again: same picture.
2. `docker stop cognee`, search again: hits still come (VAULT), the graph
   panel says Cognee is not reachable and shows the wikilink graph.
   `docker start cognee`.
3. Intake: drop a short note, a WhatsApp export (`.txt`) and a Claude export
   (`.zip`) on the board. Dry run: the chat and the AI export show
   `rule:chat` and `rule:ai_chat`; nothing moves in `/mnt/user/TheMachine/inbox`.
4. Apply. Expected: notes in `reference/chats/`, `reference/ai-chats/`, list
   lines appended, originals in `inbox/archive/YYYY-MM/`, lines in
   `inbox/.decision-log.jsonl`, FILE lines in the HUD log.
5. Drop a voice memo. Expected: an escalation "needs a transcript" (no speech
   backend); "Leave in inbox" keeps it there.
6. Council: run "Should I buy X now?". Expected: called gods with reasons,
   positions, a verdict, a new `council/YYYY-MM-DD-NNNN.md` in the vault.
   Cost: three model calls.
7. Main view: ask a "should I ... now" purchase question. Expected: COUNCIL
   lines in the log, the chips light up, Root may cut in with source
   `council:ate`.
