"""HTTP routes for the intake sorter, mounted on the OpenJarvis server.

All under ``/api/custom/intake`` (behind the OpenJarvis API key):

    GET  /state     dry run flag, routes, menu, inbox files, pending escalations,
                    transcription, last scan
    POST /scan      {"apply": false} -> the plan (apply omitted: the config decides)
    POST /capture   multipart "file" (one or more), or JSON {"text": "...", "why": "..."}
                    -> {"saved": ["inbox/..."]}
    POST /decide    {"id", "action": "file|add_list|edit|leave", "envelope"?, "list"?}
    GET  /log       the decision log tail (?n=50)

Shapes are the contract in the HUD design doc (section 4, intake).
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import Request
from pydantic import BaseModel, Field

from .config import ConfigError
from .sorter import MAX_CAPTURE, DecideError, Sorter

PREFIX = "/api/custom/intake"
CHUNK = 1024 * 1024


# Module level (models and Request): FastAPI cannot resolve names defined or
# imported inside a function when annotations are postponed.
class ScanIn(BaseModel):
    apply: bool | None = None
    by: str = "hud"


class DecideIn(BaseModel):
    id: str
    action: str
    envelope: dict[str, Any] | None = None
    new_list: dict[str, Any] | None = Field(default=None, alias="list")
    by: str = "hud"


def build_router(sorter: Sorter, log: Any = None) -> Any:
    from fastapi import APIRouter, HTTPException

    router = APIRouter()
    if log is not None and sorter.log is None:
        sorter.log = log

    def _config_error(exc: Exception) -> HTTPException:
        return HTTPException(400, f"intake.toml: {exc}")

    @router.get(f"{PREFIX}/state")
    def state():
        try:
            return sorter.state()
        except (ConfigError, ValueError) as exc:
            raise _config_error(exc) from exc

    @router.post(f"{PREFIX}/scan")
    def scan(body: ScanIn | None = None):
        body = body or ScanIn()
        try:
            return sorter.scan(body.apply, by=body.by[:20] or "hud")
        except (ConfigError, ValueError) as exc:
            raise _config_error(exc) from exc

    @router.get(f"{PREFIX}/log")
    def decision_log(n: int = 50):
        return sorter.decision_log(max(1, min(n, 1000)))

    @router.post(f"{PREFIX}/decide")
    def decide(body: DecideIn):
        try:
            return sorter.decide(
                body.id, body.action, body.envelope, body.new_list, by=body.by[:20] or "hud"
            )
        except DecideError as exc:
            raise HTTPException(exc.status, {"error": str(exc), "checks": exc.checks}) from exc
        except ConfigError as exc:
            raise _config_error(exc) from exc

    @router.post(f"{PREFIX}/capture")
    async def capture(request: Request):
        length = request.headers.get("content-length")
        if length and length.isdigit() and int(length) > MAX_CAPTURE + CHUNK:
            raise HTTPException(413, "upload is larger than 200 MB")
        ctype = request.headers.get("content-type", "")
        saved: list[str] = []
        if ctype.startswith("multipart/form-data"):
            form = await request.form()
            uploads = [v for k, v in form.multi_items() if k == "file" and hasattr(v, "read")]
            if not uploads:
                raise HTTPException(400, "no 'file' field")
            for upload in uploads:
                chunks, size = [], 0
                while chunk := await upload.read(CHUNK):
                    size += len(chunk)
                    if size > MAX_CAPTURE:
                        raise HTTPException(413, "upload is larger than 200 MB")
                    chunks.append(chunk)
                try:
                    name = upload.filename or "upload"
                    saved.append(sorter.capture_bytes(name, b"".join(chunks)))
                except ValueError as exc:
                    raise HTTPException(400, str(exc)) from exc
            return {"saved": saved}
        try:
            body = json.loads(await request.body() or b"{}")
        except ValueError as exc:
            raise HTTPException(400, "expected multipart 'file' or JSON {'text'}") from exc
        if not isinstance(body, dict) or not isinstance(body.get("text"), str):
            raise HTTPException(400, "expected multipart 'file' or JSON {'text'}")
        try:
            saved.append(sorter.capture_text(body["text"], str(body.get("why") or "")))
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return {"saved": saved}

    return router
