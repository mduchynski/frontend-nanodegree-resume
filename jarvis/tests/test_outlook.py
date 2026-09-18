"""Outlook mail over Microsoft Graph.

Graph is unreachable from CI, so these run a local mock and assert on the
requests the tools actually build -- in particular that $search is never sent
alongside $filter or $orderby, which Graph rejects outright.
"""
from __future__ import annotations

import asyncio
import json
import pathlib
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

fails = []
SEEN = {"params": None, "path": None, "body": None, "prefer": None}


def check(cond, label):
    print(f"  {'ok  ' if cond else 'FAIL'} {label}")
    if not cond:
        fails.append(label)


MESSAGES = [
    {
        "id": "AAMk-1", "subject": "Q3 numbers", "isRead": False, "hasAttachments": True,
        "receivedDateTime": "2026-09-18T14:05:00Z",
        "from": {"emailAddress": {"name": "Dana Reyes", "address": "dana@corp.com"}},
        "bodyPreview": "Attaching the revised deck ahead of Thursday.",
    },
    {
        "id": "AAMk-2", "subject": "Lunch?", "isRead": True, "hasAttachments": False,
        "receivedDateTime": "2026-09-18T11:30:00Z",
        "from": {"emailAddress": {"name": "Sam Ito", "address": "sam@corp.com"}},
        "bodyPreview": "Any good around noon?",
    },
]


class MockGraph(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _json(self, status, body):
        raw = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        parsed = urlparse(self.path)
        SEEN["path"] = parsed.path
        SEEN["params"] = {k: v[0] for k, v in parse_qs(parsed.query).items()}
        SEEN["prefer"] = self.headers.get("Prefer")

        # Graph really does reject this combination.
        p = SEEN["params"]
        if "$search" in p and ("$filter" in p or "$orderby" in p):
            return self._json(400, {"error": {"message":
                "Sorting or filtering is not supported in conjunction with search."}})

        if parsed.path.startswith("/me/messages/"):
            return self._json(200, {
                "id": "AAMk-1", "subject": "Q3 numbers",
                "receivedDateTime": "2026-09-18T14:05:00Z",
                "from": {"emailAddress": {"name": "Dana Reyes", "address": "dana@corp.com"}},
                "toRecipients": [{"emailAddress": {"name": "Duke", "address": "duke@corp.com"}}],
                "ccRecipients": [],
                "body": {"contentType": "html",
                         "content": "<html><head><style>b{}</style></head><body>"
                                    "<p>Revised deck attached.</p><script>x()</script>"
                                    "<p>Let me know by Thursday.</p></body></html>"},
            })
        if parsed.path == "/me/messages":
            return self._json(200, {"value": MESSAGES})
        return self._json(404, {"error": {"message": "nope"}})

    def do_POST(self):
        parsed = urlparse(self.path)
        SEEN["path"] = parsed.path
        n = int(self.headers.get("Content-Length", 0))
        SEEN["body"] = json.loads(self.rfile.read(n) or b"{}")
        if parsed.path == "/me/sendMail":
            return self._json(202, {})
        if parsed.path.endswith("/reply"):
            return self._json(202, {})
        if parsed.path == "/me/messages":
            return self._json(201, {"id": "draft-99"})
        return self._json(404, {"error": {"message": "nope"}})


async def main():
    srv = HTTPServer(("127.0.0.1", 8921), MockGraph)
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    from core import config
    object.__setattr__(config.cfg, "ms_client_id", "test-client")

    import tools._microsoft as ms
    ms.GRAPH = "http://127.0.0.1:8921"
    ms.headers = lambda: {"Authorization": "Bearer test", "Content-Type": "application/json"}

    from tools.base import ToolError
    from tools.outlook import (
        DraftWorkEmailTool, ReadWorkEmailTool, SearchWorkEmailTool, SendWorkEmailTool,
    )

    print("=== free-text search uses $search ALONE ===")
    out = await SearchWorkEmailTool().run(query="from:dana")
    p = SEEN["params"]
    check("$search" in p and p["$search"] == '"from:dana"', "$search sent, quoted")
    check("$filter" not in p, "no $filter alongside $search")
    check("$orderby" not in p, "no $orderby alongside $search")
    check("Dana Reyes <dana@corp.com>" in out, "sender rendered with name and address")
    check("unread, attachment" in out, "flags surfaced")
    check("AAMk-1" in out, "message id available for follow-up")

    print("\n=== unread uses $filter + $orderby, no $search ===")
    await SearchWorkEmailTool().run(unread_only=True)
    p = SEEN["params"]
    check(p.get("$filter") == "isRead eq false", "$filter set")
    check(p.get("$orderby") == "receivedDateTime desc", "$orderby newest first")
    check("$search" not in p, "no $search alongside $filter")

    print("\n=== query wins over unread_only, rather than 400ing ===")
    await SearchWorkEmailTool().run(query="invoice", unread_only=True)
    p = SEEN["params"]
    check("$search" in p and "$filter" not in p and "$orderby" not in p,
          "conflicting args resolve to a legal request")

    print("\n=== max_results is clamped ===")
    await SearchWorkEmailTool().run(max_results=500)
    check(SEEN["params"]["$top"] == "25", "clamped to 25")
    # 0 means "unspecified" for a model-supplied argument, so it takes the
    # default rather than asking Graph for zero messages.
    await SearchWorkEmailTool().run(max_results=0)
    check(SEEN["params"]["$top"] == "10", "0 falls back to the default")
    await SearchWorkEmailTool().run(max_results=-5)
    check(SEEN["params"]["$top"] == "1", "negative clamped to a legal value")

    print("\n=== reading strips HTML ===")
    body = await ReadWorkEmailTool().run(message_id="AAMk-1")
    check("Revised deck attached." in body, "body text extracted")
    check("Let me know by Thursday." in body, "all paragraphs kept")
    check("<p>" not in body and "script" not in body.lower(), "markup and scripts removed")
    check("Duke <duke@corp.com>" in body, "recipients shown")

    print("\n=== drafting does not send ===")
    out = await DraftWorkEmailTool().run(to="dana@corp.com", subject="Re: Q3", body="Looks good.")
    check(SEEN["path"] == "/me/messages", "POST to /me/messages, not sendMail")
    check(SEEN["body"]["toRecipients"][0]["emailAddress"]["address"] == "dana@corp.com",
          "recipient parsed")
    check("Not sent" in out, "makes clear nothing was sent")

    print("\n=== sending ===")
    out = await SendWorkEmailTool().run(
        to="dana@corp.com, sam@corp.com", subject="Thursday", body="Confirmed.", cc="pat@corp.com")
    check(SEEN["path"] == "/me/sendMail", "POST /me/sendMail")
    check(len(SEEN["body"]["message"]["toRecipients"]) == 2, "comma-separated recipients split")
    check(SEEN["body"]["message"]["ccRecipients"][0]["emailAddress"]["address"] == "pat@corp.com",
          "cc parsed")
    check(SEEN["body"]["saveToSentItems"] is True, "saved to Sent Items")

    print("\n=== replying threads via Graph ===")
    await SendWorkEmailTool().run(body="Works for me.", reply_to_message_id="AAMk-1")
    check(SEEN["path"] == "/me/messages/AAMk-1/reply", "uses the reply endpoint")
    check(SEEN["body"]["comment"] == "Works for me.", "reply body sent as comment")

    print("\n=== a send with no recipient is refused clearly ===")
    try:
        await SendWorkEmailTool().run(body="orphan")
        check(False, "missing recipient raises")
    except ToolError as exc:
        check("recipient is required" in str(exc), "missing recipient explained")

    print("\n=== sending is confirmation-gated with a readable preview ===")
    tool = SendWorkEmailTool()
    check(tool.confirm is True, "send_work_email requires confirmation")
    pv = tool.preview({"to": "dana@corp.com", "subject": "Thursday", "body": "Confirmed."})
    check("dana@corp.com" in pv and "Thursday" in pv and "Confirmed." in pv,
          "preview shows recipient, subject and body")
    check(SearchWorkEmailTool().confirm is False, "reading is not gated")
    check(DraftWorkEmailTool().confirm is False, "drafting is not gated")

    srv.shutdown()


asyncio.run(main())
print("\n" + ("ALL OUTLOOK CHECKS PASSED" if not fails else f"{len(fails)} FAILURES: {fails}"))
sys.exit(1 if fails else 0)
