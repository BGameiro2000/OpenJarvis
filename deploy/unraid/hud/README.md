# Machine HUD

A screen for the OpenJarvis and Cognee containers, in its own container
(`machine-hud`, port 8080). Build guide: `02-machine-hud-build-guide.md`.
Findings H1 to H5 and the choices made: [docs/VERIFY.md](docs/VERIFY.md).

Built so far: guide steps 1 to 7 (shell, events, memory, intake board and
actions, personas, council). Voice (step 8) is not built. The reasoning for
memory, intake and council, and the API contracts with the custom package,
are in [docs/DESIGN.md](docs/DESIGN.md).

Four views, as tabs in the top bar:

- **Main**: personas, the floor, the feed, the decision log.
- **Memory**: hybrid search (vault text plus Cognee, merged, each hit tagged
  VAULT, GRAPH or BOTH, no model call, latency shown), filters, the two hop
  graph (Cytoscape, stable layout, shape by type), a timeline scrubber, and
  an inspector (current truth on top, dated history below, open in Obsidian,
  ask Finch). If Cognee is down the graph says why and falls back to the
  vault's wikilinks.
- **Intake**: capture (upload or quick note), then every inbox file through
  classify, validate and file, the dry run badge, routes today, escalation
  cards (file as proposed, add a list, edit the envelope, leave), the closed
  list menu and the decision log.
- **Council**: saved simulations replayed from `council/*.md`, the router
  with a reason for every god, round 1 panels (Ananke and Ate paired), round 2
  rebuttals, the verdict typed out with dissent and flags, scenario branches.

## What you see

- **Top bar**: owner, clock, cameras (always off for now), link, engine,
  and a dot per service (OpenJarvis, Cognee, floor).
- **Persona cards**: Finch and Root with their state (IDLE, LISTENING,
  THINKING, SPEAKING, INTERJECT), the last pressure against the threshold,
  turn time and cooldown. "Root, not now" mutes one for 30 minutes.
- **Floor switches**: the profile and each interaction mode (bid, interrupt,
  hard cut, build, add, correct, raise hand, aside, duet), switched live.
- **Primary feed**: the subject box (brackets in the colour of whoever holds
  the floor), then every line. An interjection shows what the speaker was
  about to say, struck through. An aside is marked "shown, not spoken". A
  raised hand gets "Go on" and "Not now" buttons.
- **Decision log**: one line per decision, newest first, capped at 2,000:
  TURN, BID, TOOL (with duration), INTERJ, BUILD, ADD, CORR, ASIDE, HAND,
  HELD (an act the floor blocked, with the reason), FLOOR, MUTE.

## Three modes, picked at runtime

| Mode | When | Who answers |
|---|---|---|
| `floor` | OpenJarvis has the `home_jarvis` package loaded (branch `home-jarvis`) | Finch and Root, through the floor manager |
| `plain` | the package is not loaded | Finch only, over `/v1/chat/completions` (streamed) |
| `demo` | `HUD_DEMO=1` | scripted Finch and Root, sorter and council, a bundled demo vault; no upstream, no key, no cost |

To get `floor` mode on the server: copy `home-jarvis/home_jarvis/` from the
`home-jarvis` branch to `/mnt/cache/appdata/openjarvis/custom/home_jarvis`,
set `OPENJARVIS_CUSTOM_MODULES=home_jarvis` on the OpenJarvis container and
restart it. Optional: `custom/floor.toml` (see `floor.example.toml` there).

## Install on Unraid

1. Add `templates/machine-hud.xml` the same way as the other two templates.
2. Set `OPENJARVIS_API_KEY` (the one OpenJarvis printed), `COGNEE_API_KEY`,
   and `HUD_PASSWORD`. Or set `HUD_AUTH=tailscale` and publish the port with
   `tailscale serve`.
3. Open `http://<server>:8080`. To try it first, set `HUD_DEMO=1`.

The container runs as UID 99, with no capabilities, a read-only root and the
vault mounted read-only. The keys stay in the container; the browser only
talks to `/api` on the same origin. The Content Security Policy allows no
inline script and no other origin.

## Develop

```
cd backend && python -m pytest -q          # backend, upstream mocked
cd frontend && npm ci && npm test && npm run build
HUD_DEMO=1 HUD_STATIC_DIR=../frontend/dist python -m machine_hud.app   # from backend/
python tests/ui_smoke.py http://127.0.0.1:8080 /tmp/shots   # Playwright
bash tests/container.sh machine-hud:test /tmp/hud           # built image
```
