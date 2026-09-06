"""Tool contract shared by every Jarvis capability."""
from __future__ import annotations

import inspect
import logging
from contextvars import ContextVar
from typing import Any

log = logging.getLogger("jarvis.tools")

# Channel back to the HUD while a tool is still running. Set by the agent for
# the duration of one tool call. A ContextVar rather than an attribute because
# tools are shared singletons and several may be running concurrently --
# asyncio copies the context per task, so each call gets its own callback.
_progress: ContextVar = ContextVar("jarvis_progress", default=None)
_artifacts: ContextVar = ContextVar("jarvis_artifacts", default=None)


def bind_channel(progress, artifact):
    """Attach both callbacks for the current task. Returns reset tokens."""
    return _progress.set(progress), _artifacts.set(artifact)


def release_channel(tokens) -> None:
    progress_token, artifact_token = tokens
    _progress.reset(progress_token)
    _artifacts.reset(artifact_token)


async def report(text: str) -> None:
    """Tell the user what a slow tool is doing right now.

    Never let a status update break the tool it is reporting on.
    """
    callback = _progress.get()
    if callback is None:
        return
    try:
        await callback(text)
    except Exception:  # noqa: BLE001
        log.debug("progress callback failed", exc_info=True)


async def produce(kind: str, label: str, url: str = "", path: str = "") -> None:
    """Hand the HUD something to display -- an image, a 3D model, a file."""
    callback = _artifacts.get()
    if callback is None:
        return
    try:
        await callback({"kind": kind, "label": label, "url": url, "path": path})
    except Exception:  # noqa: BLE001
        log.debug("artifact callback failed", exc_info=True)


class ToolError(Exception):
    """Raised by a tool for a condition the model should see and recover from,
    rather than a crash the user should see."""


class Tool:
    name: str = ""
    description: str = ""
    schema: dict[str, Any] = {"type": "object", "properties": {}}

    #: Outbound / irreversible actions. The agent loop stops and asks the user
    #: before these run. Never set this False for anything that leaves the
    #: machine or changes someone else's calendar.
    confirm: bool = False

    #: A one-line, human-readable rendering of what is about to happen, shown
    #: in the confirmation prompt. Override for anything with `confirm = True`.
    def preview(self, args: dict) -> str:
        return f"{self.name}({', '.join(f'{k}={v!r}' for k, v in args.items())})"

    def available(self) -> bool:
        return True

    async def run(self, **kwargs) -> str:  # pragma: no cover - interface
        raise NotImplementedError

    # ---- plumbing ----

    def spec(self) -> dict:
        return {
            "name": self.name,
            "description": self.description.strip(),
            "input_schema": self.schema,
        }

    async def invoke(self, args: dict) -> str:
        result = self.run(**args)
        if inspect.isawaitable(result):
            result = await result
        return str(result)


class Registry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def add(self, tool: Tool) -> None:
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def active(self) -> list[Tool]:
        """Available tools in a stable order.

        Order matters: the tool block is part of the cached prompt prefix, and
        a reshuffled tool list silently invalidates the whole cache.
        """
        return sorted((t for t in self._tools.values() if t.available()), key=lambda t: t.name)

    def specs(self) -> list[dict]:
        return [t.spec() for t in self.active()]

    def describe(self) -> list[str]:
        return [t.name for t in self.active()]
