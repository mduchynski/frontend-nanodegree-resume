"""Gmail: search, read, draft, send."""
from __future__ import annotations

import asyncio
import base64
from email.message import EmailMessage

from core.config import cfg

from ._google import gmail
from .base import Tool


def _header(payload: dict, name: str) -> str:
    for h in payload.get("headers", []):
        if h.get("name", "").lower() == name.lower():
            return h.get("value", "")
    return ""


def _body_text(payload: dict) -> str:
    """Depth-first walk for the best text part of a MIME tree."""
    if payload.get("mimeType") == "text/plain":
        data = payload.get("body", {}).get("data")
        if data:
            return base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")
    for part in payload.get("parts", []) or []:
        text = _body_text(part)
        if text:
            return text
    # No plain-text part -- fall back to stripped HTML.
    if payload.get("mimeType") == "text/html":
        data = payload.get("body", {}).get("data")
        if data:
            from selectolax.parser import HTMLParser

            html = base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")
            return HTMLParser(html).text(separator="\n", strip=True)
    return ""


class GoogleTool(Tool):
    def available(self) -> bool:
        return cfg.google_enabled


class SearchEmailTool(GoogleTool):
    name = "search_email"
    description = """
    Search the user's Gmail. Accepts full Gmail query syntax, e.g.
    'is:unread', 'from:boss@corp.com', 'subject:invoice newer_than:7d',
    'has:attachment'. Returns a compact list -- use `read_email` with a message
    id to get the full body.
    """
    schema = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Gmail search query."},
            "max_results": {"type": "integer", "description": "1-25, default 10."},
        },
        "required": ["query"],
    }

    async def run(self, query: str, max_results: int = 10) -> str:
        n = max(1, min(int(max_results or 10), 25))
        return await asyncio.to_thread(self._search, query, n)

    def _search(self, query: str, n: int) -> str:
        svc = gmail()
        listing = (
            svc.users().messages().list(userId="me", q=query, maxResults=n).execute()
        )
        ids = [m["id"] for m in listing.get("messages", [])]
        if not ids:
            return f"No messages match {query!r}."

        out = [f"{len(ids)} message(s) matching {query!r}:", ""]
        for mid in ids:
            msg = (
                svc.users()
                .messages()
                .get(
                    userId="me",
                    id=mid,
                    format="metadata",
                    metadataHeaders=["From", "Subject", "Date"],
                )
                .execute()
            )
            p = msg.get("payload", {})
            unread = "UNREAD" in msg.get("labelIds", [])
            out.append(
                f"[id: {mid}]{' (unread)' if unread else ''}\n"
                f"  From:    {_header(p, 'From')}\n"
                f"  Subject: {_header(p, 'Subject')}\n"
                f"  Date:    {_header(p, 'Date')}\n"
                f"  {msg.get('snippet', '')[:180]}"
            )
        return "\n".join(out)


class ReadEmailTool(GoogleTool):
    name = "read_email"
    description = "Read one email in full by its message id (from `search_email`)."
    schema = {
        "type": "object",
        "properties": {"message_id": {"type": "string"}},
        "required": ["message_id"],
    }

    async def run(self, message_id: str) -> str:
        return await asyncio.to_thread(self._read, message_id)

    def _read(self, message_id: str) -> str:
        msg = (
            gmail()
            .users()
            .messages()
            .get(userId="me", id=message_id, format="full")
            .execute()
        )
        p = msg.get("payload", {})
        body = _body_text(p) or msg.get("snippet", "")
        if len(body) > 8000:
            body = body[:8000] + "\n\n[truncated]"
        return (
            f"From:    {_header(p, 'From')}\n"
            f"To:      {_header(p, 'To')}\n"
            f"Subject: {_header(p, 'Subject')}\n"
            f"Date:    {_header(p, 'Date')}\n"
            f"Thread:  {msg.get('threadId')}\n\n{body}"
        )


class SendEmailTool(GoogleTool):
    name = "send_email"
    description = """
    Send an email from the user's Gmail account. The user is always asked to
    confirm before this actually sends. Write the body in the user's voice:
    plain, direct, no filler. If you are unsure of the recipient's address,
    search their mail for it first rather than guessing.
    """
    schema = {
        "type": "object",
        "properties": {
            "to": {"type": "string", "description": "Recipient address(es), comma-separated."},
            "subject": {"type": "string"},
            "body": {"type": "string", "description": "Plain-text body."},
            "cc": {"type": "string"},
            "reply_to_message_id": {
                "type": "string",
                "description": "Message id being replied to, to keep threading intact.",
            },
        },
        "required": ["to", "subject", "body"],
    }
    confirm = True

    def preview(self, args: dict) -> str:
        body = args.get("body", "")
        preview = body if len(body) <= 300 else body[:300] + "..."
        cc = f"\nCc: {args['cc']}" if args.get("cc") else ""
        return (
            f"Send email\nTo: {args.get('to')}{cc}\n"
            f"Subject: {args.get('subject')}\n\n{preview}"
        )

    async def run(
        self,
        to: str,
        subject: str,
        body: str,
        cc: str | None = None,
        reply_to_message_id: str | None = None,
    ) -> str:
        return await asyncio.to_thread(self._send, to, subject, body, cc, reply_to_message_id)

    def _send(self, to, subject, body, cc, reply_to_message_id) -> str:
        svc = gmail()
        mime = EmailMessage()
        mime["To"] = to
        mime["Subject"] = subject
        if cc:
            mime["Cc"] = cc
        mime.set_content(body)

        payload: dict = {}
        if reply_to_message_id:
            try:
                original = (
                    svc.users()
                    .messages()
                    .get(
                        userId="me",
                        id=reply_to_message_id,
                        format="metadata",
                        metadataHeaders=["Message-ID", "References"],
                    )
                    .execute()
                )
                op = original.get("payload", {})
                parent = _header(op, "Message-ID")
                if parent:
                    mime["In-Reply-To"] = parent
                    refs = _header(op, "References")
                    mime["References"] = f"{refs} {parent}".strip()
                payload["threadId"] = original.get("threadId")
            except Exception:
                # Threading is a nicety; never block the send on it.
                pass

        payload["raw"] = base64.urlsafe_b64encode(mime.as_bytes()).decode()
        sent = svc.users().messages().send(userId="me", body=payload).execute()
        return f"Sent to {to} (id {sent.get('id')})."


class DraftEmailTool(GoogleTool):
    name = "draft_email"
    description = """
    Save an email as a Gmail draft without sending it. Prefer this over
    `send_email` when the user says 'draft', 'write up', or seems to want to
    review before it goes out.
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
        return await asyncio.to_thread(self._draft, to, subject, body)

    def _draft(self, to: str, subject: str, body: str) -> str:
        mime = EmailMessage()
        mime["To"] = to
        mime["Subject"] = subject
        mime.set_content(body)
        raw = base64.urlsafe_b64encode(mime.as_bytes()).decode()
        draft = (
            gmail()
            .users()
            .drafts()
            .create(userId="me", body={"message": {"raw": raw}})
            .execute()
        )
        return f"Draft saved for {to} (id {draft.get('id')}). Not sent."
