# Home Jarvis: Finch and Root on one floor

This branch (`home-jarvis`) holds the two persona layer. It is never merged to
main. It runs inside the Unraid image from `deploy/unraid/` through the custom
hook: copy `home_jarvis/` to `/data/custom/home_jarvis`, set
`OPENJARVIS_CUSTOM_MODULES=home_jarvis`, and optionally put a
`/data/custom/floor.toml` next to it (see `floor.example.toml`).

## How the two talk to each other

One persona holds the floor and answers. The other listens, reads the reply
sentence by sentence, and proposes at most one act per turn. The floor manager
decides if it fires, and how.

| Mode | What happens | When it is spoken |
|---|---|---|
| `bidding` | Both personas score the question; the higher score answers. Naming one ("Root, ...") always wins. | Turn start |
| `interrupt` | The listener cuts in after sentence N. The rest of the reply is dropped and the speaker yields with one line. | Mid reply |
| `hard_cut` | Safety words (smoke, gas leak, rm -rf, ...) stop the speaker at once. Bypasses every limit. | Mid reply |
| `build` | The listener extends the answer; the speaker may build back, up to `max_exchanges`, until one says PASS. | After reply |
| `addendum` | One short caveat or reminder. | After reply |
| `correct` | A fact in the reply is wrong; the listener corrects it. Needs pressure 0.75 or more. | After reply |
| `hand` | The listener raises a hand and waits. `POST /grant` lets it speak. | On grant |
| `comment` | A side note shown in the HUD, never spoken. Also the catch-all when the proposed act is switched off. | Never |
| `council` | Before a decision question ("should I", an "or", a price), the council of gods is consulted: three model calls, saved to `council/`. The speaker gets the verdict; an Ananke or Ate flag becomes Root's pressure to cut in. | Before the reply |
| `duet` | One model call writes both voices as a short script. Cannot be combined with `interrupt`. | Whole turn |

Arbitration: a hard cut beats everything, then a named persona, then the
stronger act (cut, interject, hand, correct, build, add, comment), then
pressure. An act that is blocked does not vanish. It degrades along
interject, hand, comment, so you still see it.

Limits: one spoken act per turn, a cooldown between interjections, mute per
persona, fatigue (a persona you mute often gets a higher bar), a daily cap,
and hysteresis (the same source needs 0.10 more pressure next time). Every
decision, including blocked ones (`suppressed`), is a floor event.

## Routes (`/api/custom/floor`)

- `POST /chat` with `{"text": ...}`: runs a turn, streams its events as SSE.
- `GET /events`: live SSE feed of every floor event, with replay.
- `GET /state`, `POST /config` (profile or modes), `POST /mute`, `POST /unmute`,
  `POST /grant`, `POST /feedback`.

## Intake (`/api/custom/intake`)

`home_jarvis.intake` sorts what lands in the vault's `inbox/`: notes, WhatsApp
and Telegram exports, Claude and ChatGPT exports, zips, audio. Chats are filed
by rule (chats with people are never sent to a model); notes and transcripts
go to a cheap classifier with a closed list menu; every answer is checked in
code, and anything that fails stays in the inbox as an escalation. Dry run is
the default. Config: `/data/custom/intake.toml` (see `intake.example.toml`).

```
docker exec openjarvis python -m home_jarvis.intake scan            # plan only
docker exec openjarvis python -m home_jarvis.intake scan --apply    # file it
```

Routes: `GET /state`, `POST /scan`, `POST /capture`, `POST /decide`,
`GET /log`. The vault is `$OPENJARVIS_VAULT` (default `/vault`). Full guide,
including the Cognee exclusion for `reference/chats` and how to turn on
transcription: `docs/INTAKE.md`.

## Demo and tests

`HOME_JARVIS_DEMO=1` swaps the model for a scripted brain with one scenario per
mode (GPU question for interrupt, paper for build, reminder for addendum,
deadline for correct, weather for comment, job offer for hand). No key, no
cost. The HUD uses it. It (or `HUD=1`) also gives the intake sorter a keyword
classifier instead of the model.

```
cd home-jarvis
python -m pytest -q
```

## Council

`home_jarvis/council.py`. Gods: Dionysus (life), Prometheus (academic),
Daedalus (projects), Ananke and Ate (neurodivergence, a pair), Tyche
(shopping), Hermes (career), Orpheus (creativity). Not user facing.

- **Router** in code: at most three gods by keywords, plus Ananke and Ate on a
  purchase with "now" or "tonight" or before 06:00. Every god gets a reason,
  called or not.
- **Three model calls** on the review model: round 1 positions, round 2
  rebuttals, the verdict (decision, confidence, dissent, flags, branches).
  Model output is cleaned: unknown gods dropped, stances and numbers clamped.
- **Saved** to `council/YYYY-MM-DD-NNNN.md` (never overwritten) with the full
  record in a `json council` block; the HUD replays it.
- **Routes**: `POST /api/custom/council/run` (SSE), `GET /api/custom/council/sims`,
  `GET /api/custom/council/sims/{sim}`.
- **Cost**: three extra calls per consulted turn, only on decision questions
  and only with the `council` mode on (profiles `home_text` and `focus`).

## Personas: SOUL files

`vault/personas/finch/SOUL.md` and `vault/personas/root/SOUL.md` are modelled
on Harold Finch and Root from Person of Interest, as they were by seasons 4
and 5. The research behind them (arcs season by season, what each SOUL keeps
and leaves out, sources) is in `docs/PERSONAS-POI.md`. Copy them to
`/mnt/user/TheMachine/personas/<name>/SOUL.md`; the floor reads the first
4,000 characters of each.
