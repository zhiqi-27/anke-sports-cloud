"""Persistent OAuth provider for the official MCP SDK and the Chrome client.

Firebase establishes the owner's identity in the Web consent flow. Downstream
clients receive opaque Anke Sports tokens, bound to one resource and grant.
"""

import re
import secrets
import time
from contextvars import ContextVar
from urllib.parse import parse_qs, urlsplit

from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    AuthorizeError,
    RefreshToken,
    RegistrationError,
    TokenError,
    construct_redirect_uri,
)
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from pydantic import AnyHttpUrl
from sqlalchemy import delete, func, select, update

from app.config import settings
from app.db import OAuthClient, OAuthGrant, OAuthRequest, OAuthTokenRecord, SessionLocal, User, uid
from app.security import digest, problem

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


def valid_grant(db, grant):
    user = db.get(User, grant.owner_id) if grant else None
    return bool(
        grant
        and not grant.revoked
        and grant.expires_at > time.time()
        and grant.issuer == issuer()
        and user
        and not user.deleted
    )


def token_info(db, raw, kind):
    if not raw.startswith("as_at_" if kind == "access" else "as_rt_") or len(raw) > 200:
        return None, None
    row = db.get(OAuthTokenRecord, digest(raw))
    grant = db.get(OAuthGrant, row.grant_id) if row else None
    if not row or row.kind != kind or row.expires_at <= time.time() or not valid_grant(db, grant):
        return None, None
    return row, grant


def verify_access(db, raw, audience):
    row, grant = token_info(db, raw, "access")
    if not row or row.used or grant.resource != audience:
        return None
    return AccessToken(
        token=raw,
        client_id=grant.client_id,
        scopes=row.scopes,
        expires_at=row.expires_at,
        resource=grant.resource,
        subject=grant.owner_id,
        claims={"iss": grant.issuer, "grant_id": grant.id},
    )


def issue_tokens(db, grant, scopes):
    current = int(time.time())
    access, refresh = "as_at_" + secrets.token_urlsafe(32), "as_rt_" + secrets.token_urlsafe(32)
    db.add_all(
        [
            OAuthTokenRecord(
                token_hash=digest(access),
                grant_id=grant.id,
                kind="access",
                scopes=scopes,
                expires_at=min(current + ACCESS_SECONDS, grant.expires_at),
            ),
            OAuthTokenRecord(
                token_hash=digest(refresh),
                grant_id=grant.id,
                kind="refresh",
                scopes=scopes,
                expires_at=grant.expires_at,
            ),
        ]
    )
    return OAuthToken(
        access_token=access,
        token_type="Bearer",
        expires_in=min(ACCESS_SECONDS, grant.expires_at - current),
        refresh_token=refresh,
        scope=" ".join(scopes),
    )


class SportsOAuthProvider:
    async def get_client(self, client_id):
        if not client_id or len(client_id) > 200:
            return None
        with SessionLocal() as db:
            row = db.get(OAuthClient, client_id)
            if not row:
                return None
            return OAuthClientInformationFull.model_validate_json(
                settings().cipher().decrypt(row.metadata_ciphertext.encode())
            )

    async def register_client(self, client_info):
        if (
            not client_info.redirect_uris
            or len(client_info.redirect_uris) > 8
            or not all(redirect_allowed(uri) for uri in client_info.redirect_uris)
        ):
            raise RegistrationError("invalid_redirect_uri", "HTTPS or loopback redirect URIs are required")
        if len(client_info.client_name or "") > 100:
            raise RegistrationError("invalid_client_metadata", "Client name is too long")
        if client_info.token_endpoint_auth_method not in {
            "none",
            "client_secret_post",
            "client_secret_basic",
        }:
            raise RegistrationError("invalid_client_metadata", "Unsupported client authentication")
        if not set((client_info.scope or "").split()).issubset(SCOPES):
            raise RegistrationError("invalid_client_metadata", "Unsupported scopes")
        with SessionLocal() as db:
            count = db.scalar(
                select(func.count())
                .select_from(OAuthClient)
                .where(OAuthClient.created_at > int(time.time()) - 86400)
            )
            if count >= 500:
                raise RegistrationError("invalid_client_metadata", "Registration limit reached")
            db.add(
                OAuthClient(
                    id=client_info.client_id,
                    name=client_info.client_name or "External client",
                    metadata_ciphertext=settings()
                    .cipher()
                    .encrypt(client_info.model_dump_json().encode())
                    .decode(),
                )
            )
            db.commit()

    async def authorize(self, client, params: AuthorizationParams):
        scopes = params.scopes if params.scopes is not None else ["calendar:read"]
        if not scopes or not set(scopes).issubset(SCOPES):
            raise AuthorizeError("invalid_scope", "Unsupported scopes")
        if params.resource not in {resource(), resource("extension")}:
            raise AuthorizeError("invalid_request", "An exact Anke Sports resource is required")
        if not re.fullmatch(r"[A-Za-z0-9_-]{43}", params.code_challenge) or len(params.state or "") > 512:
            raise AuthorizeError("invalid_request", "Invalid PKCE or state")
        if not redirect_allowed(params.redirect_uri):
            raise AuthorizeError("invalid_request", "Invalid redirect URI")
        # Persist the immutable request before displaying owner consent.
        pending = secrets.token_urlsafe(32)
        with SessionLocal() as db:
            count = db.scalar(
                select(func.count())
                .select_from(OAuthRequest)
                .where(
                    OAuthRequest.client_id == client.client_id,
                    OAuthRequest.expires_at > int(time.time()),
                    OAuthRequest.approved.is_(False),
                )
            )
            if count >= 20:
                raise AuthorizeError("temporarily_unavailable", "Too many pending requests")
            db.add(
                OAuthRequest(
                    id=digest(pending),
                    client_id=client.client_id,
                    params=params.model_copy(update={"scopes": scopes}).model_dump(mode="json"),
                    expires_at=int(time.time()) + 600,
                )
            )
            db.commit()
        return settings().web_url.rstrip("/") + "/connect?request=" + pending

    async def load_authorization_code(self, client, authorization_code):
        if len(authorization_code) > 200:
            return None
        with SessionLocal() as db:
            row = db.scalar(
                select(OAuthRequest).where(
                    OAuthRequest.code_hash == digest(authorization_code),
                    OAuthRequest.client_id == client.client_id,
                )
            )
            if not row or not row.approved or row.consumed or not row.owner_id:
                return None
            return AuthorizationCode(
                code=authorization_code,
                client_id=row.client_id,
                expires_at=row.code_expires_at,
                subject=row.owner_id,
                **{
                    k: row.params[k]
                    for k in [
                        "scopes",
                        "code_challenge",
                        "redirect_uri",
                        "redirect_uri_provided_explicitly",
                        "resource",
                    ]
                },
            )

    async def exchange_authorization_code(self, client, authorization_code):
        with SessionLocal() as db:
            row = db.scalar(
                select(OAuthRequest)
                .where(OAuthRequest.code_hash == digest(authorization_code.code))
                .with_for_update()
            )
            user = db.get(User, row.owner_id) if row else None
            if (
                not row
                or row.consumed
                or row.client_id != client.client_id
                or row.code_expires_at <= time.time()
                or not user
                or user.deleted
            ):
                raise TokenError("invalid_grant", "Code is invalid or expired")
            if TOKEN_RESOURCE.get() != row.params["resource"]:
                raise TokenError("invalid_grant", "Resource differs from authorization")
            changed = db.execute(
                update(OAuthRequest)
                .where(OAuthRequest.id == row.id, OAuthRequest.consumed.is_(False))
                .values(consumed=True)
            )
            if not changed.rowcount:
                raise TokenError("invalid_grant", "Code already used")
            grant = OAuthGrant(
                id=uid(),
                owner_id=row.owner_id,
                client_id=row.client_id,
                scopes=row.params["scopes"],
                resource=row.params["resource"],
                issuer=issuer(),
                expires_at=int(time.time()) + GRANT_SECONDS,
            )
            db.add(grant)
            db.flush()
            tokens = issue_tokens(db, grant, grant.scopes)
            db.commit()
            return tokens

    async def load_refresh_token(self, client, refresh_token):
        with SessionLocal() as db:
            row, grant = token_info(db, refresh_token, "refresh")
            if not row or grant.client_id != client.client_id:
                return None
            # Used refresh tokens are deliberately loaded so replay revokes the grant.
            return RefreshToken(
                token=refresh_token,
                client_id=grant.client_id,
                scopes=row.scopes,
                expires_at=row.expires_at,
                resource=grant.resource,
                subject=grant.owner_id,
            )

    async def exchange_refresh_token(self, client, refresh_token, scopes):
        with SessionLocal() as db:
            row = db.scalar(
                select(OAuthTokenRecord)
                .where(OAuthTokenRecord.token_hash == digest(refresh_token.token))
                .with_for_update()
            )
            grant = db.get(OAuthGrant, row.grant_id) if row else None
            if (
                not row
                or not valid_grant(db, grant)
                or grant.client_id != client.client_id
                or row.expires_at <= time.time()
            ):
                raise TokenError("invalid_grant", "Refresh token is invalid")
            if TOKEN_RESOURCE.get() != grant.resource or not set(scopes).issubset(row.scopes):
                raise TokenError("invalid_grant", "Resource or scopes differ from grant")
            changed = db.execute(
                update(OAuthTokenRecord)
                .where(OAuthTokenRecord.token_hash == row.token_hash, OAuthTokenRecord.used.is_(False))
                .values(used=True)
            )
            if not changed.rowcount:
                grant.revoked = True
                db.commit()
                raise TokenError("invalid_grant", "Refresh token reuse detected; reconnect required")
            tokens = issue_tokens(db, grant, scopes)
            db.commit()
            return tokens

    async def load_access_token(self, token):
        with SessionLocal() as db:
            row, grant = token_info(db, token, "access")
            return (
                verify_access(db, token, grant.resource)
                if row and grant.resource in {resource(), resource("extension")}
                else None
            )

    async def revoke_token(self, token: AccessToken | RefreshToken):
        with SessionLocal() as db:
            row = db.get(OAuthTokenRecord, digest(token.token))
            if row:
                grant = db.get(OAuthGrant, row.grant_id)
                if grant:
                    grant.revoked = True
            db.commit()


def consent_preview(db, pending):
    row = db.get(OAuthRequest, digest(pending))
    if not row or row.expires_at <= time.time() or row.approved or row.consumed:
        problem("AUTH_REQUEST_EXPIRED", "授权请求已失效，请从原客户端重新连接", 409)
    client = db.get(OAuthClient, row.client_id)
    return row, {
        "client_id": client.id,
        "client_name": client.name,
        "redirect_uri": row.params["redirect_uri"],
        "scopes": row.params["scopes"],
        "resource": row.params["resource"],
        "expires_at": row.expires_at,
    }


def consent_decide(db, user, pending, approved, scopes):
    row, _ = consent_preview(db, pending)
    chosen = sorted(set(scopes))
    if approved and (not chosen or not set(chosen).issubset(row.params["scopes"])):
        problem("INVALID_SCOPE", "不能授予未请求的权限")
    code = secrets.token_urlsafe(32) if approved else None
    values = {
        "approved": approved,
        "owner_id": user.id,
        "consumed": not approved,
        "params": {**row.params, "scopes": chosen},
        "code_hash": digest(code) if code else None,
        "code_expires_at": int(time.time()) + 120,
    }
    changed = db.execute(
        update(OAuthRequest)
        .where(OAuthRequest.id == row.id, OAuthRequest.approved.is_(False), OAuthRequest.consumed.is_(False))
        .values(**values)
    )
    if not changed.rowcount:
        problem("AUTH_REQUEST_EXPIRED", "授权请求已处理", 409)
    return construct_redirect_uri(
        row.params["redirect_uri"],
        code=code,
        error=None if approved else "access_denied",
        state=row.params["state"],
        iss=issuer(),
    )


def connection_list(db, user):
    items = []
    for grant in db.scalars(
        select(OAuthGrant).where(
            OAuthGrant.owner_id == user.id,
            OAuthGrant.revoked.is_(False),
            OAuthGrant.expires_at > int(time.time()),
        )
    ):
        client = db.get(OAuthClient, grant.client_id)
        items.append(
            {
                "id": grant.id,
                "client_name": client.name if client else "External client",
                "scopes": grant.scopes,
                "resource": grant.resource,
                "created_at": grant.created_at,
                "expires_at": grant.expires_at,
            }
        )
    return {"items": items}


def revoke_connection(db, user, grant_id):
    grant = db.get(OAuthGrant, grant_id)
    if not grant or grant.owner_id != user.id:
        problem("NOT_FOUND", "未找到此连接", 404)
    grant.revoked = True


def delete_owner_connections(db, user_id):
    grants = select(OAuthGrant.id).where(OAuthGrant.owner_id == user_id)
    db.execute(delete(OAuthTokenRecord).where(OAuthTokenRecord.grant_id.in_(grants)))
    db.execute(delete(OAuthGrant).where(OAuthGrant.owner_id == user_id))
    db.execute(delete(OAuthRequest).where(OAuthRequest.owner_id == user_id))


def clean_expired_connections():
    from app.db import CommandReceipt

    current = int(time.time())
    with SessionLocal() as db:
        expired_grants = select(OAuthGrant.id).where(
            (OAuthGrant.expires_at <= current) | OAuthGrant.revoked.is_(True)
        )
        db.execute(delete(OAuthTokenRecord).where(OAuthTokenRecord.grant_id.in_(expired_grants)))
        db.execute(
            delete(OAuthGrant).where((OAuthGrant.expires_at <= current) | OAuthGrant.revoked.is_(True))
        )
        db.execute(delete(OAuthTokenRecord).where(OAuthTokenRecord.expires_at <= current))
        db.execute(
            delete(OAuthRequest).where(
                OAuthRequest.expires_at <= current, OAuthRequest.code_expires_at <= current
            )
        )
        db.execute(delete(CommandReceipt).where(CommandReceipt.expires_at <= current))
        # Unused dynamic registrations may be re-created after 30 days.
        db.execute(
            delete(OAuthClient).where(
                OAuthClient.created_at < current - 30 * 86400,
                OAuthClient.id.not_in(select(OAuthGrant.client_id)),
                OAuthClient.id.not_in(select(OAuthRequest.client_id)),
            )
        )
        db.commit()
