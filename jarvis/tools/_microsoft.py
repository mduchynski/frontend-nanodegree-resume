"""Microsoft Graph auth (MSAL device-code flow with a cached refresh token)."""
from __future__ import annotations

import msal

from core.config import cfg

# Delegated scopes. Keep this list minimal and identical between setup and
# runtime -- MSAL keys its cache by scope set.
SCOPES = [
    "User.Read",
    "Calendars.ReadWrite",
    "OnlineMeetings.ReadWrite",
]

GRAPH = "https://graph.microsoft.com/v1.0"


def _cache() -> msal.SerializableTokenCache:
    cache = msal.SerializableTokenCache()
    if cfg.ms_token_file.exists():
        cache.deserialize(cfg.ms_token_file.read_text())
    return cache


def _save(cache: msal.SerializableTokenCache) -> None:
    if cache.has_state_changed:
        cfg.ms_token_file.write_text(cache.serialize())


def app(cache: msal.SerializableTokenCache | None = None) -> msal.PublicClientApplication:
    if not cfg.ms_client_id:
        raise RuntimeError("MS_CLIENT_ID is not set. See README step 4.")
    return msal.PublicClientApplication(
        cfg.ms_client_id,
        authority=f"https://login.microsoftonline.com/{cfg.ms_tenant_id}",
        token_cache=cache if cache is not None else _cache(),
    )


def access_token() -> str:
    """A valid bearer token, refreshed silently from the cache."""
    cache = _cache()
    client = app(cache)
    accounts = client.get_accounts()
    if not accounts:
        raise RuntimeError(
            "Microsoft is not authorised yet. Run `python setup_microsoft.py` first."
        )
    result = client.acquire_token_silent(SCOPES, account=accounts[0])
    _save(cache)
    if not result or "access_token" not in result:
        raise RuntimeError(
            "Microsoft token could not be refreshed. Re-run `python setup_microsoft.py`."
        )
    return result["access_token"]


def headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {access_token()}",
        "Content-Type": "application/json",
    }
