"""Time awareness.

This is a tool rather than a line in the system prompt on purpose: putting a
timestamp in the system prompt changes the cached prefix on every single
request and destroys the prompt cache.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from core.config import cfg

from .base import Tool


class ClockTool(Tool):
    name = "get_current_time"
    description = """
    Get the current date and time in the user's timezone. Call this before any
    reasoning that depends on "now" -- scheduling, deadlines, "next Tuesday",
    "this week", how old something is. Never guess the date.
    """
    schema = {
        "type": "object",
        "properties": {
            "days_offset": {
                "type": "integer",
                "description": "Offset in days from today. 0 = today, 1 = tomorrow, -1 = yesterday.",
            }
        },
    }

    async def run(self, days_offset: int = 0) -> str:
        tz = ZoneInfo(cfg.timezone)
        now = datetime.now(tz) + timedelta(days=days_offset or 0)
        return (
            f"{now.strftime('%A, %B %d, %Y at %I:%M %p')} {now.tzname()} "
            f"(ISO: {now.isoformat()})"
        )
