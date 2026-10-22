# Memory, intake and council: design and contracts

This is the reasoning behind HUD build steps 3 to 5 and 7, written before the
code. Voice (push to talk, TTS) is out of scope here. Audio files dropped in
the inbox are in scope: that is intake, not the voice interface.

Where the code lives:

- **HUD** (`deploy/unraid/hud/`, this branch): the screens and a backend that
  reads the vault read-only, asks Cognee, and relays every write to OpenJarvis.
- **Sorter and council** (`home-jarvis/home_jarvis/intake/`, `council.py`,
  branch `home-jarvis`): custom code loaded in OpenJarvis from `/data/custom`,
  per spec sections 12 and 13. They run where the vault is writable.

The HUD never writes to the vault. Every write (upload, scan with apply,
escalation decision, council run) is a call to the sorter or the council in
OpenJarvis, the same code `docker exec openjarvis python -m home_jarvis.intake`
runs by hand.

## 1. Intake: the debate

### What comes in

| Kind | Typical shape | Size | Whose words |
|---|---|---|---|
| Quick note | `.md` or `.txt` from Obsidian, the phone, the HUD box | bytes to kB | Bernardo's |
| Long note, document | `.md`, `.txt`, sometimes `.pdf` | kB to MB | Bernardo's or third party |
| Chat with people | WhatsApp `.txt` export (Android and iOS formats), Telegram `result.json`, often inside a `.zip` with media | kB to 100 MB, years of messages | **other people's too** |
| Chat with an AI | Claude export (`conversations.json`, in a `.zip`), ChatGPT export (`conversations.json` with a message tree) | MB, hundreds of conversations | Bernardo's and the model's |
| Voice memo | `.m4a`, `.mp3`, `.wav`, `.ogg`, `.opus`, `.webm`, `.flac` | MB | Bernardo's, sometimes others in the room |
| Anything else | `.json` of unknown shape, images, `.docx` | any | unknown |

### Options considered

**A. One LLM classifier over every file, as is.** Simple, and the spec's
classifier envelope covers it. Rejected: a Claude export is hundreds of
conversations in one file, a WhatsApp export is years of messages. One
envelope per file is meaningless, the prompt would not fit, the cost is
unbounded, and it would send other people's messages to a cloud model for no
reason.

**B. Convert every file to markdown, then classify each file.** Better: the
classifier always sees text. Still wrong for bulk exports (one file holds many
unrelated things) and for audio (it needs a transcript first).

**C. Capture, then extract into items, then route by rules before any model,
then classify only what rules cannot decide, then validate, then file.**
Chosen. Each stage does one thing and the expensive, risky one (the model)
sees the least.

### The pipeline (option C)

1. **Capture.** Every source ends up as a file in `inbox/`: Obsidian or the SMB
   share, the HUD upload and quick note box (through the sorter's capture
   route), later Telegram. Capture never interprets anything and never changes
   the original.
2. **Detect by content, not extension.** A `.txt` may be a note or a WhatsApp
   export; a `.json` may be a Claude export, a ChatGPT export, a Telegram
   export, or unknown. A `.zip` is opened in memory and its members are
   detected the same way, with limits (member count, total unpacked size, no
   paths outside the archive, no nested zips beyond one level).
3. **Extract into items.** One extractor per format turns a file into one or
   more normalised items: markdown text plus metadata (`kind`, `title`,
   `participants`, `started`, `ended`, `source`, `source_key`, `sha256`).
   - note: one item.
   - AI export: one item per conversation (`source_key` is the conversation
     id), messages as `**Bernardo**` / `**Claude**` turns with timestamps.
   - chat with people: one item per chat (`source_key` is platform plus chat
     name), messages grouped by day.
   - audio: one item whose text is the transcript. Transcription uses the
     OpenJarvis speech backend (`/v1/speech/transcribe` logic, in process). If
     none is configured (the case today: the image has no speech extra), the
     item is an escalation "needs a transcript" and the audio stays in the
     inbox untouched. Turning it on later is a config change, not a code change
     (local faster-whisper keeps audio on the server; OpenAI or Deepgram send it
     out).
   - unknown: one item that escalates "unsupported format", original kept.
4. **Route by rules first.** Deterministic routes for everything whose
   destination follows from its kind:
   - chat with people goes to `reference`, list `chats.md`, note in
     `reference/chats/`. **Never sent to the classifier model.** Other people
     did not agree to have their messages sent to a cloud model.
   - AI conversation goes to `reference`, list `ai-chats.md`, note in
     `reference/ai-chats/`. Not sent to the model by default either (cost: an
     export can be 500 conversations). Option `classify_ai_chats = true`
     classifies each conversation's title and first turn only.
   - audio without transcript escalates.
5. **Classify the rest** (notes, transcripts) with the spec's classifier: a
   model call with no tools, cheap model, the closed list menu word for word,
   and the note fenced as untrusted content. It returns the envelope
   `{route, slug, confidence, tags, target_list, why}`.
6. **Validate in code.** Route in the five, `target_list` a bare filename that
   exists in the menu for that route, slug safe (`^[a-z0-9][a-z0-9-]{0,63}$`),
   confidence at least 0.60, `why` present. Any failure escalates, with the
   failed checks shown. A missing list means escalate, never create.
7. **File.** New notes are created, never overwritten (a `-2` suffix if the
   slug exists); list lines are appended; the original moves to
   `inbox/archive/YYYY-MM/`; one line per source goes to
   `inbox/.decision-log.jsonl`. Escalations stay in `inbox/`.
8. **Dedupe.** The same file twice (same sha256) is skipped. A newer export of
   a conversation that is already filed (same `source_key`) appends only the
   messages not seen before, keyed by a hash of time, sender and text. So
   re-exporting WhatsApp every month adds only the new month.

Dry run is the default (`dry_run = true` in `/data/custom/intake.toml`): the
plan is computed and logged with `"dry_run": true`, nothing moves. Apply is
an explicit flag, a button in the HUD, or the schedule once trusted.

### Privacy consequences, stated plainly

- Chats with people are stored in the vault in full, on the server. They are
  not sent to the classifier.
- **Cognee:** `cognee-ingest` would send `reference/chats/` to the extraction
  model when a cloud key is set. Add `reference/chats` to `COGNEE_EXCLUDE`
  unless Cognee runs keyless (local GLiNER). The HUD's vault search still finds
  them, because that search is local.
- Audio with other voices is the same case as chats with people.

### What the HUD shows (guide section 7)

Four columns: capture (inbox files with detected kind), classify (the envelope
as is, or the rule that decided), validate (each check), file (paths to be
written). A dry run badge. Routes today. Escalation cards with four choices:
file as proposed, add a list, edit the envelope, leave in inbox. The closed
list menu with its scopes. The decision log tail.

## 2. Memory search and graph

- **Vault search** in the HUD backend: an in-memory SQLite FTS5 index of the
  vault's `.md` and `.txt` files, built at start and refreshed by modification
  time every minute. Read-only mount, nothing on disk. BM25 ranking, snippets,
  frontmatter `type` and dates. No model call.
- **Graph search**: Cognee `POST /api/v1/search` with `search_type: CHUNKS`
  (vector search, no model call). Chunks carry `document_name`, which
  `cognee-ingest` sets to the vault path with `/` replaced by `__`, so a chunk
  maps back to its note.
- **Hybrid**: both in parallel, merged with reciprocal rank fusion (k = 60),
  keyed by vault path. Each hit is tagged VAULT, GRAPH or BOTH. Latency is
  shown per source.
- OpenJarvis `/v1/memory/search` (H2) is not used: its index is the agent's
  memory backend, not the vault.
- **Graph**: Cognee's dataset graph JSON (H3), cached two minutes, cut to the
  two hop neighbourhood of the selected note. Chunk nodes are folded into
  "mentions" edges so the picture shows notes and entities, not chunks.
  Breadth-first layout from the selected node: deterministic, so it does not
  jump between searches.
- **Cognee down**: the graph panel says why, and falls back to the `[[wikilink]]`
  graph from the vault index, labelled as such. Vault search keeps working.

## 3. Council

- Gods: Dionysus (life), Prometheus (academic), Daedalus (inventions and
  projects), Ananke and Ate (neurodivergence, one paired panel), Tyche
  (shopping), Hermes (career), Orpheus (creativity). Not user facing.
- **Router** in code (keywords, money, the hour), at most three gods, plus
  Ananke and Ate when a purchase, "now" or "tonight", or the night hours are
  involved. Every god gets a reason, called or not, so routing is auditable.
- **Rounds**: one model call for all round 1 positions (JSON), one for round 2
  rebuttals, one for the verdict with confidence, dissent and optional
  branches. Three calls, not one per god per round.
- **Floor link**: on a decision question ("should I", "or", a price) with the
  `council` mode on, the floor consults before Finch answers. Finch gets the
  verdict as context; an Ananke or Ate flag becomes Root's interjection
  pressure (`source: council:ate`).
- Every simulation is saved to `council/YYYY-MM-DD-NNNN.md`: frontmatter,
  readable text, and the full record in a fenced `json council` block that the
  HUD replays.

## 4. Contracts

Floor package routes, behind the OpenJarvis API key. The HUD backend adds the
key and relays them under `/api/intake/*` and `/api/council/*`.

### Intake (`/api/custom/intake`)

`GET /state`:

```json
{"dry_run": true, "min_confidence": 0.6,
 "routes": ["project_shaped", "gtd_action", "idea_capture", "reference", "escalate"],
 "menu": [{"list": "next-actions.md", "route": "gtd_action", "folder": "gtd",
           "scope": "single next steps Bernardo can do himself"}],
 "inbox": [{"name": "voice-0412.m4a", "size": 182044, "mtime": 1792620000.0,
            "kind": "audio", "sha256": "..."}],
 "pending": ["<plan item, see below>"],
 "transcription": {"available": false, "backend": ""},
 "last_scan": {"at": 1792620000.0, "dry_run": true, "summary": {"reference": 2}}}
```

`POST /scan` with `{"apply": false}` returns a plan:

```json
{"dry_run": true, "at": 1792620000.0,
 "summary": {"gtd_action": 1, "reference": 3, "escalate": 1},
 "items": [{"id": "3f2a9c1b7d4e-0000", "source": "claude-export.zip", "kind": "ai_chat",
            "title": "GPU vs RAM", "count": 1, "preview": "first 400 chars",
            "decided_by": "rule:ai_chat",
            "envelope": {"route": "reference", "slug": "gpu-vs-ram", "confidence": 1.0,
                         "tags": ["ai-chat"], "target_list": "ai-chats.md", "why": "..."},
            "checks": [{"name": "route allowed", "ok": true, "detail": ""}],
            "decision": "file",
            "actions": [{"op": "write", "path": "reference/ai-chats/gpu-vs-ram.md"},
                        {"op": "append", "path": "reference/ai-chats.md"},
                        {"op": "move", "path": "inbox/archive/2026-10/claude-export.zip"}],
            "reason": ""}]}
```

`decision` is `file`, `escalate`, `merge` (new messages into a filed
conversation) or `skip` (already filed). `POST /capture`: multipart `file`
(one or more), or JSON `{"text": "...", "why": "..."}`; returns
`{"saved": ["inbox/..."]}`. `POST /decide` with
`{"id": "...", "action": "file|add_list|edit|leave", "envelope": {...}, "list": {"name": "x.md", "route": "...", "scope": "..."}}`.

Decision log line (`inbox/.decision-log.jsonl`), one per source file:

```json
{"ts": "2026-10-22T09:14:03", "id": "3f2a9c1b7d4e", "source": "claude-export.zip",
 "sha256": "...", "kind": "ai_chat", "items": 212, "decision": "filed",
 "route": "reference", "target_list": "ai-chats.md", "confidence": 1.0,
 "decided_by": "rule:ai_chat", "dry_run": false, "by": "hud",
 "paths": ["reference/ai-chats/gpu-vs-ram.md"], "why": "..."}
```

Floor events: `route`, `escalate`, `file` (log codes ROUTE, ESCAL, FILE).

### Council (`/api/custom/council`)

`POST /run` with `{"question": "..."}` streams floor events as SSE:
`consult` (router result), `position` (one per god per round), `verdict`,
then `council_saved` with `{"sim", "path"}`. `GET /sims` lists saved
simulations; `GET /sims/{sim}` returns one record:

```json
{"sim": "2026-10-22-0001", "question": "...", "created": "2026-10-22T14:31:47",
 "router": [{"god": "tyche", "called": true, "score": 0.8, "reason": "price in question"}],
 "rounds": [[{"round": 1, "god": "tyche", "stance": "for", "confidence": 0.62,
              "argument": "...", "sources": ["shopping/watch.md"]}], []],
 "verdict": {"decision": "WAIT", "confidence": 0.78, "summary": "...",
             "dissent": [{"god": "tyche", "argument": "..."}],
             "flags": [{"god": "ate", "text": "...", "pressure": 0.74}]},
 "branches": [{"name": "Buy the A2000 now", "score": 0.31, "note": "..."}]}
```

Stances: `for`, `against`, `wait`, `flag`. The HUD reads saved simulations
straight from `/vault/council/*.md` too, so replays work without OpenJarvis.
