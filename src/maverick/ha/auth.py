"""Home Assistant credentials: a long-lived token, or an IndieAuth grant.

Maverick needs a *frontend* session, not just an API credential — the renderer
installs a token bundle into ``localStorage`` and lets the Home Assistant
frontend authenticate itself with it (`src/maverick/render/dashboard.py`). A
long-lived access token works for that and is what the config file has always
carried, but getting one means the user copying a secret out of their profile
page by hand.

Home Assistant also speaks the IndieAuth redirect flow its companion apps use,
which the setup UI can drive end to end. The trade-off is that the resulting
access token lives 1800 seconds rather than ten years, so something has to
refresh it. That is what `TokenSource` is for: every caller asks for a token at
the moment it needs one instead of reading `config.token` directly.

Two details of Home Assistant's implementation shape the code below:

* A refresh grant returns a new ``access_token`` but **no** new
  ``refresh_token`` (`docs/auth_api.md`), so the stored refresh token is the
  durable credential and is kept across refreshes.
* ``client_id`` must be an http(s) URL, and a ``redirect_uri`` sharing its
  scheme and netloc is accepted without Home Assistant fetching the client_id
  page at all. Home Assistant deliberately departs from the IndieAuth spec to
  allow any *local* IP address as a client_id host, which is what makes
  Maverick's `server.base_url` usable as one.
"""

from __future__ import annotations

import abc
import asyncio
import logging
import time
from dataclasses import dataclass
from urllib.parse import urlencode, urlsplit, urlunsplit

import httpx

log = logging.getLogger(__name__)

#: Refresh this many seconds before the access token actually expires, so a
#: render that starts just under the wire does not race the expiry.
REFRESH_MARGIN = 60.0

#: What a long-lived token is worth in the frontend's bundle. Home Assistant's
#: own frontend stores an absolute expiry; a long-lived token has no meaningful
#: one, so it gets a decade.
LONG_LIVED_LIFETIME = 315_360_000.0

#: The path the setup UI serves as its IndieAuth redirect target.
CALLBACK_PATH = "/api/auth/callback"


class AuthError(RuntimeError):
    """An IndieAuth exchange with Home Assistant failed."""


@dataclass(frozen=True)
class Grant:
    """What ``/auth/token`` hands back."""

    access_token: str
    expires_in: float
    #: Empty on a refresh grant: Home Assistant only issues one at authorization.
    refresh_token: str = ""


# --------------------------------------------------------------------------- #
# Deriving the client_id and redirect_uri
# --------------------------------------------------------------------------- #

def client_id_for(base_url: str) -> str:
    """The IndieAuth client identifier for a Maverick reachable at ``base_url``.

    Home Assistant requires a path component and rejects a fragment, a
    userinfo component or a dot segment, so this normalises to scheme, netloc
    and a bare ``/``.
    """
    parts = urlsplit(base_url)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        raise AuthError(
            f"server.base_url must be an http(s) URL to link with Home Assistant, "
            f"got {base_url!r}. Set it to the address panels reach Maverick on."
        )
    return urlunsplit((parts.scheme, parts.netloc, "/", "", ""))


def redirect_uri_for(base_url: str) -> str:
    """The callback URL, on the same origin as the client_id.

    Sharing scheme and netloc with the client_id is what lets Home Assistant
    approve the redirect without fetching anything.
    """
    parts = urlsplit(client_id_for(base_url))
    return urlunsplit((parts.scheme, parts.netloc, CALLBACK_PATH, "", ""))


def authorize_url(ha_url: str, client_id: str, redirect_uri: str, state: str) -> str:
    """Where to send the browser to start the flow."""
    query = urlencode(
        {"client_id": client_id, "redirect_uri": redirect_uri, "state": state}
    )
    return f"{ha_url.rstrip('/')}/auth/authorize?{query}"


# --------------------------------------------------------------------------- #
# The token endpoint
# --------------------------------------------------------------------------- #

async def _post_token(
    ha_url: str, form: dict[str, str], *, verify_ssl: bool = True
) -> Grant:
    """POST to ``/auth/token``; the body is form-encoded, not JSON."""
    async with httpx.AsyncClient(verify=verify_ssl, timeout=30.0) as client:
        try:
            response = await client.post(
                f"{ha_url.rstrip('/')}/auth/token", data=form
            )
        except httpx.RequestError as exc:
            raise AuthError(f"Cannot reach Home Assistant at {ha_url}: {exc}") from exc

    if response.status_code >= 400:
        # Home Assistant answers with {"error": "invalid_request"} and friends.
        detail = response.text[:300]
        try:
            payload = response.json()
        except ValueError:
            payload = None
        if isinstance(payload, dict) and payload.get("error"):
            detail = str(payload.get("error_description") or payload["error"])
        raise AuthError(
            f"Home Assistant rejected the token request ({response.status_code}): {detail}"
        )

    payload = response.json()
    if not payload.get("access_token"):
        raise AuthError(f"Home Assistant returned no access token: {payload}")
    return Grant(
        access_token=payload["access_token"],
        expires_in=float(payload.get("expires_in", 1800)),
        refresh_token=payload.get("refresh_token", "") or "",
    )


async def exchange_code(
    ha_url: str, client_id: str, code: str, *, verify_ssl: bool = True
) -> Grant:
    """Trade the authorization code for an access and refresh token."""
    grant = await _post_token(
        ha_url,
        {"grant_type": "authorization_code", "code": code, "client_id": client_id},
        verify_ssl=verify_ssl,
    )
    if not grant.refresh_token:
        raise AuthError(
            "Home Assistant returned no refresh token for the authorization code, "
            "so the link could not be made durable."
        )
    return grant


async def refresh_grant(
    ha_url: str, client_id: str, refresh_token: str, *, verify_ssl: bool = True
) -> Grant:
    """Mint a fresh access token. The refresh token is unchanged and reused."""
    return await _post_token(
        ha_url,
        {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": client_id,
        },
        verify_ssl=verify_ssl,
    )


async def revoke(ha_url: str, refresh_token: str, *, verify_ssl: bool = True) -> None:
    """Invalidate a refresh token. Home Assistant always answers 200."""
    async with httpx.AsyncClient(verify=verify_ssl, timeout=30.0) as client:
        try:
            await client.post(
                f"{ha_url.rstrip('/')}/auth/revoke", data={"token": refresh_token}
            )
        except httpx.RequestError as exc:  # pragma: no cover - best effort
            log.warning("could not revoke the refresh token: %s", exc)


# --------------------------------------------------------------------------- #
# Token sources
# --------------------------------------------------------------------------- #

class TokenSource(abc.ABC):
    """Something that can produce a currently-valid access token."""

    #: Named in the UI and in `maverick check`.
    kind: str = "none"

    @abc.abstractmethod
    async def token(self) -> str:
        """Return an access token that is valid right now."""

    async def bundle_fields(self) -> dict[str, object]:
        """The credential half of the frontend's ``hassTokens`` bundle.

        Split out because the renderer owns the rest of the bundle (the URL it
        must match) while only the token source knows how long the token lasts
        and whether the frontend can refresh it on its own.
        """
        return {
            "access_token": await self.token(),
            "expires_in": LONG_LIVED_LIFETIME,
            "expires": int((time.time() + LONG_LIVED_LIFETIME) * 1000),
            "refresh_token": "",
            "clientId": None,
        }


class StaticToken(TokenSource):
    """A long-lived access token from the user's profile page."""

    kind = "long-lived token"

    def __init__(self, token: str) -> None:
        self._token = token

    async def token(self) -> str:
        return self._token


class RefreshingToken(TokenSource):
    """An IndieAuth grant, refreshed as it expires.

    One lock, so a burst of concurrent renders triggers a single refresh rather
    than one per caller.
    """

    kind = "linked account"

    def __init__(
        self,
        ha_url: str,
        client_id: str,
        refresh_token: str,
        *,
        verify_ssl: bool = True,
        access_token: str = "",
        expires_at: float = 0.0,
    ) -> None:
        self._ha_url = ha_url
        self._client_id = client_id
        self._refresh_token = refresh_token
        self._verify_ssl = verify_ssl
        self._access_token = access_token
        self._expires_at = expires_at
        self._lock = asyncio.Lock()

    @property
    def refresh_token(self) -> str:
        return self._refresh_token

    @property
    def client_id(self) -> str:
        return self._client_id

    async def token(self) -> str:
        if self._access_token and time.time() < self._expires_at - REFRESH_MARGIN:
            return self._access_token
        async with self._lock:
            # Another caller may have refreshed while this one waited.
            if self._access_token and time.time() < self._expires_at - REFRESH_MARGIN:
                return self._access_token
            grant = await refresh_grant(
                self._ha_url,
                self._client_id,
                self._refresh_token,
                verify_ssl=self._verify_ssl,
            )
            self._access_token = grant.access_token
            self._expires_at = time.time() + grant.expires_in
            log.debug("refreshed the Home Assistant access token")
            return self._access_token

    async def bundle_fields(self) -> dict[str, object]:
        """Hand the frontend the refresh token too.

        With a ``clientId`` and a ``refresh_token`` in the bundle the frontend
        renews the session itself, so a page left open past the 30-minute
        expiry does not bounce to the login screen mid-render.
        """
        access = await self.token()
        remaining = max(self._expires_at - time.time(), 0.0)
        return {
            "access_token": access,
            "expires_in": remaining,
            "expires": int(self._expires_at * 1000),
            "refresh_token": self._refresh_token,
            "clientId": self._client_id,
        }


def build_token_source(config: object) -> TokenSource | None:
    """Pick a credential from a `HomeAssistantConfig`.

    A linked account wins over a pasted token: if both are present the user has
    been through the setup UI more recently than they edited the file.
    """
    refresh_token = getattr(config, "refresh_token", "") or ""
    client_id = getattr(config, "client_id", "") or ""
    token = getattr(config, "token", "") or ""

    if refresh_token and client_id:
        return RefreshingToken(
            getattr(config, "url", ""),
            client_id,
            refresh_token,
            verify_ssl=bool(getattr(config, "verify_ssl", True)),
        )
    if refresh_token and not client_id:
        log.warning(
            "home_assistant.refresh_token is set but client_id is not; both are "
            "needed for a linked account. Falling back to home_assistant.token."
        )
    if token:
        return StaticToken(token)
    return None


__all__ = [
    "AuthError",
    "CALLBACK_PATH",
    "Grant",
    "RefreshingToken",
    "StaticToken",
    "TokenSource",
    "authorize_url",
    "build_token_source",
    "client_id_for",
    "exchange_code",
    "redirect_uri_for",
    "refresh_grant",
    "revoke",
]
