"""Tool contract shared by every Jarvis capability."""
from __future__ import annotations

import inspect
from typing import Any


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
