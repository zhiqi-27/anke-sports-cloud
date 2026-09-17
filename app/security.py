import hashlib
import ipaddress
import re
import secrets
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit

from fastapi import HTTPException, Request

from app.config import settings


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def problem(code: str, message: str, status: int = 400):
    raise HTTPException(status, {"code": code, "message": message, "retryable": status >= 500})


def local_allowed(request: Request) -> bool:
    cfg = settings()
    return (
        cfg.env == "local"
        and cfg.local_preview
        and request.url.hostname in {"localhost", "127.0.0.1", "::1", "testserver"}
        and request.client is not None
        and request.client.host in {"127.0.0.1", "::1", "testclient"}
    )


def firebase_app():
    import firebase_admin
    from firebase_admin import credentials
    import json

    project = settings().firebase_project_id
    if not project:
        raise ValueError("FIREBASE_PROJECT_REQUIRED")
    try:
        app = firebase_admin.get_app("anke-sports-auth")
    except ValueError:
        credential = None
        if value := settings().firebase_credentials_json.get_secret_value():
            try:
                payload = json.loads(value)
                if not isinstance(payload, dict) or payload.get("type") != "service_account":
                    raise ValueError()
                if payload.get("project_id") != project:
                    raise ValueError("IDENTITY_TARGET_MISMATCH")
                credential = credentials.Certificate(payload)
            except Exception:
                # Do not include JSON parser excerpts or private-key diagnostics in host logs.
                raise ValueError("FIREBASE_CREDENTIALS_INVALID_OR_WRONG_PROJECT") from None
        try:
            app = firebase_admin.initialize_app(
                credential=credential, options={"projectId": project}, name="anke-sports-auth"
            )
        except ValueError:
            app = firebase_admin.get_app("anke-sports-auth")
    if app.project_id != project:
        raise ValueError("IDENTITY_TARGET_MISMATCH")
    return app


def firebase_subject(token):
    from firebase_admin import auth

    if not settings().firebase_project_id:
        problem("AUTH_UNCONFIGURED", "Firebase 登录尚未配置", 503)
    try:
        return auth.verify_id_token(token, app=firebase_app(), check_revoked=True)["uid"]
    except Exception:
        problem("AUTH_REQUIRED", "登录已过期，请重新登录", 401)


def actor(request: Request, db, required=True) -> str | None:
    from app.db import Session, User

    bearer = request.headers.get("Authorization", "")
    token = request.cookies.get("anke_sports_session", "")
    if bearer.startswith("Bearer as_at_"):
        from app.oauth import resource, verify_access

        principal = verify_access(db, bearer[7:], resource("extension"))
        if not principal:
            problem("AUTH_REQUIRED", "连接已失效，请重新授权", 401)
        require_scope(request, principal)
        return principal.subject
    if bearer.startswith("Bearer "):
        user_id = firebase_subject(bearer[7:])
        user = db.get(User, user_id)
        if user and user.deleted:
            problem("ACCOUNT_DELETED", "账号已删除", 403)
        return user_id
    if token and local_allowed(request):
        session = db.get(Session, digest(token))
        if session and datetime.fromisoformat(session.expires_at) > datetime.now(timezone.utc):
            user = db.get(User, session.user_id)
            if user and not user.deleted:
                return session.user_id
    if required:
        problem("AUTH_REQUIRED", "登录后保存到个人日历", 401)
    return None


def local_session(db) -> str:
    from app.db import Session

    token = secrets.token_urlsafe(32)
    db.add(
        Session(
            token_hash=digest(token),
            user_id="local-reviewer",
            expires_at=(datetime.now(timezone.utc) + timedelta(days=7)).isoformat(),
        )
    )
    return token


def canonical_url(value: str) -> tuple[str, str]:
    if any(ord(c) < 32 for c in value) or "\\" in value:
        problem("INVALID_URL", "请输入有效的比赛直播入口链接")
    parsed = urlsplit(value.strip())
    host = (parsed.hostname or "").lower().rstrip(".")
    try:
        port = parsed.port
    except ValueError:
        problem("INVALID_URL", "链接端口无效")
    if parsed.scheme != "https" or parsed.username or parsed.password or port not in (None, 443):
        problem("INVALID_URL", "只支持不含账号凭据的 HTTPS 链接")
    if not host or "." not in host or host.endswith((".local", ".internal", ".localhost")):
        problem("INVALID_URL", "不支持本地或内网地址")
    try:
        ipaddress.ip_address(host.strip("[]"))
        problem("INVALID_URL", "请使用平台的域名链接")
    except ValueError:
        pass
    from app.platforms import RULES

    allowed = {domain for rule in RULES for domain in rule["hosts"]}
    if host not in allowed:
        problem("UNSUPPORTED_PLATFORM", "暂不支持此平台，请使用已支持的比赛直播入口")
    from urllib.parse import unquote

    decoded_path = unquote(unquote(parsed.path)).lower()
    if any(x in decoded_path for x in ["\\", "..", ".m3u8", ".mpd", ".mp4", ".m4s"]):
        problem("INVALID_URL", "请使用稳定网页链接，不保存媒体流地址")
    query = parse_qs(parsed.query, keep_blank_values=True)
    forbidden = {
        "token",
        "access_token",
        "auth",
        "authorization",
        "signature",
        "sig",
        "key",
        "api_key",
        "jwt",
        "session",
        "sessionid",
        "code",
        "password",
        "expires",
        "policy",
        "credential",
        "redirect",
        "redirect_uri",
        "redirect_url",
        "next",
        "return_url",
        "returnto",
        "continue",
    }
    if any(
        k.lower().replace("-", "_") in forbidden or k.lower().startswith(("x-amz-", "x-goog-")) for k in query
    ):
        problem("INVALID_URL", "不能保存带有访问凭据或跳转目标的链接")
    if parsed.path in {"", "/"}:
        problem("INVALID_URL", "请输入具体比赛直播入口，不能是平台首页")
    query = parse_qs(parsed.query)
    if any(k.lower() in {"token", "access_token", "auth", "signature", "key"} for k in query):
        problem("INVALID_URL", "不能保存带有访问凭据的链接")
    query = {k: v for k, v in query.items() if not k.lower().startswith("utm_")}
    return urlunsplit(("https", host, parsed.path, urlencode(query, doseq=True), "")), host.removeprefix(
        "www."
    )


def check_origin(request: Request):
    origin = request.headers.get("origin")
    allowed = (
        {settings().web_url, "http://localhost:3000", "http://127.0.0.1:3000"}
        if settings().env == "local"
        else {settings().web_url}
    )
    if (
        origin
        and origin.startswith("chrome-extension://")
        and request.headers.get("Authorization", "").startswith("Bearer as_at_")
        and settings().storage_backend == "sql"
    ):
        from app.db import OAuthClient, SessionLocal
        from app.oauth import resource, verify_access

        with SessionLocal() as db:
            principal = verify_access(db, request.headers["Authorization"][7:], resource("extension"))
            client = db.get(OAuthClient, principal.client_id) if principal else None
            if client:
                import json

                metadata = json.loads(settings().cipher().decrypt(client.metadata_ciphertext.encode()))
                extension_id = origin.removeprefix("chrome-extension://")
                if re.fullmatch(r"[a-p]{32}", extension_id) and any(
                    urlsplit(uri).hostname == extension_id + ".chromiumapp.org"
                    for uri in metadata.get("redirect_uris", [])
                ):
                    return
    if origin and origin not in allowed:
        problem("ORIGIN_REJECTED", "请求来源不受支持", 403)
    if request.cookies.get("anke_sports_session") and not origin:
        problem("ORIGIN_REQUIRED", "缺少请求来源", 403)


def require_scope(request, principal):
    path = request.url.path
    if request.method in {"GET", "HEAD"} and re.fullmatch(
        r"/api/v1/(sources|events(/[^/]+)?|me/(calendar|config/export))",
        path,
    ):
        needed = "calendar:read"
    elif request.method in {"POST", "PUT", "PATCH", "DELETE"} and re.fullmatch(
        r"/api/v1/(events/[^/]+/links|me/(calendar/events/[^/]+|follows|preferences|links/[^/]+/(block|pin)|config/import))",
        path,
    ):
        needed = "calendar:write"
    elif request.method == "GET" and path in {"/api/v1/me/feed/address", "/api/v1/me/feed/preview"}:
        needed = "feed:read"
    else:
        problem("INSUFFICIENT_SCOPE", "此连接不允许执行此操作", 403)
    if needed not in principal.scopes:
        problem("INSUFFICIENT_SCOPE", "此连接未获得所需权限", 403)
