"""Calendar over Microsoft Graph: read the schedule, find gaps, book Teams meetings."""
from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import httpx2 as httpx

from core.config import cfg

from ._microsoft import GRAPH, headers
from .base import Tool, ToolError

_TIMEOUT = httpx.Timeout(25.0, connect=8.0)


class GraphTool(Tool):
    def available(self) -> bool:
        return cfg.microsoft_enabled

    async def _get(self, path: str, params: dict | None = None) -> dict:
        return await self._call("GET", path, params=params)

    async def _post(self, path: str, body: dict) -> dict:
        return await self._call("POST", path, json=body)

    async def _call(self, method: str, path: str, **kw) -> dict:
        hdrs = headers()
        # Ask Graph to return times already converted to the user's timezone.
        hdrs["Prefer"] = f'outlook.timezone="{cfg.timezone}"'
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                resp = await client.request(method, f"{GRAPH}{path}", headers=hdrs, **kw)
        except httpx.HTTPError as exc:
            raise ToolError(f"Could not reach Microsoft Graph: {exc}") from exc

        if resp.status_code >= 400:
            detail = ""
            try:
                detail = resp.json().get("error", {}).get("message", "")
            except Exception:
                detail = resp.text[:300]
            raise ToolError(f"Graph {resp.status_code}: {detail}")
        return resp.json() if resp.content else {}


def _fmt(event: dict) -> str:
    start = event.get("start", {}).get("dateTime", "")[:16].replace("T", " ")
    end = event.get("end", {}).get("dateTime", "")[11:16]
    subject = event.get("subject", "(no subject)")
    organizer = event.get("organizer", {}).get("emailAddress", {}).get("name", "")
    location = (event.get("location") or {}).get("displayName", "")
    online = " [Teams]" if event.get("isOnlineMeeting") else ""
    bits = [f"{start}-{end}  {subject}{online}"]
    if organizer:
        bits.append(f"organiser: {organizer}")
    if location:
        bits.append(f"location: {location}")
    attendees = event.get("attendees") or []
    if attendees:
        names = [
            a.get("emailAddress", {}).get("name") or a.get("emailAddress", {}).get("address", "")
            for a in attendees[:6]
        ]
        bits.append(f"with: {', '.join(n for n in names if n)}")
    return f"[id: {event.get('id', '')[:24]}...] " + "\n    ".join(bits)


def _window(days_ahead: int) -> tuple[str, str]:
    tz = ZoneInfo(cfg.timezone)
    now = datetime.now(tz)
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return start.isoformat(), (start + timedelta(days=days_ahead)).isoformat()


def _parse_local(value: str) -> datetime:
    """Parse an ISO 8601 start time into a naive local datetime.

    Graph wants a naive wall-clock time paired with an explicit timeZone
    field, so an offset-aware input is converted into the user's zone and
    then stripped rather than passed through.
    """
    raw = (value or "").strip()
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ToolError(
            f"Could not parse start time {value!r}. Use ISO 8601, e.g. 2026-03-14T15:00:00"
        ) from exc
    if dt.tzinfo is not None:
        dt = dt.astimezone(ZoneInfo(cfg.timezone)).replace(tzinfo=None)
    return dt


class ListEventsTool(GraphTool):
    name = "list_calendar_events"
    description = """
    List the user's upcoming calendar events from Outlook/Teams. Use for
    'what's on my calendar', 'am I free', 'what's my day look like'. Always
    call `get_current_time` first if you need to reason about relative dates.
    """
    schema = {
        "type": "object",
        "properties": {
            "days_ahead": {
                "type": "integer",
                "description": "How many days forward from today. Default 1 (today only).",
            }
        },
    }

    async def run(self, days_ahead: int = 1) -> str:
        days = max(1, min(int(days_ahead or 1), 60))
        start, end = _window(days)
        data = await self._get(
            "/me/calendarView",
            params={
                "startDateTime": start,
                "endDateTime": end,
                "$orderby": "start/dateTime",
                "$top": "50",
                "$select": "id,subject,start,end,organizer,attendees,location,isOnlineMeeting",
            },
        )
        events = data.get("value", [])
        if not events:
            return f"Nothing scheduled in the next {days} day(s)."
        header = f"{len(events)} event(s) over the next {days} day(s):"
        return header + "\n\n" + "\n".join(_fmt(e) for e in events)


class FindFreeTimeTool(GraphTool):
    name = "find_free_time"
    description = """
    Find open slots in the user's calendar. Use before proposing a meeting time
    so you never suggest something that conflicts. Returns gaps inside working
    hours only.
    """
    schema = {
        "type": "object",
        "properties": {
            "days_ahead": {"type": "integer", "description": "Search window in days. Default 5."},
            "duration_minutes": {
                "type": "integer",
                "description": "Minimum slot length to report. Default 30.",
            },
            "workday_start": {"type": "integer", "description": "Hour, 24h. Default 9."},
            "workday_end": {"type": "integer", "description": "Hour, 24h. Default 17."},
        },
    }

    async def run(
        self,
        days_ahead: int = 5,
        duration_minutes: int = 30,
        workday_start: int = 9,
        workday_end: int = 17,
    ) -> str:
        days = max(1, min(int(days_ahead or 5), 21))
        duration = max(15, int(duration_minutes or 30))
        start, end = _window(days)

        data = await self._get(
            "/me/calendarView",
            params={
                "startDateTime": start,
                "endDateTime": end,
                "$orderby": "start/dateTime",
                "$top": "100",
                "$select": "subject,start,end,showAs",
            },
        )
        tz = ZoneInfo(cfg.timezone)
        busy: list[tuple[datetime, datetime]] = []
        for ev in data.get("value", []):
            if ev.get("showAs") in ("free", "workingElsewhere"):
                continue
            try:
                s = datetime.fromisoformat(ev["start"]["dateTime"][:19]).replace(tzinfo=tz)
                e = datetime.fromisoformat(ev["end"]["dateTime"][:19]).replace(tzinfo=tz)
                busy.append((s, e))
            except (KeyError, ValueError):
                continue
        busy.sort()

        now = datetime.now(tz)
        slots: list[str] = []
        for offset in range(days):
            day = (now + timedelta(days=offset)).replace(
                hour=workday_start, minute=0, second=0, microsecond=0
            )
            day_end = day.replace(hour=workday_end)
            if day.weekday() >= 5:  # skip weekends
                continue
            cursor = max(day, now)
            for s, e in busy:
                if e <= cursor or s >= day_end:
                    continue
                if s - cursor >= timedelta(minutes=duration):
                    slots.append(
                        f"{cursor.strftime('%a %b %d')}  "
                        f"{cursor.strftime('%I:%M %p')} - {s.strftime('%I:%M %p')}"
                    )
                cursor = max(cursor, e)
            if day_end - cursor >= timedelta(minutes=duration):
                slots.append(
                    f"{cursor.strftime('%a %b %d')}  "
                    f"{cursor.strftime('%I:%M %p')} - {day_end.strftime('%I:%M %p')}"
                )

        if not slots:
            return f"No free blocks of {duration}+ minutes in the next {days} weekday(s)."
        return f"Open slots ({duration}+ min, {workday_start}:00-{workday_end}:00):\n" + "\n".join(
            f"  {s}" for s in slots[:25]
        )


class CreateEventTool(GraphTool):
    name = "create_calendar_event"
    description = """
    Book an event on the user's Outlook/Teams calendar, optionally inviting
    people and generating a Teams meeting link. The user confirms before
    anything is actually booked. Call `find_free_time` first unless the user
    gave you an exact time.
    """
    schema = {
        "type": "object",
        "properties": {
            "subject": {"type": "string"},
            "start": {
                "type": "string",
                "description": "Local start time, ISO 8601, e.g. '2026-03-14T15:00:00'. No timezone suffix.",
            },
            "duration_minutes": {"type": "integer", "description": "Default 30."},
            "attendees": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Email addresses to invite.",
            },
            "body": {"type": "string", "description": "Agenda / description."},
            "teams_meeting": {
                "type": "boolean",
                "description": "Attach a Teams link. Default true when there are attendees.",
            },
            "location": {"type": "string"},
        },
        "required": ["subject", "start"],
    }
    confirm = True

    def preview(self, args: dict) -> str:
        who = ", ".join(args.get("attendees") or []) or "just you"
        return (
            f"Book calendar event\n"
            f"What:  {args.get('subject')}\n"
            f"When:  {args.get('start')} for {args.get('duration_minutes', 30)} min\n"
            f"Who:   {who}"
        )

    async def run(
        self,
        subject: str,
        start: str,
        duration_minutes: int = 30,
        attendees: list[str] | None = None,
        body: str | None = None,
        teams_meeting: bool | None = None,
        location: str | None = None,
    ) -> str:
        start_dt = _parse_local(start)

        duration = max(5, int(duration_minutes or 30))
        end_dt = start_dt + timedelta(minutes=duration)
        attendees = attendees or []
        if teams_meeting is None:
            teams_meeting = bool(attendees)

        payload: dict = {
            "subject": subject,
            "start": {"dateTime": start_dt.isoformat(), "timeZone": cfg.timezone},
            "end": {"dateTime": end_dt.isoformat(), "timeZone": cfg.timezone},
            "attendees": [
                {"emailAddress": {"address": a}, "type": "required"} for a in attendees
            ],
        }
        if body:
            payload["body"] = {"contentType": "text", "content": body}
        if location:
            payload["location"] = {"displayName": location}
        if teams_meeting:
            payload["isOnlineMeeting"] = True
            payload["onlineMeetingProvider"] = "teamsForBusiness"

        created = await self._post("/me/events", payload)
        link = (created.get("onlineMeeting") or {}).get("joinUrl")
        parts = [
            f"Booked '{subject}' for "
            f"{start_dt.strftime('%A %B %d at %I:%M %p')} ({duration} min)."
        ]
        if attendees:
            parts.append(f"Invited: {', '.join(attendees)}.")
        if link:
            parts.append(f"Teams link: {link}")
        return " ".join(parts)


class CancelEventTool(GraphTool):
    name = "cancel_calendar_event"
    description = """
    Cancel an event on the user's calendar by its id (from
    `list_calendar_events`). Attendees are notified automatically.
    """
    schema = {
        "type": "object",
        "properties": {
            "event_id": {"type": "string"},
            "message": {"type": "string", "description": "Optional note to attendees."},
        },
        "required": ["event_id"],
    }
    confirm = True

    def preview(self, args: dict) -> str:
        return f"Cancel calendar event {args.get('event_id')} and notify attendees"

    async def run(self, event_id: str, message: str | None = None) -> str:
        await self._post(
            f"/me/events/{event_id}/cancel", {"Comment": message or "Cancelled."}
        )
        return "Event cancelled and attendees notified."
