"""
MS Graph OAuth2 token management via MSAL device code flow.

Token cache is stored at /agent/ms_token_cache.bin, persisted via the
agent-identity volume that is already mounted at /agent.

Usage:
  token = get_ms_token()          # raises if not authenticated
  flow  = init_device_flow()      # start device code auth
  complete_device_flow(flow)      # block until user completes auth
"""

import os

import msal

_MS_SCOPES = ["Calendars.ReadWrite"]
_CACHE_PATH = "/agent/ms_token_cache.bin"


def _load_cache() -> msal.SerializableTokenCache:
    cache = msal.SerializableTokenCache()
    if os.path.exists(_CACHE_PATH):
        with open(_CACHE_PATH) as f:
            cache.deserialize(f.read())
    return cache


def _save_cache(cache: msal.SerializableTokenCache) -> None:
    if cache.has_state_changed:
        with open(_CACHE_PATH, "w") as f:
            f.write(cache.serialize())


def _get_app() -> msal.PublicClientApplication:
    client_id = os.environ.get("MS_GRAPH_CLIENT_ID", "")
    if not client_id:
        raise RuntimeError("MS_GRAPH_CLIENT_ID not set")
    cache = _load_cache()
    return msal.PublicClientApplication(
        client_id,
        authority="https://login.microsoftonline.com/consumers",
        token_cache=cache,
    )


def get_ms_token() -> str:
    """Return a valid access token, auto-refreshing via cache.

    Raises RuntimeError if not authenticated (run calendar-auth first).
    """
    app = _get_app()
    accounts = app.get_accounts()
    if accounts:
        result = app.acquire_token_silent(_MS_SCOPES, account=accounts[0])
        if result and "access_token" in result:
            _save_cache(app.token_cache)
            return result["access_token"]
    raise RuntimeError("MS Graph not authenticated — run: agent calendar-auth")


def init_device_flow() -> dict:
    """Start device code flow.

    Returns the flow dict which contains 'message' (shown to user),
    'verification_uri', and 'user_code'.
    """
    app = _get_app()
    flow = app.initiate_device_flow(scopes=_MS_SCOPES)
    if "error" in flow:
        raise RuntimeError(f"Device flow error: {flow.get('error_description')}")
    return flow


def complete_device_flow(flow: dict) -> None:
    """Block until the user completes device code auth. Saves token to cache."""
    app = _get_app()
    result = app.acquire_token_by_device_flow(flow)
    if "access_token" not in result:
        raise RuntimeError(f"Auth failed: {result.get('error_description')}")
    _save_cache(app.token_cache)
