"""Tool registry.

Integrations are imported defensively: a missing or broken optional dependency
(no Google libraries installed, say) disables that capability and logs it,
rather than taking the whole assistant down. Jarvis without email is still
useful; Jarvis that will not boot is not.
"""
from __future__ import annotations

import logging

from .base import Registry, Tool, ToolError

log = logging.getLogger("jarvis.tools")

registry = Registry()


def _load(label: str, factory) -> None:
    """Register a group of tools, tolerating import or construction failure."""
    try:
        for tool in factory():
            registry.add(tool)
    except Exception as exc:  # noqa: BLE001 - optional integration
        log.warning("%s tools unavailable: %s: %s", label, type(exc).__name__, exc)


def _core():
    from .clock import ClockTool
    from .memory_tool import ForgetTool, RecallTool, RememberTool

    return [ClockTool(), RememberTool(), RecallTool(), ForgetTool()]


def _research():
    from .research import ReadWebpageTool, WebSearchTool

    return [WebSearchTool(), ReadWebpageTool()]


def _mail():
    from .gmail_tool import (
        DraftEmailTool,
        ReadEmailTool,
        SearchEmailTool,
        SendEmailTool,
    )

    return [SearchEmailTool(), ReadEmailTool(), DraftEmailTool(), SendEmailTool()]


def outlook_backend() -> str:
    """Which implementation of work mail and calendar to use.

    Both expose the same tool names, so exactly one may register -- otherwise
    the second silently overwrites the first in the registry.
    """
    from core.config import cfg

    from . import outlook_local

    mode = cfg.outlook_mode
    if mode in ("graph", "local", "off"):
        return mode
    if cfg.microsoft_enabled:
        return "graph"
    try:
        if outlook_local.available():
            return "local"
    except Exception:  # noqa: BLE001 - never let capability detection be fatal
        log.warning("could not check for the local Outlook app", exc_info=True)
    return "off"


def _work():
    backend = outlook_backend()

    if backend == "graph":
        from .calendar_tool import (
            CancelEventTool,
            CreateEventTool,
            FindFreeTimeTool,
            ListEventsTool,
        )
        from .outlook import (
            DraftWorkEmailTool,
            ReadWorkEmailTool,
            SearchWorkEmailTool,
            SendWorkEmailTool,
        )
    elif backend == "local":
        from .outlook_local import (
            CancelEventTool,
            CreateEventTool,
            DraftWorkEmailTool,
            FindFreeTimeTool,
            ListEventsTool,
            ReadWorkEmailTool,
            SearchWorkEmailTool,
            SendWorkEmailTool,
        )
    else:
        return []

    log.info("work mail and calendar: %s backend", backend)
    return [
        SearchWorkEmailTool(),
        ReadWorkEmailTool(),
        DraftWorkEmailTool(),
        SendWorkEmailTool(),
        ListEventsTool(),
        FindFreeTimeTool(),
        CreateEventTool(),
        CancelEventTool(),
    ]


def _images():
    from .imagegen import GenerateImageTool

    return [GenerateImageTool()]


def _threed():
    from .tripo import (
        Check3DJobTool,
        Convert3DModelTool,
        ImageTo3DTool,
        TextTo3DTool,
        TripoBalanceTool,
    )

    return [
        TextTo3DTool(),
        ImageTo3DTool(),
        Check3DJobTool(),
        Convert3DModelTool(),
        TripoBalanceTool(),
    ]


def _messaging():
    from .messaging import SendTextTool

    return [SendTextTool()]


_load("core", _core)
_load("research", _research)
_load("gmail", _mail)
_load("work mail and calendar", _work)
_load("images", _images)
_load("3d", _threed)
_load("messaging", _messaging)

__all__ = ["registry", "Registry", "Tool", "ToolError"]
