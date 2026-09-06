#!/usr/bin/env python3
"""One-time Google authorisation for Gmail + Google Calendar.

Prerequisite: download an OAuth client (type "Desktop app") from the Google
Cloud console and save it as credentials_google.json next to this file.
Full walkthrough in README.md, step 3.
"""
from __future__ import annotations

import sys

from google_auth_oauthlib.flow import InstalledAppFlow

from core.config import cfg
from tools._google import SCOPES


def main() -> int:
    if not cfg.google_credentials_file.exists():
        print(f"Missing {cfg.google_credentials_file}.")
        print("Download an OAuth 'Desktop app' client from the Google Cloud")
        print("console and save it there. See README.md step 3.")
        return 1

    flow = InstalledAppFlow.from_client_secrets_file(
        str(cfg.google_credentials_file), SCOPES
    )
    print("A browser window will open. Approve access for Gmail and Calendar.\n")
    creds = flow.run_local_server(port=0, prompt="consent")
    cfg.google_token_file.write_text(creds.to_json())
    print(f"\nDone. Token saved to {cfg.google_token_file}")
    print("Keep that file private -- it grants access to your mailbox.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
