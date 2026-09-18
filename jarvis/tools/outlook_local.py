"""Outlook mail and calendar by driving the desktop app directly.

For tenants that block app registrations. Nothing new talks to Microsoft's
cloud: Outlook is already signed in and already authorised, and Jarvis asks
*it* for things over COM, as the logged-in user. No Azure, no consent, no
tokens.

Requires **classic** Outlook. The "new Outlook" for Windows dropped COM, VBA
and MAPI entirely; there is no local automation path on it.

The COM calls are deliberately confined to small functions at the bottom of
each tool. Everything that decides *what* to ask for -- query building, date
windows, formatting -- is pure and tested.
"""
from __future__ import annotations

import asyncio
import re
import sys
from datetime import datetime, timedelta

from core.config import cfg, tz

from .base import Tool, ToolError

# Outlook object model constants (olFolderInbox, olFolderCalendar, ...).
FOLDER_INBOX = 6
FOLDER_CALENDAR = 9
ITEM_MAIL = 0
ITEM_APPOINTMENT = 1
MEETING = 1          # olMeeting -- turns an appointment into an invite
BUSY_FREE = 0        # olFree

# Outlook's Restrict() wants dates in the UI's short format. This is the
# en-US one; a machine set to another locale needs it changed.
OUTLOOK_DATE = "%m/%d/%Y %I:%M %p"


def available() -> bool:
    """True when this machine can actually drive Outlook."""
    if sys.platform != "win32":
        return False
    import importlib.util

    return importlib.util.find_spec("win32com.client") is not None


# --------------------------------------------------------------------------
# pure helpers -- no COM, so these are unit-tested
# --------------------------------------------------------------------------

def build_mail_filter(query: str | None, unread_only: bool) -> str:
    """A DASL restriction for the inbox.

    DASL rather than the bracket syntax because the two cannot be mixed, and
    free-text needs DASL's LIKE. Supports `from:` and `subject:` prefixes;
    anything else is matched against sender, subject and body.
    """
    parts: list[str] = []
    text = (query or "").strip()

    if text:
        prefix = re.match(r"^(from|subject|body)\s*:\s*(.+)$", text, re.IGNORECASE)
        if prefix:
            field, value = prefix.group(1).lower(), _escape(prefix.group(2))
            column = {
                "from": "urn:schemas:httpmail:fromname",
                "subject": "urn:schemas:httpmail:subject",
                "body": "urn:schemas:httpmail:textdescription",
            }[field]
            parts.append(f"\"{column}\" LIKE '%{value}%'")
        else:
            value = _escape(text)
            parts.append(
                "(\"urn:schemas:httpmail:subject\" LIKE '%{v}%'"
                " OR \"urn:schemas:httpmail:fromname\" LIKE '%{v}%'"
                " OR \"urn:schemas:httpmail:fromemail\" LIKE '%{v}%'"
                " OR \"urn:schemas:httpmail:textdescription\" LIKE '%{v}%')".format(v=value)
            )

    if unread_only:
        parts.append('"urn:schemas:httpmail:read" = 0')

    return "@SQL=" + " AND ".join(parts) if parts else ""


def _escape(value: str) -> str:
    """Neutralise the characters that would break or widen a DASL LIKE."""
    return value.replace("'", "''").replace("%", "").replace('"', "")


def calendar_window(days_ahead: int) -> tuple[datetime, datetime]:
    now = datetime.now(tz())
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return start, start + timedelta(days=max(1, days_ahead))


def build_calendar_filter(start: datetime, end: datetime) -> str:
    return f"[Start] >= '{start.strftime(OUTLOOK_DATE)}' AND [End] <= '{end.strftime(OUTLOOK_DATE)}'"


def format_message(msg: dict) -> str:
    flags = []
    if msg.get("unread"):
        flags.append("unread")
    if msg.get("attachments"):
        flags.append("attachment")
    tag = f" ({', '.join(flags)})" if flags else ""
    return (
        f"[id: {msg.get('id', '')}]{tag}\n"
        f"  From:    {msg.get('sender') or '(unknown)'}\n"
        f"  Subject: {msg.get('subject') or '(no subject)'}\n"
        f"  When:    {msg.get('received') or ''}\n"
        f"  {(msg.get('preview') or '')[:180]}"
    )


def format_appointment(appt: dict) -> str:
    bits = [f"{appt.get('start', '')}-{appt.get('end', '')}  {appt.get('subject') or '(no subject)'}"]
    if appt.get("organizer"):
        bits.append(f"organiser: {appt['organizer']}")
    if appt.get("location"):
        bits.append(f"location: {appt['location']}")
    if appt.get("attendees"):
        bits.append(f"with: {appt['attendees']}")
    return f"[id: {appt.get('id', '')}] " + "\n    ".join(bits)


def free_slots(
    busy: list[tuple[datetime, datetime]],
    days: int,
    duration: int,
    workday_start: int,
    workday_end: int,
) -> list[str]:
    """Gaps of at least `duration` minutes inside working hours, weekdays only."""
    zone = tz()
    now = datetime.now(zone)
    busy = sorted(busy)
    slots: list[str] = []

    for offset in range(days):
        day = (now + timedelta(days=offset)).replace(
            hour=workday_start, minute=0, second=0, microsecond=0
        )
        if day.weekday() >= 5:
            continue
        day_end = day.replace(hour=workday_end)
        cursor = max(day, now)
        for start, end in busy:
            if end <= cursor or start >= day_end:
                continue
            if start - cursor >= timedelta(minutes=duration):
                slots.append(
                    f"{cursor.strftime('%a %b %d')}  "
                    f"{cursor.strftime('%I:%M %p')} - {start.strftime('%I:%M %p')}"
                )
            cursor = max(cursor, end)
        if day_end - cursor >= timedelta(minutes=duration):
            slots.append(
                f"{cursor.strftime('%a %b %d')}  "
                f"{cursor.strftime('%I:%M %p')} - {day_end.strftime('%I:%M %p')}"
            )
    return slots


# --------------------------------------------------------------------------
# COM plumbing
# --------------------------------------------------------------------------

def _outlook():
    """A live Outlook.Application. Replaced wholesale in tests."""
    import win32com.client

    try:
        return win32com.client.Dispatch("Outlook.Application")
    except Exception as exc:  # noqa: BLE001 - pywin32 raises com_error
        raise ToolError(
            "Could not reach Outlook. Make sure the classic Outlook desktop app is "
            "installed and running. The 'new Outlook' does not support automation -- "
            "switch it off with the toggle in the top right."
        ) from exc


async def _com(fn, *args):
    """Run a COM call on a worker thread.

    Every thread that touches COM has to initialise it first, and asyncio's
    executor threads have not.
    """

    def wrapped():
        import pythoncom

        pythoncom.CoInitialize()
        try:
            return fn(*args)
        finally:
            pythoncom.CoUninitialize()

    return await asyncio.to_thread(wrapped)


def _text(value, limit: int = 0) -> str:
    out = str(value or "").strip()
    return out[:limit] if limit and len(out) > limit else out


class LocalOutlookTool(Tool):
    def available(self) -> bool:
        return available()


# --------------------------------------------------------------------------
# mail
# --------------------------------------------------------------------------

class SearchWorkEmailTool(LocalOutlookTool):
    name = "search_work_email"
    description = """
    Search the user's work mailbox (Outlook). Use `query` for free text --
    'from:dana', 'subject:invoice', or plain words matched against sender,
    subject and body. Use `unread_only` to see what is new; unlike the cloud
    version, it can be combined with a query.

    Returns a compact list. Follow up with `read_work_email` for a full message.
    """
    schema = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Free text, or from:/subject:/body: prefixed."},
            "unread_only": {"type": "boolean"},
            "max_results": {"type": "integer", "description": "1-25, default 10."},
        },
    }

    async def run(self, query: str | None = None, unread_only: bool = False,
                  max_results: int = 10) -> str:
        top = max(1, min(int(max_results or 10), 25))
        restriction = build_mail_filter(query, unread_only)
        messages = await _com(self._search, restriction, top)

        if not messages:
            what = f"matching {query!r}" if query else ("unread" if unread_only else "recent")
            return f"No {what} messages in the work mailbox."
        return f"{len(messages)} message(s):\n\n" + "\n\n".join(format_message(m) for m in messages)

    @staticmethod
    def _search(restriction: str, top: int) -> list[dict]:
        namespace = _outlook().GetNamespace("MAPI")
        items = namespace.GetDefaultFolder(FOLDER_INBOX).Items
        items.Sort("[ReceivedTime]", True)
        if restriction:
            items = items.Restrict(restriction)

        out: list[dict] = []
        for item in items:
            try:
                if getattr(item, "Class", ITEM_MAIL) != ITEM_MAIL:
                    continue  # meeting responses and receipts also live here
                out.append({
                    "id": _text(item.EntryID),
                    "sender": f"{_text(item.SenderName)} <{_text(item.SenderEmailAddress)}>",
                    "subject": _text(item.Subject),
                    "received": str(item.ReceivedTime)[:16],
                    "unread": bool(item.UnRead),
                    "attachments": item.Attachments.Count > 0,
                    "preview": _text(item.Body, 200).replace("\r\n", " "),
                })
            except Exception:  # noqa: BLE001 - one unreadable item must not end the search
                continue
            if len(out) >= top:
                break
        return out


class ReadWorkEmailTool(LocalOutlookTool):
    name = "read_work_email"
    description = "Read one work email in full by its id (from `search_work_email`)."
    schema = {
        "type": "object",
        "properties": {"message_id": {"type": "string"}},
        "required": ["message_id"],
    }

    async def run(self, message_id: str) -> str:
        return await _com(self._read, message_id)

    @staticmethod
    def _read(message_id: str) -> str:
        namespace = _outlook().GetNamespace("MAPI")
        try:
            item = namespace.GetItemFromID(message_id)
        except Exception as exc:  # noqa: BLE001
            raise ToolError(f"No message with id {message_id}. Search for it again.") from exc

        body = _text(item.Body)
        if len(body) > 8000:
            body = body[:8000] + "\n\n[truncated]"
        return (
            f"From:    {_text(item.SenderName)} <{_text(item.SenderEmailAddress)}>\n"
            f"To:      {_text(item.To)}\n"
            + (f"Cc:      {_text(item.CC)}\n" if _text(item.CC) else "")
            + f"Subject: {_text(item.Subject)}\n"
            f"When:    {str(item.ReceivedTime)[:16]}\n\n{body}"
        )


class DraftWorkEmailTool(LocalOutlookTool):
    name = "draft_work_email"
    description = """
    Save a work email as an Outlook draft without sending it. Prefer this over
    `send_work_email` when the user says 'draft' or 'write up'.
    """
    schema = {
        "type": "object",
        "properties": {
            "to": {"type": "string"},
            "subject": {"type": "string"},
            "body": {"type": "string"},
        },
        "required": ["to", "subject", "body"],
    }

    async def run(self, to: str, subject: str, body: str) -> str:
        await _com(self._draft, to, subject, body)
        return f"Draft saved in Outlook for {to}. Not sent."

    @staticmethod
    def _draft(to: str, subject: str, body: str) -> None:
        mail = _outlook().CreateItem(ITEM_MAIL)
        mail.To = to
        mail.Subject = subject
        mail.Body = body
        mail.Save()


class SendWorkEmailTool(LocalOutlookTool):
    name = "send_work_email"
    description = """
    Send an email from the user's work mailbox. The user confirms before it
    actually sends.

    Write in the user's voice: plain, direct, no filler. To reply to something,
    pass `reply_to_message_id` -- then `body` is your reply text and Outlook
    handles the subject, recipients and threading.
    """
    schema = {
        "type": "object",
        "properties": {
            "to": {"type": "string", "description": "Recipient(s), comma or semicolon separated. Omit when replying."},
            "subject": {"type": "string", "description": "Omit when replying."},
            "body": {"type": "string"},
            "cc": {"type": "string"},
            "reply_to_message_id": {"type": "string"},
        },
        "required": ["body"],
    }
    confirm = True

    def preview(self, args: dict) -> str:
        body = args.get("body", "")
        snippet = body if len(body) <= 300 else body[:300] + "..."
        if args.get("reply_to_message_id"):
            return f"Send work reply\n\n{snippet}"
        cc = f"\nCc: {args['cc']}" if args.get("cc") else ""
        return (
            f"Send work email\nTo: {args.get('to')}{cc}\n"
            f"Subject: {args.get('subject')}\n\n{snippet}"
        )

    async def run(self, body: str, to: str | None = None, subject: str | None = None,
                  cc: str | None = None, reply_to_message_id: str | None = None) -> str:
        if reply_to_message_id:
            await _com(self._reply, reply_to_message_id, body)
            return "Reply sent from the work mailbox."
        if not to:
            raise ToolError("A recipient is required unless you are replying to a message.")
        await _com(self._send, to, subject or "(no subject)", body, cc or "")
        return f"Sent to {to} from the work mailbox."

    @staticmethod
    def _send(to: str, subject: str, body: str, cc: str) -> None:
        mail = _outlook().CreateItem(ITEM_MAIL)
        mail.To = to
        if cc:
            mail.CC = cc
        mail.Subject = subject
        mail.Body = body
        mail.Send()

    @staticmethod
    def _reply(message_id: str, body: str) -> None:
        namespace = _outlook().GetNamespace("MAPI")
        try:
            original = namespace.GetItemFromID(message_id)
        except Exception as exc:  # noqa: BLE001
            raise ToolError(f"No message with id {message_id}. Search for it again.") from exc
        reply = original.Reply()
        reply.Body = body + "\n\n" + _text(reply.Body)
        reply.Send()


# --------------------------------------------------------------------------
# calendar
# --------------------------------------------------------------------------

class ListEventsTool(LocalOutlookTool):
    name = "list_calendar_events"
    description = """
    List the user's upcoming Outlook calendar events. Use for 'what's on my
    calendar', 'am I free', 'what's my day look like'. Call `get_current_time`
    first if you need to reason about relative dates.
    """
    schema = {
        "type": "object",
        "properties": {
            "days_ahead": {"type": "integer", "description": "Days forward from today. Default 1."}
        },
    }

    async def run(self, days_ahead: int = 1) -> str:
        days = max(1, min(int(days_ahead or 1), 60))
        start, end = calendar_window(days)
        events = await _com(self._list, build_calendar_filter(start, end), 60)
        if not events:
            return f"Nothing scheduled in the next {days} day(s)."
        return (
            f"{len(events)} event(s) over the next {days} day(s):\n\n"
            + "\n".join(format_appointment(e) for e in events)
        )

    @staticmethod
    def _list(restriction: str, cap: int) -> list[dict]:
        namespace = _outlook().GetNamespace("MAPI")
        items = namespace.GetDefaultFolder(FOLDER_CALENDAR).Items
        # Both are required before Restrict, or recurring events are missed.
        items.IncludeRecurrences = True
        items.Sort("[Start]")
        items = items.Restrict(restriction)

        out: list[dict] = []
        for item in items:
            try:
                attendees = _text(item.RequiredAttendees).replace("; ", ", ")
                out.append({
                    "id": _text(item.EntryID),
                    "subject": _text(item.Subject),
                    "start": str(item.Start)[:16],
                    "end": str(item.End)[11:16],
                    "organizer": _text(item.Organizer),
                    "location": _text(item.Location),
                    "attendees": attendees[:120],
                })
            except Exception:  # noqa: BLE001
                continue
            if len(out) >= cap:
                break
        return out


class FindFreeTimeTool(LocalOutlookTool):
    name = "find_free_time"
    description = """
    Find open slots in the user's Outlook calendar. Use before proposing a
    meeting time so you never suggest something that conflicts. Working hours
    and weekdays only.
    """
    schema = {
        "type": "object",
        "properties": {
            "days_ahead": {"type": "integer", "description": "Search window in days. Default 5."},
            "duration_minutes": {"type": "integer", "description": "Minimum slot length. Default 30."},
            "workday_start": {"type": "integer", "description": "Hour, 24h. Default 9."},
            "workday_end": {"type": "integer", "description": "Hour, 24h. Default 17."},
        },
    }

    async def run(self, days_ahead: int = 5, duration_minutes: int = 30,
                  workday_start: int = 9, workday_end: int = 17) -> str:
        days = max(1, min(int(days_ahead or 5), 21))
        duration = max(15, int(duration_minutes or 30))
        start, end = calendar_window(days)
        busy = await _com(self._busy, build_calendar_filter(start, end))

        slots = free_slots(busy, days, duration, workday_start, workday_end)
        if not slots:
            return f"No free blocks of {duration}+ minutes in the next {days} weekday(s)."
        return (
            f"Open slots ({duration}+ min, {workday_start}:00-{workday_end}:00):\n"
            + "\n".join(f"  {s}" for s in slots[:25])
        )

    @staticmethod
    def _busy(restriction: str) -> list[tuple[datetime, datetime]]:
        zone = tz()
        namespace = _outlook().GetNamespace("MAPI")
        items = namespace.GetDefaultFolder(FOLDER_CALENDAR).Items
        items.IncludeRecurrences = True
        items.Sort("[Start]")
        items = items.Restrict(restriction)

        busy: list[tuple[datetime, datetime]] = []
        for item in items:
            try:
                if int(item.BusyStatus) == BUSY_FREE:
                    continue
                start = datetime.fromisoformat(str(item.Start)[:19]).replace(tzinfo=zone)
                end = datetime.fromisoformat(str(item.End)[:19]).replace(tzinfo=zone)
                busy.append((start, end))
            except Exception:  # noqa: BLE001
                continue
        return busy


class CreateEventTool(LocalOutlookTool):
    name = "create_calendar_event"
    description = """
    Book an event on the user's Outlook calendar, optionally inviting people.
    The user confirms before anything is booked. Call `find_free_time` first
    unless they gave you an exact time.

    A Teams link cannot be attached automatically this way -- say so if they
    ask for one, and suggest they add it from the invite window.
    """
    schema = {
        "type": "object",
        "properties": {
            "subject": {"type": "string"},
            "start": {"type": "string", "description": "Local start time, ISO 8601, e.g. '2026-03-14T15:00:00'."},
            "duration_minutes": {"type": "integer", "description": "Default 30."},
            "attendees": {"type": "array", "items": {"type": "string"}},
            "body": {"type": "string", "description": "Agenda / description."},
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

    async def run(self, subject: str, start: str, duration_minutes: int = 30,
                  attendees: list[str] | None = None, body: str | None = None,
                  location: str | None = None) -> str:
        start_dt = parse_local(start)
        duration = max(5, int(duration_minutes or 30))
        people = attendees or []

        await _com(self._create, subject, start_dt, duration, people, body or "", location or "")

        note = f" Invited: {', '.join(people)}." if people else ""
        return (
            f"Booked '{subject}' for {start_dt.strftime('%A %B %d at %I:%M %p')} "
            f"({duration} min).{note}"
        )

    @staticmethod
    def _create(subject, start_dt, duration, attendees, body, location) -> None:
        appt = _outlook().CreateItem(ITEM_APPOINTMENT)
        appt.Subject = subject
        appt.Start = start_dt.strftime("%Y-%m-%d %H:%M")
        appt.Duration = duration
        if body:
            appt.Body = body
        if location:
            appt.Location = location
        if attendees:
            appt.MeetingStatus = MEETING  # makes it an invitation rather than a block
            for address in attendees:
                appt.Recipients.Add(address)
            appt.Recipients.ResolveAll()
            appt.Send()
        else:
            appt.Save()


class CancelEventTool(LocalOutlookTool):
    name = "cancel_calendar_event"
    description = """
    Cancel an event on the user's Outlook calendar by its id (from
    `list_calendar_events`). Attendees are notified.
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
        await _com(self._cancel, event_id, message or "")
        return "Event cancelled."

    @staticmethod
    def _cancel(event_id: str, message: str) -> None:
        namespace = _outlook().GetNamespace("MAPI")
        try:
            appt = namespace.GetItemFromID(event_id)
        except Exception as exc:  # noqa: BLE001
            raise ToolError(f"No event with id {event_id}. List the calendar again.") from exc
        if int(getattr(appt, "MeetingStatus", 0)) == MEETING:
            appt.MeetingStatus = 5  # olMeetingCanceled
            if message:
                appt.Body = message + "\n\n" + _text(appt.Body)
            appt.Save()
            appt.Send()
        else:
            appt.Delete()


def parse_local(value: str) -> datetime:
    """ISO 8601 to a naive local datetime, as Outlook expects."""
    from zoneinfo import ZoneInfo

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
