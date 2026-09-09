import hashlib
import ipaddress
import re
import secrets
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit

from fastapi import HTTPException, Request

from app.config import settings
from app.db import Session, User


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


def actor(request: Request, db, required=True) -> str | None:
    bearer = request.headers.get("Authorization", "")
    token = request.cookies.get("anke_sports_session", "")
    if bearer.startswith("Bearer "):
        import firebase_admin
        from firebase_admin import auth

        cfg = settings()
        if not cfg.firebase_project_id:
            problem("AUTH_UNCONFIGURED", "Firebase 登录尚未配置", 503)
        try:
            try:
                app = firebase_admin.get_app()
            except ValueError:
                app = firebase_admin.initialize_app(options={"projectId": cfg.firebase_project_id})
            claims = auth.verify_id_token(bearer[7:], app=app, check_revoked=True)
            user_id = claims["uid"]
            user = db.get(User, user_id)
            if user and user.deleted:
                problem("ACCOUNT_DELETED", "账号已删除", 403)
            return user_id
        except HTTPException:
            raise
        except Exception:
            problem("AUTH_REQUIRED", "登录已过期，请重新登录", 401)
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
        problem("INVALID_URL", "请输入有效 HTTPS 内容链接")
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
    allowed = {
        "youtube.com",
        "www.youtube.com",
        "m.youtube.com",
        "youtu.be",
        "nba.com",
        "www.nba.com",
        "watch.nba.com",
        "tv.nba.com",
        "f1tv.formula1.com",
        "www.formula1.com",
        "www.uefa.com",
        "www.fifa.com",
        "www.espn.com",
        "www.bilibili.com",
        "bilibili.com",
    }
    if host not in allowed:
        problem("UNSUPPORTED_PLATFORM", "暂不支持此平台，请使用 YouTube 或已支持的官方内容链接")
    if host in {"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be"}:
        parts = parsed.path.strip("/").split("/")
        video_id = (
            parts[0]
            if host == "youtu.be"
            else (
                parts[1]
                if len(parts) > 1 and parts[0] in {"shorts", "live", "embed"}
                else parse_qs(parsed.query).get("v", [""])[0]
            )
        )
        if not re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id):
            problem("INVALID_URL", "请输入具体 YouTube 视频链接")
        return f"https://www.youtube.com/watch?v={video_id}", "YouTube"
    if parsed.path in {"", "/"}:
        problem("INVALID_URL", "请输入具体比赛或内容页，不能是平台首页")
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
    if origin and origin not in allowed:
        problem("ORIGIN_REJECTED", "请求来源不受支持", 403)
    if request.cookies.get("anke_sports_session") and not origin:
        problem("ORIGIN_REQUIRED", "缺少请求来源", 403)
