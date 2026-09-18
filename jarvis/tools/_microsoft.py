"""Microsoft Graph: auth (MSAL device-code flow) and the shared tool base."""
from __future__ import annotations

import httpx2 as httpx
import msal

from core.config import cfg

from .base import Tool, ToolError

# Delegated scopes. Keep this list minimal and identical between setup and
# runtime -- MSAL keys its cache by scope set.
SCOPES = [
    "User.Read",
    "Calendars.ReadWrite",
    "OnlineMeetings.ReadWrite",
    "Mail.ReadWrite",   # read the mailbox and save drafts
    "Mail.Send",        # send; deliberately separate from ReadWrite
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


_TIMEOUT = httpx.Timeout(30.0, connect=8.0)


class GraphTool(Tool):
    """Base for every tool that talks to Microsoft Graph."""

    def available(self) -> bool:
        return cfg.microsoft_enabled

    async def _get(self, path: str, params: dict | None = None) -> dict:
        return await self._call("GET", path, params=params)

    async def _post(self, path: str, body: dict | None = None) -> dict:
        return await self._call("POST", path, json=body or {})

    async def _call(self, method: str, path: str, **kw) -> dict:
        hdrs = headers()
        # Ask Graph to return times already converted to the user's timezone.
        hdrs["Prefer"] = f'outlook.timezone="{cfg.timezone}"'
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                resp = await client.request(method, f"{GRAPH}{path}", headers=hdrs, **kw)
        except httpx.HTTPError as exc:
            raise ToolError(f"Could not reach Microsoft Graph: {exc}") from exc

        if resp.status_code == 403:
            raise ToolError(
                "Microsoft refused that (403). The app registration is probably missing a "
                "permission -- re-run setup_microsoft.py to consent to the current scopes."
            )
        if resp.status_code >= 400:
            detail = ""
            try:
                detail = resp.json().get("error", {}).get("message", "")
            except Exception:
                detail = resp.text[:300]
            raise ToolError(f"Graph {resp.status_code}: {detail}")
        return resp.json() if resp.content else {}
