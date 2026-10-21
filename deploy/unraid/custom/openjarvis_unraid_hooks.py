"""Startup hooks for custom code in /data/custom (no upstream code is changed).

Custom modules are imported by the Unraid launcher before ``jarvis serve``
builds anything (see ``OPENJARVIS_CUSTOM_MODULES``). At import time the server
does not exist yet, so a module registers a callback here and gets the live
FastAPI app once ``create_app`` has run:

    from openjarvis_unraid_hooks import on_server_ready, load_floor_config

    @on_server_ready
    def start(app):
        bus = app.state.bus          # the server's EventBus
        engine = app.state.engine    # the inference engine (has .stream())
        config = load_floor_config() # /data/custom/floor.toml as a dict, or None
        ...

Also provided:

- ``publish_custom(bus, "persona.interject", {...})``: publish an event whose
  name is not in upstream's ``EventType`` enum, on the same bus.
- ``subscribe_custom(bus, "persona.interject", callback)``.
- ``add_routes(app, router)``: mount a FastAPI router so it wins over the web
  UI catch-all route. Paths under ``/api/`` or ``/v1/`` are behind the API key.

Planned use (later phase, not built here): a floor manager and a registry of
interruption strategy plugins, configured by ``/data/custom/floor.toml``.
"""

from __future__ import annotations

import logging
import os
import sys
import threading
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger("openjarvis_unraid_hooks")

_ready_callbacks: list[Callable[[Any], None]] = []
_ready_apps: list[Any] = []
_lock = threading.Lock()
_installed = False


def custom_dir() -> Path:
    """Directory for user code and its config (default /data/custom)."""
    home = os.environ.get("OPENJARVIS_HOME", "/data")
    return Path(os.environ.get("OPENJARVIS_CUSTOM_DIR", f"{home}/custom"))


def floor_config_path() -> Path:
    """Path of the floor manager config (``OPENJARVIS_FLOOR_CONFIG``)."""
    default = custom_dir() / "floor.toml"
    return Path(os.environ.get("OPENJARVIS_FLOOR_CONFIG", str(default)))


def load_floor_config() -> dict | None:
    """Parse floor.toml. None when the file is absent; raises on bad TOML."""
    import tomllib

    path = floor_config_path()
    if not path.is_file():
        return None
    with path.open("rb") as fh:
        return tomllib.load(fh)


def on_server_ready(callback: Callable[[Any], None]) -> Callable[[Any], None]:
    """Call ``callback(app)`` once the server app exists (usable as decorator).

    A callback registered after the app was built runs immediately. A failing
    callback is logged and does not stop the server.
    """
    with _lock:
        _ready_callbacks.append(callback)
        apps = list(_ready_apps)
    for app in apps:
        _run(callback, app)
    return callback


def _run(callback: Callable[[Any], None], app: Any) -> None:
    try:
        callback(app)
    except Exception:  # noqa: BLE001
        logger.exception("on_server_ready callback %r failed", callback)
        print(
            f"[openjarvis-unraid] server-ready hook {callback!r} failed",
            file=sys.stderr,
        )


def install() -> None:
    """Wrap ``openjarvis.server.app.create_app`` (called by the launcher)."""
    global _installed
    if _installed:
        return
    import openjarvis.server.app as server_app

    original = server_app.create_app

    def create_app(*args: Any, **kwargs: Any) -> Any:
        app = original(*args, **kwargs)
        with _lock:
            _ready_apps.append(app)
            callbacks = list(_ready_callbacks)
        for callback in callbacks:
            _run(callback, app)
        return app

    create_app.__wrapped__ = original  # type: ignore[attr-defined]
    server_app.create_app = create_app
    _installed = True


class CustomEventType(str):
    """An event name outside upstream's EventType enum.

    The bus keys subscribers by event type and only reads ``.value`` when
    logging a failed subscriber, so a str with ``.value`` behaves like an
    enum member there. Upstream bridges (WebSocket, SSE) subscribe to fixed
    enum members, so custom events never leak into them.
    """

    @property
    def value(self) -> str:
        return str(self)


def publish_custom(bus: Any, name: str, data: dict | None = None) -> Any:
    """Publish a custom event (for example ``persona.interject``) on ``bus``."""
    return bus.publish(CustomEventType(name), data or {})


def subscribe_custom(bus: Any, name: str, callback: Callable[[Any], None]) -> None:
    """Subscribe to a custom event published with ``publish_custom``."""
    bus.subscribe(CustomEventType(name), callback)


def add_routes(app: Any, router: Any) -> None:
    """Include ``router`` ahead of the web UI catch-all route."""
    before = len(app.router.routes)
    app.include_router(router)
    new = app.router.routes[before:]
    del app.router.routes[before:]
    app.router.routes[0:0] = new


__all__ = [
    "CustomEventType",
    "add_routes",
    "custom_dir",
    "floor_config_path",
    "install",
    "load_floor_config",
    "on_server_ready",
    "publish_custom",
    "subscribe_custom",
]
