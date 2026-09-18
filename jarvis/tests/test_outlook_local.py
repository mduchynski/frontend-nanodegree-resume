"""Local Outlook (COM) tools.

There is no Outlook and no Windows in CI, so this does two things: tests the
pure query/format/slot logic directly, and runs the tools against a fake
Outlook.Application that mimics the object model closely enough to catch
wrong property names and wrong call sequences.

What it cannot prove: that real Outlook accepts these DASL strings and date
formats. That needs a Windows box.
"""
from __future__ import annotations

import asyncio
import pathlib
import sys
from datetime import datetime, timedelta

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

fails = []


def check(cond, label):
    print(f"  {'ok  ' if cond else 'FAIL'} {label}")
    if not cond:
        fails.append(label)


from tools import outlook_local as ol  # noqa: E402
from tools.base import ToolError  # noqa: E402


# ---------------------------------------------------------------- fake COM

class FakeItems(list):
    def __init__(self, items):
        super().__init__(items)
        self.IncludeRecurrences = False
        self.sorted_by = None
        self.restriction = None

    def Sort(self, field, descending=False):
        self.sorted_by = (field, descending)

    def Restrict(self, restriction):
        self.restriction = restriction
        CALLS["restrict"] = restriction
        out = FakeItems(list(self))
        out.IncludeRecurrences = self.IncludeRecurrences
        return out


class FakeMail:
    Class = 0

    def __init__(self, **kw):
        self.EntryID = kw.get("id", "E1")
        self.SenderName = kw.get("sender", "Dana Reyes")
        self.SenderEmailAddress = kw.get("email", "dana@corp.com")
        self.Subject = kw.get("subject", "Q3 numbers")
        self.Body = kw.get("body", "Attaching the revised deck ahead of Thursday.")
        self.To = kw.get("to", "duke@corp.com")
        self.CC = kw.get("cc", "")
        self.ReceivedTime = kw.get("received", "2026-09-18 14:05:00")
        self.UnRead = kw.get("unread", True)
        self.Attachments = type("A", (), {"Count": kw.get("attachments", 1)})()
        self.sent = False
        self.saved = False

    def Send(self):
        self.sent = True
        CALLS["sent"] = self

    def Save(self):
        self.saved = True
        CALLS["saved"] = self

    def Reply(self):
        reply = FakeMail(subject="RE: " + self.Subject, body="\n> original")
        CALLS["reply_of"] = self.EntryID
        return reply


class FakeAppt:
    def __init__(self, **kw):
        self.EntryID = kw.get("id", "A1")
        self.Subject = kw.get("subject", "Design review")
        self.Start = kw.get("start", "2026-09-18 14:00:00")
        self.End = kw.get("end", "2026-09-18 15:00:00")
        self.Organizer = kw.get("organizer", "Dana Reyes")
        self.Location = kw.get("location", "Room 4")
        self.RequiredAttendees = kw.get("attendees", "Sam Ito; Pat Lee")
        self.BusyStatus = kw.get("busy", 2)
        self.MeetingStatus = kw.get("meeting", 0)
        self.Body = ""
        self.Duration = 0
        self.Recipients = FakeRecipients()

    def Save(self):
        CALLS["saved"] = self

    def Send(self):
        CALLS["sent"] = self

    def Delete(self):
        CALLS["deleted"] = self.EntryID


class FakeRecipients:
    def __init__(self):
        self.added = []
        self.resolved = False

    def Add(self, address):
        self.added.append(address)

    def ResolveAll(self):
        self.resolved = True


class FakeNamespace:
    def __init__(self, mail, appts):
        self.mail = mail
        self.appts = appts

    def GetDefaultFolder(self, folder_id):
        items = FakeItems(self.mail if folder_id == ol.FOLDER_INBOX else self.appts)
        CALLS["folder"] = folder_id
        return type("F", (), {"Items": items})()

    def GetItemFromID(self, entry_id):
        for item in list(self.mail) + list(self.appts):
            if item.EntryID == entry_id:
                return item
        raise RuntimeError("not found")


class FakeOutlook:
    def __init__(self, mail, appts):
        self.ns = FakeNamespace(mail, appts)
        self.created = []

    def GetNamespace(self, _):
        return self.ns

    def CreateItem(self, kind):
        item = FakeMail(subject="", body="") if kind == ol.ITEM_MAIL else FakeAppt(subject="")
        self.created.append(item)
        CALLS["created"] = item
        return item


CALLS: dict = {}
MAIL = [FakeMail(id="E1"), FakeMail(id="E2", subject="Lunch?", sender="Sam Ito", unread=False, attachments=0)]
APPTS = [FakeAppt(id="A1"), FakeAppt(id="A2", subject="Vendor call", start="2026-09-18 17:00:00",
                                    end="2026-09-18 17:30:00", busy=0)]
APP = FakeOutlook(MAIL, APPTS)

ol._outlook = lambda: APP
# COM needs a worker thread with CoInitialize; neither exists here.
ol._com = lambda fn, *a: asyncio.get_event_loop().run_in_executor(None, lambda: fn(*a))


# ---------------------------------------------------------------- pure logic

def pure_checks():
    print("=== DASL query building ===")
    f = ol.build_mail_filter("invoice", False)
    check(f.startswith("@SQL="), "uses DASL")
    check("urn:schemas:httpmail:subject" in f and "textdescription" in f,
          "plain text searches subject and body")
    check("fromname" in f, "and the sender")

    f = ol.build_mail_filter("from:dana", False)
    check("fromname" in f and "subject" not in f, "from: prefix narrows to sender")
    f = ol.build_mail_filter("subject:invoice", False)
    check("subject" in f and "fromname" not in f, "subject: prefix narrows to subject")

    f = ol.build_mail_filter(None, True)
    check(f == '@SQL="urn:schemas:httpmail:read" = 0', "unread-only filter")

    f = ol.build_mail_filter("deck", True)
    check("AND" in f and "read\" = 0" in f, "query and unread combine locally")
    check(ol.build_mail_filter(None, False) == "", "no filter when nothing asked")

    print("\n=== injection-ish input is neutralised ===")
    f = ol.build_mail_filter("o'brien", False)
    check("''" in f and "'o'brien'" not in f, "single quote doubled")
    check(ol._escape("100%") == "100", "stray wildcard stripped from the value")
    f = ol.build_mail_filter("100%", False)
    check("'%100%'" in f, "value wrapped in the LIKE wildcards, not the user's")
    check('"' not in ol._escape('a"b'), "quote that would close a DASL column name removed")

    print("\n=== free-slot arithmetic ===")
    zone = ol.tz()
    base = datetime.now(zone).replace(hour=0, minute=0, second=0, microsecond=0)
    # find the next weekday so the test is not run-day dependent
    day = base + timedelta(days=1)
    while day.weekday() >= 5:
        day += timedelta(days=1)
    offset = (day - base).days
    busy = [(day.replace(hour=10), day.replace(hour=11)),
            (day.replace(hour=14), day.replace(hour=15))]
    slots = ol.free_slots(busy, offset + 1, 30, 9, 17)
    joined = " | ".join(slots)
    check(any("09:00 AM - 10:00 AM" in s for s in slots), "gap before the first meeting")
    check(any("11:00 AM - 02:00 PM" in s for s in slots), "gap between meetings")
    check(any("03:00 PM - 05:00 PM" in s for s in slots), "gap after the last")
    check("Sat" not in joined and "Sun" not in joined, "weekends skipped")

    long_only = ol.free_slots(busy, offset + 1, 180, 9, 17)
    check(not any("09:00 AM" in s for s in long_only), "a 3h request drops the 1h gap")

    print("\n=== time parsing ===")
    check(ol.parse_local("2026-03-14T15:00:00") == datetime(2026, 3, 14, 15, 0), "naive ISO")
    aware = ol.parse_local("2026-03-14T19:00:00+00:00")
    check(aware.tzinfo is None, "offset-aware converted to naive local")
    try:
        ol.parse_local("next tuesday")
        check(False, "bad time rejected")
    except ToolError as exc:
        check("ISO 8601" in str(exc), "bad time explains the format")


# ---------------------------------------------------------------- tools

async def tool_checks():
    print("\n=== searching mail ===")
    out = await ol.SearchWorkEmailTool().run(query="deck")
    check(CALLS["folder"] == ol.FOLDER_INBOX, "reads the inbox")
    check(CALLS["restrict"].startswith("@SQL="), "restriction applied server-side")
    check("Dana Reyes <dana@corp.com>" in out, "sender rendered")
    check("(unread, attachment)" in out, "flags surfaced")
    check("[id: E1]" in out, "id available for follow-up")

    out = await ol.SearchWorkEmailTool().run(max_results=1)
    check(out.count("[id:") == 1, "max_results honoured")

    print("\n=== non-mail items in the inbox are skipped ===")
    receipt = FakeMail(id="R1")
    receipt.Class = 3   # a meeting response, not a MailItem
    APP.ns.mail = [receipt] + MAIL
    out = await ol.SearchWorkEmailTool().run()
    check("[id: R1]" not in out, "meeting responses filtered out")
    APP.ns.mail = MAIL

    print("\n=== reading ===")
    out = await ol.ReadWorkEmailTool().run(message_id="E1")
    check("Attaching the revised deck" in out, "body returned")
    check("Subject: Q3 numbers" in out, "headers returned")
    try:
        await ol.ReadWorkEmailTool().run(message_id="nope")
        check(False, "unknown id raises")
    except ToolError as exc:
        check("Search for it again" in str(exc), "unknown id is actionable")

    print("\n=== drafting saves, never sends ===")
    CALLS.clear()
    await ol.DraftWorkEmailTool().run(to="dana@corp.com", subject="Re: Q3", body="Looks good.")
    check(CALLS.get("saved") is not None, "draft saved")
    check(CALLS.get("sent") is None, "nothing sent")
    check(CALLS["saved"].To == "dana@corp.com", "recipient set")

    print("\n=== sending ===")
    CALLS.clear()
    await ol.SendWorkEmailTool().run(to="a@b.com", subject="Thursday", body="Confirmed.", cc="c@d.com")
    check(CALLS.get("sent") is not None, "mail sent")
    check(CALLS["sent"].CC == "c@d.com", "cc set")

    CALLS.clear()
    await ol.SendWorkEmailTool().run(body="Works for me.", reply_to_message_id="E1")
    check(CALLS.get("reply_of") == "E1", "replied to the right message")
    check("Works for me." in CALLS["sent"].Body, "reply text on top")
    check("> original" in CALLS["sent"].Body, "quoted original kept below")

    try:
        await ol.SendWorkEmailTool().run(body="orphan")
        check(False, "missing recipient raises")
    except ToolError as exc:
        check("recipient is required" in str(exc), "missing recipient explained")

    print("\n=== calendar ===")
    out = await ol.ListEventsTool().run(days_ahead=2)
    check(CALLS["folder"] == ol.FOLDER_CALENDAR, "reads the calendar folder")
    check("[Start] >=" in CALLS["restrict"], "date-window restriction applied")
    check("Design review" in out and "Room 4" in out, "event details rendered")
    check("Sam Ito, Pat Lee" in out, "attendees listed")

    print("\n=== booking ===")
    CALLS.clear()
    out = await ol.CreateEventTool().run(
        subject="Sync", start="2026-09-21T15:00:00", duration_minutes=45,
        attendees=["dana@corp.com", "sam@corp.com"])
    item = CALLS["created"]
    check(item.Subject == "Sync", "subject set")
    check(item.Duration == 45, "duration set")
    check(item.MeetingStatus == ol.MEETING, "becomes an invitation when there are attendees")
    check(item.Recipients.added == ["dana@corp.com", "sam@corp.com"], "attendees added")
    check(item.Recipients.resolved, "recipients resolved before sending")
    check(CALLS.get("sent") is not None, "invitation sent")

    CALLS.clear()
    await ol.CreateEventTool().run(subject="Focus block", start="2026-09-21T09:00:00")
    check(CALLS["created"].MeetingStatus == 0, "solo event is not an invitation")
    check(CALLS.get("saved") is not None and CALLS.get("sent") is None, "saved, not sent")

    print("\n=== free time uses only busy events ===")
    out = await ol.FindFreeTimeTool().run(days_ahead=3)
    check("Open slots" in out or "No free blocks" in out, "returns a usable answer")

    print("\n=== gating ===")
    check(ol.SendWorkEmailTool().confirm and ol.CreateEventTool().confirm
          and ol.CancelEventTool().confirm, "outbound actions are gated")
    check(not ol.SearchWorkEmailTool().confirm and not ol.ReadWorkEmailTool().confirm
          and not ol.DraftWorkEmailTool().confirm, "reads and drafts are not")
    pv = ol.CreateEventTool().preview({"subject": "Sync", "start": "2026-09-21T15:00:00",
                                       "attendees": ["dana@corp.com"]})
    check("Sync" in pv and "dana@corp.com" in pv, "booking preview is readable")


pure_checks()
asyncio.get_event_loop().run_until_complete(tool_checks())
print("\n" + ("ALL LOCAL OUTLOOK CHECKS PASSED" if not fails else f"{len(fails)} FAILURES: {fails}"))
sys.exit(1 if fails else 0)
