#!/usr/bin/env python3
"""One-time Microsoft authorisation for the Teams / Outlook calendar.

Uses the device-code flow, so it works even when the work account enforces MFA
or conditional access. Full walkthrough in README.md, step 4.
"""
from __future__ import annotations

import sys

import msal

from core.config import cfg
from tools._microsoft import SCOPES, app


def main() -> int:
    if not cfg.ms_client_id:
        print("MS_CLIENT_ID is not set in .env. See README.md step 4.")
        return 1

    cache = msal.SerializableTokenCache()
    if cfg.ms_token_file.exists():
        cache.deserialize(cfg.ms_token_file.read_text())
    client = app(cache)

    flow = client.initiate_device_flow(scopes=SCOPES)
    if "user_code" not in flow:
        print("Could not start device flow:", flow.get("error_description", flow))
        return 1

    print("\n" + "=" * 58)
    print(flow["message"])
    print("=" * 58 + "\n")
    print("Waiting for you to finish signing in...")

    result = client.acquire_token_by_device_flow(flow)
    if "access_token" not in result:
        print("Sign-in failed:", result.get("error_description", result))
        return 1

    cfg.ms_token_file.write_text(cache.serialize())
    name = (result.get("id_token_claims") or {}).get("name", "your account")
    print(f"\nDone. Signed in as {name}.")
    print(f"Token cached at {cfg.ms_token_file} -- keep it private.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
