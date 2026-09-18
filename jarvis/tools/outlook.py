"""Outlook mail over Microsoft Graph -- the work mailbox.

Kept separate from the Gmail tools so both can be connected at once: personal
mail in Gmail, work mail here. The tool names say which is which so the model
does not have to guess.

One Graph constraint shapes the search tool: `$search` cannot be combined with
`$filter` or `$orderby` on messages -- the request is rejected. So a free-text
query uses `$search` alone (relevance-ordered, no sorting available), and a
structured request uses `$filter` with `$orderby` instead. Never both.
"""
from __future__ import annotations

import re

from ._microsoft import GraphTool
from .base import ToolError

_FIELDS = "id,subject,from,toRecipients,receivedDateTime,isRead,hasAttachments,bodyPreview"


def _addr(entry: dict) -> str:
    email = (entry or {}).get("emailAddress", {}) or {}
    name = email.get("name", "")
    address = email.get("address", "")
    if name and address and name != address:
        return f"{name} <{address}>"
    return address or name or "(unknown)"


def _recipients(values: list[str]) -> list[dict]:
    out = []
    for value in values:
        for piece in re.split(r"[;,]", value or ""):
            piece = piece.strip()
            if piece:
                out.append({"emailAddress": {"address": piece}})
    return out


def _html_to_text(html: str) -> str:
    from selectolax.parser import HTMLParser

    tree = HTMLParser(html or "")
    for tag in ("script", "style", "head"):
        for node in tree.css(tag):
            node.decompose()
    lines = [ln.strip() for ln in (tree.body.text(separator="\n") if tree.body else "").splitlines()]
    return "\n".join(ln for ln in lines if ln)


def _summarise(msg: dict) -> str:
    when = (msg.get("receivedDateTime") or "")[:16].replace("T", " ")
    flags = []
    if not msg.get("isRead"):
        flags.append("unread")
    if msg.get("hasAttachments"):
        flags.append("attachment")
    tag = f" ({', '.join(flags)})" if flags else ""
    return (
        f"[id: {msg.get('id', '')[:40]}...]{tag}\n"
        f"  From:    {_addr(msg.get('from'))}\n"
        f"  Subject: {msg.get('subject') or '(no subject)'}\n"
        f"  When:    {when}\n"
        f"  {(msg.get('bodyPreview') or '')[:180]}"
    )


class SearchWorkEmailTool(GraphTool):
    name = "search_work_email"
    description = """
    Search the user's work mailbox (Outlook / Microsoft 365).

    Use `query` for free text. Outlook search syntax works:
    'from:dana', 'subject:invoice', 'hasAttachments:true', or plain words.
    Use `unread_only` on its own to see what is new -- do not combine it with
    `query`, because Graph rejects a search and a filter in the same request;
    if both are given, the query wins.

    Returns a compact list. Follow up with `read_work_email` for a full message.
    """
    schema = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Free-text search. Outlook syntax allowed."},
            "unread_only": {
                "type": "boolean",
                "description": "List unread mail, newest first. Ignored when `query` is set.",
            },
            "max_results": {"type": "integer", "description": "1-25, default 10."},
        },
    }

    async def run(
        self,
        query: str | None = None,
        unread_only: bool = False,
        max_results: int = 10,
    ) -> str:
        top = max(1, min(int(max_results or 10), 25))
        params: dict = {"$select": _FIELDS, "$top": str(top)}

        if query:
            # $search is mutually exclusive with $filter and $orderby.
            params["$search"] = f'"{query}"'
            label = f"matching {query!r}"
        else:
            if unread_only:
                params["$filter"] = "isRead eq false"
                label = "unread"
            else:
                label = "most recent"
            params["$orderby"] = "receivedDateTime desc"

        data = await self._get("/me/messages", params=params)
        messages = data.get("value", [])
        if not messages:
            return f"No {label} messages in the work mailbox."
        return f"{len(messages)} {label} message(s):\n\n" + "\n\n".join(
            _summarise(m) for m in messages
        )


class ReadWorkEmailTool(GraphTool):
    name = "read_work_email"
    description = "Read one work email in full by its message id (from `search_work_email`)."
    schema = {
        "type": "object",
        "properties": {"message_id": {"type": "string"}},
        "required": ["message_id"],
    }

    async def run(self, message_id: str) -> str:
        msg = await self._get(
            f"/me/messages/{message_id}",
            params={"$select": "id,subject,from,toRecipients,ccRecipients,receivedDateTime,body"},
        )
        body = msg.get("body") or {}
        text = body.get("content", "")
        if (body.get("contentType") or "").lower() == "html":
            text = _html_to_text(text)
        if len(text) > 8000:
            text = text[:8000] + "\n\n[truncated]"

        to = ", ".join(_addr(r) for r in (msg.get("toRecipients") or [])) or "(none)"
        cc = ", ".join(_addr(r) for r in (msg.get("ccRecipients") or []))
        header = (
            f"From:    {_addr(msg.get('from'))}\n"
            f"To:      {to}\n"
            + (f"Cc:      {cc}\n" if cc else "")
            + f"Subject: {msg.get('subject') or '(no subject)'}\n"
            f"When:    {(msg.get('receivedDateTime') or '')[:16].replace('T', ' ')}\n"
        )
        return header + "\n" + text


class DraftWorkEmailTool(GraphTool):
    name = "draft_work_email"
    description = """
    Save a work email as an Outlook draft without sending it. Prefer this over
    `send_work_email` when the user says 'draft' or 'write up', or seems to
    want to review before it goes out.
    """
    schema = {
        "type": "object",
        "properties": {
            "to": {"type": "string", "description": "Recipient address(es), comma-separated."},
            "subject": {"type": "string"},
            "body": {"type": "string", "description": "Plain-text body."},
        },
        "required": ["to", "subject", "body"],
    }

    async def run(self, to: str, subject: str, body: str) -> str:
        created = await self._post(
            "/me/messages",
            {
                "subject": subject,
                "body": {"contentType": "Text", "content": body},
                "toRecipients": _recipients([to]),
            },
        )
        return f"Draft saved in Outlook for {to} (id {created.get('id', '')[:40]}...). Not sent."


class SendWorkEmailTool(GraphTool):
    name = "send_work_email"
    description = """
    Send an email from the user's work mailbox. The user is asked to confirm
    before it actually sends.

    Write in the user's voice: plain, direct, no filler. If unsure of an
    address, search the mailbox for it rather than guessing. To reply to
    something, pass `reply_to_message_id` so it threads properly -- then `body`
    is your reply text and subject and recipients are handled for you.
    """
    schema = {
        "type": "object",
        "properties": {
            "to": {"type": "string", "description": "Recipient address(es). Omit when replying."},
            "subject": {"type": "string", "description": "Omit when replying."},
            "body": {"type": "string"},
            "cc": {"type": "string"},
            "reply_to_message_id": {
                "type": "string",
                "description": "Id of the message being replied to, from search_work_email.",
            },
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

    async def run(
        self,
        body: str,
        to: str | None = None,
        subject: str | None = None,
        cc: str | None = None,
        reply_to_message_id: str | None = None,
    ) -> str:
        if reply_to_message_id:
            # Graph threads the reply and fills in the recipients itself.
            await self._post(
                f"/me/messages/{reply_to_message_id}/reply",
                {"comment": body},
            )
            return "Reply sent from the work mailbox."

        if not to:
            raise ToolError("A recipient is required unless you are replying to a message.")

        message: dict = {
            "subject": subject or "(no subject)",
            "body": {"contentType": "Text", "content": body},
            "toRecipients": _recipients([to]),
        }
        if cc:
            message["ccRecipients"] = _recipients([cc])

        await self._post("/me/sendMail", {"message": message, "saveToSentItems": True})
        return f"Sent to {to} from the work mailbox."
