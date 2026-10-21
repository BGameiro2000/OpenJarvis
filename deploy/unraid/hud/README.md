# Machine HUD

A screen for the OpenJarvis and Cognee containers, in its own container
(`machine-hud`, port 8080). Build guide: `02-machine-hud-build-guide.md`.
Findings H1 to H5 and the choices made: [docs/VERIFY.md](docs/VERIFY.md).

Built so far: guide steps 1 (shell) and 2 (events), plus the persona view of
step 6 when the floor package is loaded. Memory, intake, council and voice
come later; their places on the screen say so.

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
| `demo` | `HUD_DEMO=1` | scripted Finch and Root, no upstream, no key, no cost |

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
