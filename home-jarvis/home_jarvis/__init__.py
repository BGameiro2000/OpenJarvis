"""Home Jarvis: Finch and Root sharing one floor, on top of OpenJarvis.

Load in the OpenJarvis container with ``OPENJARVIS_CUSTOM_MODULES=home_jarvis``
(the package lives in ``/data/custom/home_jarvis``). On server start it builds
the floor manager with the server's engine and event bus and mounts its routes
under ``/api/custom/floor``. Config: ``/data/custom/floor.toml``.

It also builds the intake sorter (``home_jarvis.intake``) over the vault and
mounts it under ``/api/custom/intake``. Config: ``/data/custom/intake.toml``.

``HOME_JARVIS_DEMO=1`` uses a scripted brain instead of the model (no key, no
cost), for the HUD and for tests.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

logger = logging.getLogger("home_jarvis")

FLOOR = None  # the running FloorManager, for introspection
SORTER = None  # the running intake Sorter


def _start(app) -> None:  # noqa: ANN001
    global FLOOR, SORTER
    from openjarvis_unraid_hooks import (
        add_routes,
        floor_config_path,
        publish_custom,
    )

    from .brain import EngineBrain
    from .config import load_path
    from .demo import demo_brain
    from .events import EventLog
    from .floor import FloorManager
    from .routes import build_router

    cfg = load_path(floor_config_path())
    bus = app.state.bus
    log = EventLog(publish=lambda name, data: publish_custom(bus, name, data))
    if os.environ.get("HOME_JARVIS_DEMO"):
        brain = demo_brain()
    else:
        vault = Path(os.environ.get("OPENJARVIS_VAULT", "/vault"))
        model = cfg.model or getattr(app.state, "model", "")
        brain = EngineBrain(app.state.engine, model, vault, cfg.review_model)
    FLOOR = FloorManager(brain, cfg, log)
    add_routes(app, build_router(FLOOR))
    logger.info("home_jarvis floor ready: profile %s, modes %s", cfg.profile, cfg.enabled)
    print(
        f"[home_jarvis] floor ready: profile {cfg.profile}, modes {', '.join(cfg.enabled)}"
        + (" (DEMO brain)" if os.environ.get("HOME_JARVIS_DEMO") else ""),
        flush=True,
    )
    try:
        from .intake import build_sorter, demo_mode
        from .intake.routes import build_router as build_intake_router

        SORTER = build_sorter(app, log)
        add_routes(app, build_intake_router(SORTER, log))
        print(
            f"[home_jarvis] intake ready: vault {SORTER.vault}, "
            f"dry_run {SORTER.config.dry_run}" + (" (DEMO classifier)" if demo_mode() else ""),
            flush=True,
        )
    except Exception:  # noqa: BLE001  (a broken intake.toml must not stop the floor)
        logger.exception("home_jarvis intake failed to start")


try:
    from openjarvis_unraid_hooks import on_server_ready

    on_server_ready(_start)
except ImportError:  # imported outside the Unraid image (tests)
    pass
