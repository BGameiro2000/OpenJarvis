"""Cognee graph memory tools for OpenJarvis.

Loaded by the Unraid launcher when ``COGNEE_URL`` is set. Registers:

- ``cognee_recall`` (read only, enabled in the default config): retrieves
  context from the Cognee graph built over the vault. It never asks Cognee for
  an LLM answer (``only_context`` / retrieval-only search types), so a question
  costs one LLM call, the OpenJarvis one.
- ``cognee_remember`` (writes to Cognee, NOT enabled by default): adds a text
  to the Cognee dataset. Add it to ``[tools] enabled`` to use it.

If Cognee is unreachable the tool returns a clear error and the agent carries
on without graph memory. Tools are registered even when Cognee is down at
startup, so they work as soon as Cognee comes back.

Env: COGNEE_URL, COGNEE_API_KEY, COGNEE_DATASET (default "vault"),
COGNEE_RECALL_TOP_K (default 8), COGNEE_TIMEOUT (seconds, default 30).
"""

from __future__ import annotations

import json
import os
from typing import Any

import httpx

from openjarvis.core.registry import ToolRegistry
from openjarvis.core.types import ToolResult
from openjarvis.tools._stubs import BaseTool, ToolSpec

MAX_CHARS = 12000


def _settings() -> dict[str, Any]:
    return {
        "url": os.environ.get("COGNEE_URL", "http://cognee:8000").rstrip("/"),
        "key": os.environ.get("COGNEE_API_KEY", ""),
        "dataset": os.environ.get("COGNEE_DATASET", "vault"),
        "top_k": int(os.environ.get("COGNEE_RECALL_TOP_K", "8")),
        "timeout": float(os.environ.get("COGNEE_TIMEOUT", "30")),
    }


def _headers(key: str) -> dict[str, str]:
    return {"X-Api-Key": key} if key else {}


def _unavailable(tool: str, url: str, exc: Exception) -> ToolResult:
    return ToolResult(
        tool_name=tool,
        content=(
            f"Cognee graph memory is unavailable ({url}: {type(exc).__name__}: {exc}). "
            "Continue without graph memory and tell the user it could not be consulted."
        ),
        success=False,
    )


def _http_error(tool: str, resp: httpx.Response) -> ToolResult:
    hint = ""
    if resp.status_code in (401, 403):
        hint = " Check that COGNEE_API_KEY matches in both containers."
    elif resp.status_code == 404:
        hint = " The dataset may not exist yet; run cognee-ingest first."
    return ToolResult(
        tool_name=tool,
        content=f"Cognee returned HTTP {resp.status_code}: {resp.text[:500]}.{hint}",
        success=False,
    )


def _render(value: Any) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, indent=1, default=str)


@ToolRegistry.register("cognee_recall")
class CogneeRecallTool(BaseTool):
    """Read-only recall from the Cognee knowledge graph over the vault."""

    tool_id = "cognee_recall"
    is_local = False

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="cognee_recall",
            description=(
                "Search the user's notes (the markdown vault) through the Cognee "
                "knowledge graph. Returns related entities, relations and note "
                "excerpts with their source note names. Use it when a question "
                "may depend on their notes, projects, people or past ideas."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "What to look for, in natural language.",
                    },
                    "top_k": {
                        "type": "integer",
                        "description": "How many results to retrieve (default 8).",
                    },
                },
                "required": ["query"],
            },
            category="memory",
            timeout_seconds=60.0,
            required_capabilities=["memory:read", "network:fetch"],
        )

    def execute(self, **params: Any) -> ToolResult:
        s = _settings()
        query = str(params.get("query", "")).strip()
        if not query:
            return ToolResult(tool_name="cognee_recall", content="Empty query.", success=False)
        top_k = int(params.get("top_k") or s["top_k"])
        sections: list[str] = []
        try:
            with httpx.Client(timeout=s["timeout"], headers=_headers(s["key"])) as client:
                # Graph context without the answer: no LLM call inside Cognee.
                # CHUNKS is retrieval only and carries the source documents.
                for search_type, extra in (
                    ("GRAPH_COMPLETION", {"only_context": True}),
                    ("CHUNKS", {}),
                ):
                    resp = client.post(
                        f"{s['url']}/api/v1/search",
                        json={
                            "search_type": search_type,
                            "query": query,
                            "datasets": [s["dataset"]],
                            "top_k": top_k,
                            **extra,
                        },
                    )
                    if resp.status_code != 200:
                        return _http_error("cognee_recall", resp)
                    sections.append(f"## {search_type}\n{_render(resp.json())}")
        except httpx.HTTPError as exc:
            return _unavailable("cognee_recall", s["url"], exc)
        text = "\n\n".join(sections)
        if len(text) > MAX_CHARS:
            text = text[:MAX_CHARS] + "\n[...truncated...]"
        return ToolResult(tool_name="cognee_recall", content=text, success=True)


@ToolRegistry.register("cognee_remember")
class CogneeRememberTool(BaseTool):
    """Add a text to Cognee. Not enabled by default."""

    tool_id = "cognee_remember"
    is_local = False

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="cognee_remember",
            description=(
                "Store a short text in the Cognee knowledge graph (not in the "
                "vault). Costs LLM tokens on the Cognee side."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "The text to remember."},
                },
                "required": ["text"],
            },
            category="memory",
            timeout_seconds=60.0,
            required_capabilities=["memory:write", "network:fetch"],
        )

    def execute(self, **params: Any) -> ToolResult:
        s = _settings()
        text = str(params.get("text", "")).strip()
        if not text:
            return ToolResult(tool_name="cognee_remember", content="Empty text.", success=False)
        try:
            with httpx.Client(timeout=s["timeout"], headers=_headers(s["key"])) as client:
                resp = client.post(
                    f"{s['url']}/api/v1/add",
                    data={"datasetName": s["dataset"], "raw_data": [text]},
                )
                if resp.status_code != 200:
                    return _http_error("cognee_remember", resp)
                resp = client.post(
                    f"{s['url']}/api/v1/cognify",
                    json={"datasets": [s["dataset"]], "run_in_background": True},
                )
                if resp.status_code != 200:
                    return _http_error("cognee_remember", resp)
        except httpx.HTTPError as exc:
            return _unavailable("cognee_remember", s["url"], exc)
        return ToolResult(
            tool_name="cognee_remember",
            content="Stored in Cognee; the graph updates in the background.",
            success=True,
        )


__all__ = ["CogneeRecallTool", "CogneeRememberTool"]
