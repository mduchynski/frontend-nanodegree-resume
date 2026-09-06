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


def _calendar():
    from .calendar_tool import (
        CancelEventTool,
        CreateEventTool,
        FindFreeTimeTool,
        ListEventsTool,
    )

    return [ListEventsTool(), FindFreeTimeTool(), CreateEventTool(), CancelEventTool()]


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
_load("calendar", _calendar)
_load("images", _images)
_load("3d", _threed)
_load("messaging", _messaging)

__all__ = ["registry", "Registry", "Tool", "ToolError"]
