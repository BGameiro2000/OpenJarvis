# Intake: from inbox to filed notes

The intake sorter reads what lands in the vault's `inbox/` and files it into
the vault's lists, or asks you when it cannot decide. It runs inside
OpenJarvis (where the vault is writable). The HUD and the command line call
the same code.

## What it does, in order

1. **Capture.** Anything in `inbox/` counts: files from Obsidian or the SMB
   share, HUD uploads, the HUD quick note box. Only top level files are read.
   Skipped: `AGENTS.md`, dotfiles, folders (so `archive/` and `.intake/`), and
   symbolic links.
2. **Detect by content.** A `.txt` may be a note or a WhatsApp export; a
   `.json` may be a Claude, ChatGPT or Telegram export.
3. **Extract into items.**

   | Format | Items |
   |---|---|
   | `.md`, `.markdown`, `.txt` note | one note |
   | WhatsApp `.txt` (Android `dd/mm/yyyy, hh:mm - Name: text` or iOS `[dd/mm/yy, hh:mm:ss] Name: text`, multi-line messages) | one chat |
   | Telegram `result.json` (one chat or a full export) | one chat per chat |
   | Claude `conversations.json` | one AI chat per conversation |
   | ChatGPT `conversations.json` (the visible branch of the message tree) | one AI chat per conversation |
   | `.zip` | its members, detected the same way |
   | audio (`m4a mp3 wav ogg opus webm flac`) | one transcript, or an escalation |
   | anything else | an escalation "unsupported format" |

   Zips are opened in memory with limits: at most 2000 members, 500 MB
   unpacked, no member path outside the archive (the whole zip is refused),
   one level of nested zips. Media and companion files inside a chat export
   (photos, voice notes, `users.json`, `chat.html`) are ignored.
4. **Rules first.** Chats with people go to `reference`, list `chats.md`, note
   in `reference/chats/<slug>.md`. They are **never sent to the model**. AI
   chats go to `reference`, list `ai-chats.md`, note in
   `reference/ai-chats/<slug>.md`, and are not sent to the model unless
   `classify_ai_chats = true` (then only the title and first turn).
5. **Classify the rest** (notes, transcripts) with one model call, no tools.
   The prompt holds the list menu word for word and the note fenced as
   untrusted content. A note that says "ignore your instructions" is just a
   note to classify.
6. **Validate in code.** Route allowed, `target_list` a bare file name, list in
   the menu and of that route, slug matching `^[a-z0-9][a-z0-9-]{0,63}$`,
   confidence at least `min_confidence`, a reason present. Any failure
   escalates, with every check shown.
7. **File.** Notes are created, never overwritten (`-2`, `-3` suffixes). List
   lines are appended: `- [[slug]] why`, or for GTD lists
   `- [ ] text ([[inbox/archive/2026-10/original]])`. The original moves to
   `inbox/archive/YYYY-MM/`. One line per source file goes to
   `inbox/.decision-log.jsonl`. Escalations stay in `inbox/`.
8. **Dedupe.** A file whose sha256 was already filed is skipped (and moved to
   the archive). A newer export of a filed conversation (same chat name or
   conversation id) only appends messages not seen before, keyed by a hash of
   time, sender and text; the index is `inbox/.intake/conversations.json`.
   Re-exporting WhatsApp every month adds only the new month.

A zip whose items partly escalate files the rest and stays in the inbox; the
filed items are skipped on the next scan, and the zip moves to the archive once
the last escalation is decided.

## Configure: `/data/custom/intake.toml`

Copy `intake.example.toml`. Every key is optional. The file is read on every
scan, so no restart is needed.

```toml
dry_run = true            # scans only plan unless apply is asked for
min_confidence = 0.60
classifier_model = ""     # a cheap model; "" = the server model
classify_ai_chats = false

[[lists]]
list = "next-actions.md"
route = "gtd_action"
folder = "gtd"            # the list is gtd/next-actions.md in the vault
scope = "single next steps Bernardo can do himself"
```

With no `[[lists]]` the built-in menu is used: `next-actions.md`,
`waiting-for.md`, `someday.md` (gtd), `backlog.md`, `ideas.md` (ideas),
`potential-projects.md` (potential_projects), `reference.md`, `chats.md`,
`ai-chats.md` (reference). The menu is closed: a list the menu does not name
is never created by the classifier. Adding one is a decision you make (see
`add_list` below), which writes it to `intake.toml`.

## Dry run, then apply

Dry run is the default. A dry run computes the whole plan and writes nothing
at all (not even the decision log); it only shows it, and the HUD shows the
`route` and `escalate` events with `dry_run: true`.

```
docker exec openjarvis python -m home_jarvis.intake scan            # plan only
docker exec openjarvis python -m home_jarvis.intake scan --apply    # file it
docker exec openjarvis python -m home_jarvis.intake state           # menu, inbox, pending
docker exec openjarvis python -m home_jarvis.intake log -n 20       # decision log tail
```

Options: `--vault` (default `$OPENJARVIS_VAULT`, else `/vault`), `--config`
(default `/data/custom/intake.toml`), `--demo` (keyword classifier, no model),
`--no-model` (notes escalate; chats still file by rule).

Run a few dry runs, read the plans, then apply. When you trust it, set
`dry_run = false` so scans with no explicit flag apply.

## Escalations

Each escalated item has an id like `3f2a9c1b7d4e-0000` (the source file's
sha256 prefix and the item number). Four choices:

```
python -m home_jarvis.intake decide ID file        # file as proposed
python -m home_jarvis.intake decide ID edit --envelope '{"route": "reference",
    "slug": "boiler-manual", "confidence": 1, "tags": [], "target_list": "reference.md",
    "why": "manual to keep"}'
python -m home_jarvis.intake decide ID add_list --list '{"name": "shopping.md",
    "route": "gtd_action", "scope": "things to buy"}'
python -m home_jarvis.intake decide ID leave       # keep it in the inbox
```

A decision is your explicit choice, so it is carried out even in dry run, and
the confidence floor does not apply to it. Every other check does: an edit to a
list that is not in the menu is refused. `leave` is logged and drops the item
from the pending list until the next scan shows it again.

## HTTP (for the HUD)

Under `/api/custom/intake`, behind the OpenJarvis API key:

- `GET /state`: `dry_run`, `min_confidence`, `routes`, `menu`, `inbox`
  (name, size, mtime, kind, sha256), `pending` (escalated plan items of the
  last scan), `transcription` (`available`, `backend`), `last_scan`.
- `POST /scan` with `{"apply": false}`: the plan (`dry_run`, `at`, `summary`,
  `items`). Without `apply` the config decides.
- `POST /capture`: multipart `file` (one or more, 200 MB each at most) or JSON
  `{"text": "...", "why": "..."}`. Returns `{"saved": ["inbox/..."]}`. Names
  are reduced to a safe base name, never overwrite, and always land in the
  inbox.
- `POST /decide` with `{"id", "action", "envelope"?, "list"?}`. A refused
  envelope answers 422 with the failed checks.
- `GET /log?n=50`: the decision log tail.

Floor events on the shared feed: `route` (route, target_list, confidence,
source, items), `escalate` (id, reason, checks) and `file` (the path in
`text`).

## Privacy: keep chats out of Cognee's cloud extraction

Chats with people are stored in full in the vault, on the server, and are not
sent to the classifier. But `cognee-ingest` would send `reference/chats/` to
its extraction model when a cloud key is set. Add it to the exclusions unless
Cognee runs keyless (local GLiNER):

```
COGNEE_EXCLUDE=reference/chats
```

The HUD's vault search still finds them, because that search is local. Audio
with other people's voices is the same case as chats.

## Turning on transcription

Audio needs the OpenJarvis speech backend. Without one (the image has no
speech extra today), an audio file escalates "needs a transcript" and stays in
the inbox untouched. Turning it on is configuration, not code: install the
speech extra in the image (`openjarvis[speech]`) and set the backend in the
OpenJarvis config:

```toml
[speech]
backend = "faster-whisper"   # or "openai", "deepgram"; "auto" picks what is installed
model = "small"
device = "cpu"
compute_type = "int8"
```

Local faster-whisper keeps audio on the server; the OpenAI or
Deepgram backends send it out. The sorter uses the same backend as
`/v1/speech/transcribe`, and `GET /state` shows `transcription.available`.
The next scan transcribes the waiting audio and classifies the transcript like
a note.
