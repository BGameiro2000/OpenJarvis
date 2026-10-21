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

## Demo and tests

`HOME_JARVIS_DEMO=1` swaps the model for a scripted brain with one scenario per
mode (GPU question for interrupt, paper for build, reminder for addendum,
deadline for correct, weather for comment, job offer for hand). No key, no
cost. The HUD uses it.

```
cd home-jarvis
python -m pytest -q
```
