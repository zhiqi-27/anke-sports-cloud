"""Storage-independent OAuth policy shared by both adapters."""

from contextvars import ContextVar
from urllib.parse import parse_qs, urlsplit
from pydantic import AnyHttpUrl
from app.config import settings

SCOPES = ["calendar:read", "calendar:write", "feed:read"]
TOKEN_RESOURCE: ContextVar[str | None] = ContextVar("oauth_token_resource", default=None)
ACCESS_SECONDS = 900
GRANT_SECONDS = 7 * 86400


def issuer():
    return str(AnyHttpUrl(settings().public_url))


def resource(kind="mcp"):
    return settings().public_url.rstrip("/") + ("/mcp" if kind == "mcp" else "/api/v1")


def redirect_allowed(value):
    parsed = urlsplit(str(value))
    if parsed.username or parsed.password or parsed.fragment or not parsed.hostname:
        return False
    if set(parse_qs(parsed.query)).intersection({"code", "state", "error", "iss"}):
        return False
    if len(str(value)) > 2000 or "\\" in str(value):
        return False
    if parsed.scheme == "http":
        return parsed.hostname in {"localhost", "127.0.0.1", "::1"}
    return parsed.scheme == "https"
