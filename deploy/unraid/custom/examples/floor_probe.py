"""Probe for the custom code hook. Not a floor manager; a test fixture.

Proves the doors the personas phase needs are open, without building it:

- a plugin registry for interruption strategies, built on upstream's
  ``RegistryBase`` exactly like tools and agents;
- ``/data/custom/floor.toml`` read at startup;
- the live server event bus: subscribe to upstream events, publish custom ones;
- an authenticated route (``/api/custom/floor/status``) to inspect it all.

Try it: copy this file to /data/custom/, set
OPENJARVIS_CUSTOM_MODULES=floor_probe, restart, then
``curl -H "Authorization: Bearer $KEY" http://SERVER:8000/api/custom/floor/status``.
"""

from __future__ import annotations

import time
from typing import Any

from openjarvis_unraid_hooks import (
    add_routes,
    load_floor_config,
    on_server_ready,
    publish_custom,
    subscribe_custom,
)

from openjarvis.core.events import EventType
from openjarvis.core.registry import RegistryBase


class StrategyRegistry(RegistryBase[Any]):
    """Registry of interruption strategy plugins (isolated storage)."""


@StrategyRegistry.register("s6_aside")
class SilentAside:
    name = "s6_aside"
    phase = "during_turn"

    def evaluate(self, ctx: Any) -> None:
        return None


STATE: dict[str, Any] = {
    "loaded_at": time.time(),
    "strategies": [],
    "floor_config": None,
    "upstream_events": {},
    "custom_events": [],
}


@on_server_ready
def _start(app: Any) -> None:
    from fastapi import APIRouter

    bus = app.state.bus
    STATE["strategies"] = sorted(StrategyRegistry.keys())
    STATE["floor_config"] = load_floor_config()
    STATE["engine"] = type(app.state.engine).__name__
    STATE["engine_has_stream"] = hasattr(app.state.engine, "stream")

    def _count(event: Any) -> None:
        key = event.event_type.value
        STATE["upstream_events"][key] = STATE["upstream_events"].get(key, 0) + 1

    for et in (
        EventType.AGENT_TURN_START,
        EventType.AGENT_TURN_END,
        EventType.INFERENCE_START,
        EventType.INFERENCE_END,
        EventType.TOOL_CALL_START,
        EventType.TOOL_CALL_END,
    ):
        bus.subscribe(et, _count)

    subscribe_custom(
        bus,
        "floor.config_changed",
        lambda e: STATE["custom_events"].append({"type": str(e.event_type), **e.data}),
    )
    publish_custom(bus, "floor.config_changed", {"who": "probe", "how": "startup"})

    router = APIRouter()

    @router.get("/api/custom/floor/status")
    def status() -> dict:
        return STATE

    @router.post("/api/custom/floor/publish")
    def publish(name: str = "persona.aside") -> dict:
        publish_custom(bus, "floor.config_changed", {"who": "api", "how": name})
        return {"published": name, "seen": len(STATE["custom_events"])}

    add_routes(app, router)
