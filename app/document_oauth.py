"""Opaque OAuth credentials with owner-partition grants and atomic token rotation."""

import re
import secrets
import time
from uuid import uuid4

from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizeError,
    RefreshToken,
    RegistrationError,
    TokenError,
    construct_redirect_uri,
)
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken

from app.document_accounts import document, owner_partition
from app.document_store import Conflict, Write, clean, partition_items
from app.oauth_rules import (
    ACCESS_SECONDS,
    GRANT_SECONDS,
    SCOPES,
    TOKEN_RESOURCE,
    issuer,
    redirect_allowed,
    resource,
)
from app.security import digest, problem


class DocumentOAuthProvider:
    def __init__(self, runtime):
        self.rt, self.store = runtime, runtime.store

    def put(self, row, old=None):
        self.store.batch(
            "state",
            row["pk"],
            [Write("replace" if old else "create", row["id"], row, old["_etag"] if old else None)],
        )

    async def get_client(self, client_id):
        if not client_id or len(client_id) > 200:
            return None
        row = self.store.get("state", "oauth:clients", digest(client_id))
        return (
            OAuthClientInformationFull.model_validate_json(
                self.rt.cfg.cipher().decrypt(row["payload"]["metadata"].encode())
            )
            if row
            else None
        )

    async def register_client(self, client_info):
        if (
            not client_info.redirect_uris
            or len(client_info.redirect_uris) > 8
            or not all(redirect_allowed(uri) for uri in client_info.redirect_uris)
        ):
            raise RegistrationError("invalid_redirect_uri", "HTTPS or loopback redirect URIs are required")
        if (
            len(client_info.client_name or "") > 100
            or client_info.token_endpoint_auth_method
            not in {"none", "client_secret_post", "client_secret_basic"}
            or not set((client_info.scope or "").split()).issubset(SCOPES)
        ):
            raise RegistrationError("invalid_client_metadata", "Invalid client metadata")
        current = int(time.time())
        if (
            sum(
                r["payload"]["created_at"] > current - 86400
                for r in partition_items(self.store, "state", "oauth:clients", "oauth_client")
            )
            >= 500
        ):
            raise RegistrationError("invalid_client_metadata", "Registration limit reached")
        self.put(
            document(
                "oauth:clients",
                digest(client_info.client_id),
                "oauth_client",
                created_at=current,
                metadata=self.rt.cfg.cipher().encrypt(client_info.model_dump_json().encode()).decode(),
            )
        )

    async def authorize(self, client, params):
        scopes = params.scopes if params.scopes is not None else ["calendar:read"]
        if not scopes or not set(scopes).issubset(SCOPES):
            raise AuthorizeError("invalid_scope", "Unsupported scopes")
        if (
            params.resource not in {resource(), resource("extension")}
            or not re.fullmatch(r"[A-Za-z0-9_-]{43}", params.code_challenge)
            or len(params.state or "") > 512
            or not redirect_allowed(params.redirect_uri)
        ):
            raise AuthorizeError("invalid_request", "Invalid resource, PKCE or redirect")
        pk = "oauth:requests:" + digest(client.client_id)
        if (
            sum(
                r["payload"]["expires_at"] > time.time() and not r["payload"]["consumed"]
                for r in partition_items(self.store, "state", pk, "oauth_request")
            )
            >= 20
        ):
            raise AuthorizeError("temporarily_unavailable", "Too many pending requests")
        raw = secrets.token_urlsafe(32)
        ident = digest(raw)
        # Routes precede authority; a partially created route cannot grant access.
        self.put(document("oauth:routes", ident, "oauth_route", target_pk=pk))
        self.put(
            document(
                pk,
                ident,
                "oauth_request",
                client_id=client.client_id,
                consumed=False,
                expires_at=int(time.time()) + 600,
                params=params.model_copy(update={"scopes": scopes}).model_dump(mode="json"),
            )
        )
        return self.rt.cfg.web_url.rstrip("/") + "/connect?request=" + raw

    def pending(self, raw):
        route = self.store.get("state", "oauth:routes", digest(raw))
        row = self.store.get("state", route["payload"]["target_pk"], digest(raw)) if route else None
        if not row or row["payload"]["consumed"] or row["payload"]["expires_at"] <= time.time():
            problem("AUTH_REQUEST_EXPIRED", "授权请求已失效，请重新连接", 409)
        return row

    async def preview(self, raw):
        value = self.pending(raw)["payload"]
        client = await self.get_client(value["client_id"])
        return {
            "client_id": client.client_id,
            "client_name": client.client_name or "External client",
            **{k: value["params"][k] for k in ["redirect_uri", "scopes", "resource"]},
            "expires_at": value["expires_at"],
        }

    def consent(self, uid, raw, approved, scopes):
        account = self.rt.accounts.active(uid)
        pending = self.pending(raw)
        value = pending["payload"]
        chosen = sorted(set(scopes))
        if approved and (not chosen or not set(chosen).issubset(value["params"]["scopes"])):
            problem("INVALID_SCOPE", "不能授予未请求的权限")
        # Claim once before creating owner authority. A crash here requires a new
        # authorization request; it cannot duplicate consent across partitions.
        consumed = clean(pending)
        consumed["payload"]["consumed"] = True
        self.put(consumed, pending)
        code = secrets.token_urlsafe(32) if approved else None
        if approved:
            self.put(document("oauth:routes", digest(code), "oauth_route", owner_pk=account["pk"]))
            row = document(
                account["pk"],
                digest(code),
                "oauth_code",
                client_id=value["client_id"],
                used=False,
                expires_at=int(time.time()) + 120,
                params={**value["params"], "scopes": chosen},
            )
            self.store.batch(
                "state", account["pk"], [self.rt.accounts.guard(account), Write("create", row["id"], row)]
            )
        return construct_redirect_uri(
            value["params"]["redirect_uri"],
            code=code,
            error=None if approved else "access_denied",
            state=value["params"]["state"],
            iss=issuer(),
        )

    def routed(self, raw):
        if len(raw) > 200:
            return None
        route = self.store.get("state", "oauth:routes", digest(raw))
        if not route or "owner_pk" not in route["payload"]:
            return None
        return self.store.get("state", route["payload"]["owner_pk"], digest(raw))

    def account(self, pk):
        row = self.store.get("state", pk, "account")
        if not row or row["payload"]["deleted"]:
            raise TokenError("invalid_grant", "Account unavailable")
        return row

    async def load_authorization_code(self, client, authorization_code):
        row = self.routed(authorization_code)
        if (
            not row
            or row["kind"] != "oauth_code"
            or row["payload"]["used"]
            or row["payload"]["client_id"] != client.client_id
        ):
            return None
        value = row["payload"]
        return AuthorizationCode(
            code=authorization_code,
            client_id=client.client_id,
            expires_at=value["expires_at"],
            **{
                k: value["params"][k]
                for k in [
                    "scopes",
                    "code_challenge",
                    "redirect_uri",
                    "redirect_uri_provided_explicitly",
                    "resource",
                ]
            },
        )

    def issue(self, grant, scopes):
        current = int(time.time())
        access, refresh = "as_at_" + secrets.token_urlsafe(32), "as_rt_" + secrets.token_urlsafe(32)
        writes = []
        for raw, kind, expiry in [
            (access, "access", min(current + ACCESS_SECONDS, grant["expires_at"])),
            (refresh, "refresh", grant["expires_at"]),
        ]:
            self.put(document("oauth:routes", digest(raw), "oauth_route", owner_pk=grant["owner_pk"]))
            row = document(
                grant["owner_pk"],
                digest(raw),
                "oauth_token",
                grant_id=grant["id"],
                token_kind=kind,
                scopes=scopes,
                expires_at=expiry,
                used=False,
            )
            writes.append(Write("create", row["id"], row))
        return writes, OAuthToken(
            access_token=access,
            refresh_token=refresh,
            token_type="Bearer",
            expires_in=min(ACCESS_SECONDS, grant["expires_at"] - current),
            scope=" ".join(scopes),
        )

    async def exchange_authorization_code(self, client, authorization_code):
        row = self.routed(authorization_code.code)
        if not row or row["kind"] != "oauth_code":
            raise TokenError("invalid_grant", "Invalid code")
        account = self.account(row["pk"])
        value = row["payload"]
        if (
            value["used"]
            or value["expires_at"] <= time.time()
            or value["client_id"] != client.client_id
            or TOKEN_RESOURCE.get() != value["params"]["resource"]
        ):
            raise TokenError("invalid_grant", "Invalid or expired code")
        grant = dict(
            id=uuid4().hex,
            owner_pk=row["pk"],
            owner_id=account["payload"]["user_id"],
            client_id=client.client_id,
            scopes=value["params"]["scopes"],
            resource=value["params"]["resource"],
            issuer=issuer(),
            revoked=False,
            created_at=int(time.time()),
            expires_at=int(time.time()) + GRANT_SECONDS,
        )
        saved = document(row["pk"], "grant:" + grant["id"], "oauth_grant", **grant)
        changed = clean(row)
        changed["payload"]["used"] = True
        writes, tokens = self.issue(grant, grant["scopes"])
        try:
            self.store.batch(
                "state",
                row["pk"],
                [
                    self.rt.accounts.guard(account),
                    Write("replace", row["id"], changed, row["_etag"]),
                    Write("create", saved["id"], saved),
                    *writes,
                ],
            )
        except Conflict:
            raise TokenError("invalid_grant", "Code already used or account changed") from None
        return tokens

    def token(self, raw, kind):
        if not raw.startswith("as_at_" if kind == "access" else "as_rt_"):
            return None, None
        row = self.routed(raw)
        if (
            not row
            or row["kind"] != "oauth_token"
            or row["payload"]["token_kind"] != kind
            or row["payload"]["expires_at"] <= time.time()
        ):
            return None, None
        grant = self.store.get("state", row["pk"], "grant:" + row["payload"]["grant_id"])
        account = self.store.get("state", row["pk"], "account")
        if (
            not account
            or account["payload"]["deleted"]
            or not grant
            or grant["payload"]["revoked"]
            or grant["payload"]["expires_at"] <= time.time()
            or grant["payload"]["issuer"] != issuer()
        ):
            return None, None
        return row, grant

    async def load_access_token(self, raw):
        return self.verify_access(raw)

    def verify_access(self, raw):
        row, grant = self.token(raw, "access")
        if not row or row["payload"]["used"]:
            return None
        g = grant["payload"]
        return AccessToken(
            token=raw,
            client_id=g["client_id"],
            scopes=row["payload"]["scopes"],
            expires_at=row["payload"]["expires_at"],
            resource=g["resource"],
            subject=g["owner_id"],
            claims={"iss": g["issuer"], "grant_id": g["id"]},
        )

    async def load_refresh_token(self, client, raw):
        row, grant = self.token(raw, "refresh")
        if not row or grant["payload"]["client_id"] != client.client_id:
            return None
        return RefreshToken(
            token=raw,
            client_id=client.client_id,
            scopes=row["payload"]["scopes"],
            expires_at=row["payload"]["expires_at"],
        )

    async def exchange_refresh_token(self, client, refresh_token, scopes):
        row, grant = self.token(refresh_token.token, "refresh")
        if (
            not row
            or grant["payload"]["client_id"] != client.client_id
            or TOKEN_RESOURCE.get() != grant["payload"]["resource"]
            or not set(scopes).issubset(row["payload"]["scopes"])
        ):
            raise TokenError("invalid_grant", "Invalid refresh token")
        account = self.account(row["pk"])
        if row["payload"]["used"]:
            changed = clean(grant)
            changed["payload"]["revoked"] = True
            self.put(changed, grant)
            raise TokenError("invalid_grant", "Refresh reuse detected")
        changed = clean(row)
        changed["payload"]["used"] = True
        writes, tokens = self.issue(grant["payload"], scopes)
        try:
            self.store.batch(
                "state",
                row["pk"],
                [
                    self.rt.accounts.guard(account),
                    Write("replace", grant["id"], clean(grant), grant["_etag"]),
                    Write("replace", row["id"], changed, row["_etag"]),
                    *writes,
                ],
            )
        except Conflict:
            # Re-read to distinguish an actual refresh replay from another owner mutation.
            latest, active = self.token(refresh_token.token, "refresh")
            if latest and latest["payload"]["used"]:
                changed = clean(active)
                changed["payload"]["revoked"] = True
                self.put(changed, active)
            raise TokenError("invalid_grant", "Refresh token changed; reconnect required") from None
        return tokens

    async def revoke_token(self, token):
        row = self.routed(token.token)
        if row:
            grant = self.store.get("state", row["pk"], "grant:" + row["payload"]["grant_id"])
            if grant:
                changed = clean(grant)
                changed["payload"]["revoked"] = True
                self.put(changed, grant)

    async def connections(self, uid):
        self.rt.accounts.active(uid)
        items = []
        for row in partition_items(self.store, "state", owner_partition(uid), "oauth_grant"):
            g = row["payload"]
            if g["revoked"] or g["expires_at"] <= time.time():
                continue
            client = await self.get_client(g["client_id"])
            items.append(
                {
                    **{k: g[k] for k in ["id", "scopes", "resource", "created_at", "expires_at"]},
                    "client_name": client.client_name or "External client",
                }
            )
        return {"items": items}

    def disconnect(self, uid, ident):
        account = self.rt.accounts.active(uid)
        row = self.store.get("state", account["pk"], "grant:" + ident)
        if not row:
            problem("NOT_FOUND", "未找到此连接", 404)
        changed = clean(row)
        changed["payload"]["revoked"] = True
        self.store.batch(
            "state",
            row["pk"],
            [self.rt.accounts.guard(account), Write("replace", row["id"], changed, row["_etag"])],
        )
