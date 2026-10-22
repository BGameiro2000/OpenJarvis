# Machine HUD: verify before building (H1 to H5)

Checked against the pinned upstream commit `cf464de1ff13d84dede80eb07e5e45c1d06708ba`
and the Cognee 1.6.5 wheel from PyPI. Build guide: `02-machine-hud-build-guide.md`, section 13.

## H1. `ws_bridge` and `stream_bridge` message format, and the event bus

- `ws_bridge.py` serves `WS /v1/agents/events` (auth: the same API key, as a
  Bearer header or a WebSocket subprotocol). Each message is one JSON object:
  `{"type": "<event_type value>", "timestamp": <float>, "data": {...}}`.
  Optional `?agent_id=` filter.
- It forwards a fixed list only: agent tick start, end, error, budget, stall,
  message received, checkpoint saved, `tool_call_start`, `tool_call_end`,
  `inference_start`, `inference_end`. **Not** `agent_turn_start` or
  `agent_turn_end`, and not custom events.
- `stream_bridge.py` is the streamed `/v1/chat/completions` path when an agent
  runs. Before and between OpenAI style `data:` chunks it sends named SSE
  events: `event: agent_turn_start`, `inference_start`, `inference_end`,
  `tool_call_start` (arguments as JSON text), `tool_call_end` (`tool`,
  `success`, `latency` in seconds, `result`), then the answer as chunks and
  `data: [DONE]`. The turn end has no named event; `[DONE]` marks it.
- So the bus is exposed only in part, and only per request (stream) or for
  agent ticks (WebSocket).

**Decision.** The HUD gets turn events from the chat stream it starts itself
(TURN, TOOL with duration, THINKING then SPEAKING on the first token,
LISTENING on `[DONE]`). Persona events come from the floor package's own SSE
feed, `GET /api/custom/floor/events` (see H5). The WebSocket relay is left for
later: it adds agent ticks only, which the HUD does not show yet.

## H2. Memory search over HTTP

Yes. `POST /v1/memory/search` with `{"query": ..., "top_k": N}` returns
`{"results": [{"content", "score", "metadata"}]}`. It calls the memory
backend's `retrieve`, no LLM call. If memory is not configured it returns an
empty list (not an error); `GET /v1/memory/stats` tells the two apart. Both are
under `/v1`, so they need the API key; the HUD backend adds it.

## H3. Cognee subgraph as JSON

Partly. `GET /api/v1/datasets/{dataset_id}/graph` returns JSON
`{"nodes": [{"id", "label", "type", "properties"}], "edges": [{"source", "target", "label"}]}`
for a whole dataset (`X-Api-Key` header). There is no subgraph route. The HUD
backend will fetch the dataset graph, cache it briefly, and cut the two hop
neighbourhood around a node itself. Dataset ids come from `GET /api/v1/datasets`.
`GET /api/v1/datasets/graph-summary` gives node and edge counts for the top bar.

## H4. How `approval_routes` represent a pending item

`GET /v1/approvals/pending` returns `{"actions": [...], "count": N}`; each
action is `{"id", "action_type", "description", "payload", "permission_key",
"tier", "status", "created_at", "expires_at"}`. `POST /v1/approvals/{id}/approve`
and `/deny` change the status. The store is a SQLite file inside OpenJarvis.

**Decision.** Escalations from intake are not written into that store (the HUD
never writes, and the sorter owns intake). The HUD shows both in one queue by
mapping an escalation to the same shape (`action_type: "intake_escalation"`,
`payload`: the classifier envelope). Step 5.

## H5. Custom events on the upstream bus from `/data/custom`

Yes, through `openjarvis_unraid_hooks.publish_custom(bus, "persona.interject", {...})`
(see `deploy/unraid/docs/VERIFY.md`). But the upstream bridges subscribe to a
fixed list of event types, so custom events never reach `ws_bridge` or
`stream_bridge`. The floor package therefore also serves its own SSE feed at
`/api/custom/floor/events` (mounted with `add_routes`, behind the same API key),
and the HUD backend relays that feed.

## Assumptions recorded while building

- **Svelte, not React.** The HUD is one dense screen driven by a stream of
  small events. Svelte compiles to direct DOM updates with no runtime diffing,
  the bundle is a fraction of React's, and stores make "event in, card state
  out" a few lines. No component library is needed either way.
- **Fonts** come from the `@fontsource` packages (OFL) and are bundled by Vite
  into the image. No request leaves the HUD at runtime.
- **Three chat modes**, picked at runtime: `floor` when
  `/api/custom/floor/state` answers (the persona package from the
  `home-jarvis` branch is loaded), `plain` when it does not (one persona,
  Finch, over `/v1/chat/completions`), and `demo` when `HUD_DEMO=1` (scripted
  events, no upstream, no key). The HUD reads the same event shape in all three.
- **Log codes.** The guide's codes plus the floor's acts, six characters max:
  `BID`, `BUILD`, `ADD`, `CORR`, `ASIDE`, `HAND`, `MUTE`, `FLOOR`, `HELD`
  (an act the floor blocked, shown for tuning).
- **Auth.** `HUD_AUTH=basic` (with `HUD_USER`, `HUD_PASSWORD`), `tailscale`
  (requires the `Tailscale-User-Login` header that `tailscale serve` adds),
  or `off` (LAN only, a warning in the log). Default: `basic` if
  `HUD_PASSWORD` is set, else `off`.
- **CSP.** `default-src 'self'`, no inline scripts, no third party origins.
  Inline style attributes are not used; dynamic widths go through the CSSOM,
  which CSP does not block.
- **Logs** are capped at 2,000 lines and rendered with a small fixed row
  height window (virtualised list), no library.
- **Base images** are pinned by tag and digest in `versions.env`
  (`HUD_NODE_IMAGE`, `HUD_PYTHON_IMAGE`). The digests are the ones the first
  CI build resolved (run 39683803814).

## Memory, intake and council (steps 3 to 7)

- **Search speed**: the vault index is in memory (SQLite FTS5); a search on
  the demo vault takes a few milliseconds, and Cognee's CHUNKS search adds its
  own time, shown separately. Neither calls a model.
- **Cytoscape and the CSP**: Cytoscape injects one inline `<style>` unless an
  element with id `__________cytoscape_stylesheet` exists. `index.html` links
  `cytoscape.css` with that id, so nothing inline is needed and the CSP stays
  without `unsafe-inline`. Cytoscape is loaded only by the Memory view.
- **Writes**: the HUD's intake and council buttons call the custom package in
  OpenJarvis (`/api/custom/intake/*`, `/api/custom/council/*`). Without it
  the board still shows the inbox and the decision log from the vault, and
  replays still work; the buttons say the sorter or council is not loaded.
- **"Console view"** in the request was read as the council view (guide
  step 7).

## CI result

Run 39683803814, job `hud`: backend tests (10), frontend tests (5), image
build, container smoke (UID 99, CapEff 0, read-only root and vault, login
required, CSP, demo turn with Root cutting in, no key in any response or
static file), and the browser smoke in Chromium (no CSP violation or page
error, interjection with the unsaid sentence, HTML in text stays text,
reduced motion, phone width). Screenshots are in the run's `hud-screenshots`
artifact.
