"""Shared Google OAuth plumbing for the Gmail and Google Calendar tools."""
from __future__ import annotations

import functools

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

from core.config import cfg

SCOPES = [
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/calendar",
]


def credentials() -> Credentials:
    if not cfg.google_token_file.exists():
        raise RuntimeError(
            "Google is not authorised yet. Run `python setup_google.py` first."
        )
    creds = Credentials.from_authorized_user_file(str(cfg.google_token_file), SCOPES)
    if not creds.valid:
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
            cfg.google_token_file.write_text(creds.to_json())
        else:
            raise RuntimeError(
                "Google credentials are invalid. Re-run `python setup_google.py`."
            )
    return creds


@functools.lru_cache(maxsize=2)
def service(api: str, version: str):
    """Cached API client. Credentials refresh themselves in place, so caching
    the client is safe."""
    return build(api, version, credentials=credentials(), cache_discovery=False)


def gmail():
    return service("gmail", "v1")


def gcal():
    return service("calendar", "v3")
