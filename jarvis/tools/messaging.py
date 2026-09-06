"""Text messaging -- deliberately pluggable, because there is no good free SMS
API on Windows.

Three provider slots exist. Pick one with JARVIS_SMS_PROVIDER in .env:

  none   (default) -- Jarvis tells the user texting is not wired up.
  ntfy   -- free, no account. Push notifications to YOUR OWN phone via
            ntfy.sh. This is not SMS and cannot message other people, but it
            covers the common "remind me" / "ping me when" case at zero cost.
  twilio -- real SMS to anyone. ~$1.15/month for a number plus ~$0.0079 per
            message. The only option here that actually sends a text to a
            third party.

To add a provider, subclass Provider and register it in _PROVIDERS.
"""
from __future__ import annotations

import os

import httpx2 as httpx

from .base import Tool, ToolError

_TIMEOUT = httpx.Timeout(15.0, connect=6.0)


class Provider:
    """Interface every messaging backend implements."""

    name = "none"

    def available(self) -> bool:
        return False

    async def send(self, to: str, message: str) -> str:
        raise ToolError(
            "Text messaging is not configured. Set JARVIS_SMS_PROVIDER in .env "
            "to 'ntfy' (free, notifies your own phone) or 'twilio' (real SMS). "
            "See jarvis/README.md, 'Texting'."
        )


class NtfyProvider(Provider):
    """Free push to the user's own devices. Send-only, self-directed."""

    name = "ntfy"

    def __init__(self) -> None:
        self.topic = os.getenv("NTFY_TOPIC", "")
        self.server = os.getenv("NTFY_SERVER", "https://ntfy.sh").rstrip("/")

    def available(self) -> bool:
        return bool(self.topic)

    async def send(self, to: str, message: str) -> str:
        # `to` is ignored: ntfy can only reach the user's own subscribed topic.
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(
                f"{self.server}/{self.topic}",
                content=message.encode("utf-8"),
                headers={"Title": "Jarvis"},
            )
            if resp.status_code >= 400:
                raise ToolError(f"ntfy push failed ({resp.status_code}): {resp.text[:200]}")
        return "Pushed to your phone via ntfy."


class TwilioProvider(Provider):
    """Real SMS. Costs money -- roughly a cent a message."""

    name = "twilio"

    def __init__(self) -> None:
        self.sid = os.getenv("TWILIO_ACCOUNT_SID", "")
        self.token = os.getenv("TWILIO_AUTH_TOKEN", "")
        self.from_number = os.getenv("TWILIO_FROM_NUMBER", "")

    def available(self) -> bool:
        return bool(self.sid and self.token and self.from_number)

    async def send(self, to: str, message: str) -> str:
        if not to:
            raise ToolError("A destination phone number is required for SMS.")
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(
                f"https://api.twilio.com/2010-04-01/Accounts/{self.sid}/Messages.json",
                data={"To": to, "From": self.from_number, "Body": message},
                auth=(self.sid, self.token),
            )
        if resp.status_code >= 400:
            detail = ""
            try:
                detail = resp.json().get("message", "")
            except Exception:
                detail = resp.text[:200]
            raise ToolError(f"Twilio error {resp.status_code}: {detail}")
        return f"Text sent to {to}."


_PROVIDERS = {"none": Provider, "ntfy": NtfyProvider, "twilio": TwilioProvider}


def _provider() -> Provider:
    key = os.getenv("JARVIS_SMS_PROVIDER", "none").strip().lower()
    return _PROVIDERS.get(key, Provider)()


class SendTextTool(Tool):
    name = "send_text"
    description = """
    Send a text message. Use for short, urgent, time-sensitive things -- for
    anything longer or more formal, prefer email. The user confirms before it
    sends.
    """
    schema = {
        "type": "object",
        "properties": {
            "to": {
                "type": "string",
                "description": "Phone number in E.164 form, e.g. +15551234567. "
                "Ignored when the provider can only reach the user's own devices.",
            },
            "message": {"type": "string", "description": "Body. Keep it under 300 characters."},
        },
        "required": ["message"],
    }
    confirm = True

    def preview(self, args: dict) -> str:
        dest = args.get("to") or "your phone"
        return f"Send text to {dest}:\n\n{args.get('message')}"

    def available(self) -> bool:
        # Always offered, so Jarvis can explain *why* it cannot text rather
        # than pretending the capability does not exist.
        return True

    async def run(self, message: str, to: str = "") -> str:
        return await _provider().send(to, message)
