# Two personas, one floor: implementation plan

Status: design only. Nothing here is built yet. Written 2026-10-21 against
OpenJarvis `cf464de` and the findings V17 and V18 in `VERIFY.md`, as input for
the personas phase of `04-interruption-strategies.md` and steps 2 and 6 of the
HUD guide (`02-machine-hud-build-guide.md`).

## 1. What the code allows (short version)

- Custom code in `/data/custom` runs inside the OpenJarvis process and, through
  `openjarvis_unraid_hooks.on_server_ready`, gets the server's event bus,
  engine, agent and config. It can publish its own events and mount its own
  authenticated routes. Proven with `custom/examples/floor_probe.py`.
- Engines stream tokens (`engine.stream`). The server's tool-using agent does
  not: it finishes the turn, then replays the text.
- TTS takes whole strings. A sentence queue is ours to write.
- Upstream's `persona_name` loads `personas/<name>/SOUL.md` **and** its own
  `MEMORY.md` and `USER.md`. That contradicts "both personas read the same
  memory". Shared memory needs explicit paths instead (section 3).

## 2. Where it lives

One module set in `/data/custom/home_jarvis/` (Bernardo's separate repository,
mounted there), loaded with `OPENJARVIS_CUSTOM_MODULES=home_jarvis`:

| Piece | Job |
|---|---|
| `floor.py` | Floor manager. Owns who speaks. Pure arbitration logic plus a small state store (cooldown, mute, fatigue, daily cap). |
| `strategies/` | One module per strategy (S1 to S8), each registered in `StrategyRegistry(RegistryBase)` with `name`, `phase`, `evaluate(ctx) -> Proposal or None`. |
| `personas.py` | Finch and Root: SOUL path, voice id, tool set, domain weights. |
| `speech.py` | Sentence splitter and TTS queue (pause, drop, resume). |
| `routes.py` | `POST /api/custom/floor/chat` (SSE), `GET /api/custom/floor/events` (SSE), `POST /api/custom/floor/config`. |
| `floor.toml` | Profiles, thresholds, weights (the format in `04` section 6.3). |

Why inside OpenJarvis and not in the HUD container: the floor manager needs
the engine, the agents and the event bus, which only exist in that process,
and the HUD guide says the HUD only renders. The HUD backend relays
`/api/custom/floor/*` and keeps the key server side.

## 3. Personas as two agents over shared memory

- Build two agent instances at startup (in the `on_server_ready` callback),
  both from `AgentRegistry` (`orchestrator`), same engine, each with its own
  `SystemPromptBuilder` and `MemoryFilesConfig`:
  - `soul_path = /vault/personas/<name>/SOUL.md` (differs)
  - `memory_path = /vault/MEMORY.md`, `user_path = /vault/USER.md` (shared)
- Tool sets differ by persona (Root gets spending and routine tools first;
  Finch research and planning), but both always get `cognee_recall` and
  `memory_manage`.
- The server's own agent (`app.state.agent`) stays untouched, so the stock web
  UI and `/v1/chat/completions` keep working as today.

## 4. A turn, step by step

1. `pre_turn`: S7 picks the first speaker. Rules first (domain keywords,
   "Root, ..." naming the persona); a small-model bid only when rules tie.
   Emits `floor.bid`.
2. The speaker's agent runs the turn (tools included). Text arrives whole at
   the end (V18), which is fine for arbitration: the floor manager has every
   sentence before any audio.
3. `during_turn`: the text is split into sentences. Before each sentence is
   released to TTS (voice) or to the HUD (text), the enabled `during_turn`
   strategies evaluate it with the listener's view: pressure signals from
   rules (amounts, keywords, Ananke/Ate flags, a wiki "current truth"
   contradiction via `cognee_recall` context). The floor manager arbitrates
   (override order, strength, pressure, ties; degrade S3 to S5 to S6 when
   limits block). Emits `persona.interject`, `persona.hand_raised`,
   `persona.aside` or `floor.suppressed`.
4. On an interjection: the remaining sentences of the speaker are dropped or
   summarised, the listener's agent gets the context plus the trigger and
   produces one short reply, the speaker may yield one line. Emits
   `persona.yield`.
5. `post_turn`: S2 addendum and S8 correction run on the final text.
6. Every step publishes one event with `publish_custom`; the floor's SSE route
   streams them to the HUD in the shape of the HUD guide section 4.

Live tokens (for lower voice latency) are a later optimisation: let the agent
decide tools, then generate the final answer with `engine.stream()` under the
same persona prompt, and feed sentences to the queue as they complete.

## 5. Switching and combining at runtime

- Active strategy set = profile from `floor.toml` (chosen by context: time,
  client, focus toggle) plus live overrides (voice, HUD toggles, Telegram).
- A switch publishes `floor.config_changed` with who and how; the floor
  manager rebuilds its enabled list between turns, never mid-sentence.
- Incompatible pairs (S1 with S3 in the same turn) are rejected when the
  config is loaded, as `04` requires.
- Determinism for testing: the floor manager takes events and text in and
  returns decisions; no clock or randomness inside except through an injected
  clock. Recorded conversations replay offline with the same results.

## 6. Self-critique: what could go wrong

- **Cost doubles quietly.** Two agents plus a listener check per sentence can
  triple calls per turn. Rules first, one interjection per turn, and S2 or S1
  as the default for text keep it bounded. Measure cost per turn from day one
  (the HUD top bar has the slot).
- **Latency.** Agent turns with tools take seconds before the first sentence
  exists; S3 then only cuts sentences that were never spoken anyway. For voice
  that is the honest trade-off until live streaming (section 4, last
  paragraph) is built.
- **"First token means SPEAKING" (HUD guide section 4) does not hold** for
  agent turns: the first token arrives after the whole turn. The HUD should
  switch to SPEAKING on the floor manager's first released sentence instead.
- **One process.** A slow subscriber blocks the publisher (the bus calls
  subscribers synchronously). Subscribers must only enqueue; work runs in a
  worker thread or the event loop.
- **Upstream drift.** The hooks rely on `create_app`, `app.state.bus`,
  `EventType` names and `SystemPromptBuilder`. A fork sync can break them;
  the CI wiring tests should grow a floor-manager smoke test when it exists.
- **Is a second persona wanted at all?** `04` recommends starting with S7 + S2
  in text and S1 as an experiment. Agreed: build the arbitration and events
  first, measure acceptance (target over 60% accepted), and only then invest
  in sentence-level TTS and S3.

## 7. Build order (matches `04` section 9)

1. Floor manager, strategy registry, `floor.toml`, events, S7 rules + S2, text only.
2. S1 duet as an option, compared with S2 on real conversations.
3. Sentence splitter and TTS queue (needs a TTS backend: the cloned voice, or
   the `voice` extra for Kokoro, which this image does not include yet).
4. S3 with rules-only pressure, then a small-model contradiction check.
5. S5, S6, degradation, mute, fatigue, daily cap.
6. Live switches. 7. S4 once Home Assistant is connected.
