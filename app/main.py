from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from email.utils import format_datetime, parsedate_to_datetime
from uuid import uuid4

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from sqlalchemy import delete, select

from app.calendar import event_view
from app.config import settings
from app.db import (
    Base,
    Creator,
    Event,
    Feed,
    Job,
    Link,
    Projection,
    ProviderState,
    VideoMatch,
    Session,
    Source,
    User,
    engine,
    get_db,
)
from app.providers import resolve_creator
from app.schemas import (
    CalendarUserView,
    EventList,
    EventView,
    ImportPreviewView,
    LinkAddedView,
    ServiceStatusView,
    SourceList,
    AddCreator,
    AddLink,
    Config,
    FeedAction,
    ImportInput,
    OverrideInput,
    SaveFollows,
    SavePreferences,
    ResolveCreator,
    CreatorIdentity,
    UpdateCreator,
    CreatorRemovalImpact,
    ReviewList,
    ReviewDecision,
)
from app.security import actor, check_origin, digest, local_allowed, local_session, problem
from app.seed import seed_demo
from app.service import attach_link, enqueue, ensure_user, import_preview, save_config, user_view


@asynccontextmanager
async def lifespan(app):
    if settings().env == "local":
        Base.metadata.create_all(engine)
        if settings().local_preview:
            from app.db import SessionLocal

            with SessionLocal() as db:
                seed_demo(db)
                db.commit()
    yield


app = FastAPI(title="Anke Sports API", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings().web_url],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    allow_headers=["Authorization", "Content-Type", "Idempotency-Key"],
)


@app.middleware("http")
async def boundary(request, call_next):
    request.state.request_id = uuid4().hex[:16]
    try:
        if request.method not in {"GET", "HEAD", "OPTIONS"} and not request.url.path.startswith("/webhooks/"):
            check_origin(request)
        response = await call_next(request)
    except HTTPException as exc:
        response = JSONResponse(
            {"error": {**exc.detail, "request_id": request.state.request_id}}, status_code=exc.status_code
        )
    response.headers["X-Request-ID"] = request.state.request_id
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    return response


@app.exception_handler(HTTPException)
async def http_error(request, exc):
    detail = (
        exc.detail
        if isinstance(exc.detail, dict)
        else {"code": "REQUEST_FAILED", "message": str(exc.detail), "retryable": False}
    )
    return JSONResponse(
        {"error": {**detail, "request_id": request.state.request_id}}, status_code=exc.status_code
    )


@app.exception_handler(RequestValidationError)
async def validation_error(request, exc):
    return JSONResponse(
        {
            "error": {
                "code": "INVALID_INPUT",
                "message": "输入格式无效，请检查内容和必填项",
                "retryable": False,
                "request_id": request.state.request_id,
            }
        },
        status_code=422,
    )


def me(request: Request, db=Depends(get_db)):
    user = ensure_user(db, actor(request, db))
    db.commit()
    return user


def find_event(db, event_id):
    event = db.get(Event, event_id)
    if not event:
        problem("EVENT_NOT_FOUND", "未找到这场比赛", 404)
    return event


@app.get("/api/v1/health")
def health():
    return {"status": "ok", "product": "Anke Sports", "environment": settings().env}


@app.get("/api/v1/status", response_model=ServiceStatusView)
def status(request: Request, db=Depends(get_db)):
    return {
        "local_preview": local_allowed(request),
        "firebase_configured": bool(settings().firebase_project_id),
        "providers": [
            {"id": p.id, "last_success": p.last_success, "error": p.error, "enabled": p.enabled}
            for p in db.scalars(select(ProviderState))
        ],
        "integrations": {
            "ics_device_test": "not_tested",
            "youtube_push": "not_tested",
            "app_links": "not_tested",
        },
    }


@app.post("/api/v1/auth/local", response_model=CalendarUserView)
def login_local(request: Request, response: Response, db=Depends(get_db)):
    if not local_allowed(request):
        problem("NOT_FOUND", "未找到", 404)
    user = ensure_user(db, "local-reviewer")
    token = local_session(db)
    db.commit()
    response.set_cookie("anke_sports_session", token, httponly=True, samesite="strict", max_age=7 * 86400)
    return user_view(db, user)


@app.post("/api/v1/auth/logout")
def logout(request: Request, response: Response, db=Depends(get_db)):
    token = request.cookies.get("anke_sports_session")
    if token:
        db.execute(delete(Session).where(Session.token_hash == digest(token)))
        db.commit()
    response.delete_cookie("anke_sports_session")
    return {"signed_out": True}


@app.get("/api/v1/sources", response_model=SourceList)
def sources(q: str = "", dataset: str = "real", db=Depends(get_db)):
    rows = db.scalars(
        select(Source).where(Source.demo.is_(dataset == "demo")).order_by(Source.kind, Source.name)
    ).all()
    return {
        "items": [
            {
                "id": s.id,
                "name": s.name,
                "short_name": s.short_name,
                "sport": s.sport,
                "kind": s.kind,
                "color": s.color,
                "demo": s.demo,
            }
            for s in rows
            if q.lower() in (s.name + s.short_name).lower()
        ]
    }


@app.get("/api/v1/events", response_model=EventList)
def events(
    request: Request,
    from_: str = Query(alias="from"),
    to: str = Query(),
    dataset: str = "real",
    followed: bool = False,
    q: str = "",
    db=Depends(get_db),
):
    try:
        lower, upper = (
            datetime.fromisoformat(from_.replace("Z", "+00:00")),
            datetime.fromisoformat(to.replace("Z", "+00:00")),
        )
        if not lower.tzinfo or not upper.tzinfo or not timedelta(0) < upper - lower <= timedelta(days=181):
            raise ValueError()
    except ValueError:
        problem("INVALID_RANGE", "请查询带时区、最长 180 天的有效时间范围")
    user_id = actor(request, db, required=followed)
    user = db.get(User, user_id) if user_id else None
    result = []
    for event in db.scalars(select(Event).where(Event.demo.is_(dataset == "demo"))):
        start = datetime.fromisoformat(event.starts_at.replace("Z", "+00:00")) if event.starts_at else None
        if start is not None and not lower <= start < upper:
            continue
        if (
            start is None
            and event.local_date
            and not lower.date().isoformat() <= event.local_date < upper.date().isoformat()
        ):
            continue
        view = event_view(db, event, user)
        if (followed and not view["included"]) or q.lower() not in event.title.lower():
            continue
        result.append(view)
    result.sort(key=lambda x: (x["starts_at"] or x["local_date"] or "9999", x["id"]))
    return {
        "items": result,
        "coverage": {
            "dataset": dataset,
            "complete": dataset == "demo",
            "note": "synthetic fixtures"
            if dataset == "demo"
            else "Only connected provider snapshots; coverage is not guaranteed",
        },
        "next_cursor": None,
    }


@app.get("/api/v1/events/{event_id}", response_model=EventView)
def event_detail(event_id: str, request: Request, db=Depends(get_db)):
    user_id = actor(request, db, required=False)
    return event_view(db, find_event(db, event_id), db.get(User, user_id) if user_id else None)


@app.get("/api/v1/me/calendar", response_model=CalendarUserView)
def calendar(user=Depends(me), db=Depends(get_db)):
    return user_view(db, user)


@app.put("/api/v1/me/follows", response_model=CalendarUserView)
def follows(data: SaveFollows, user=Depends(me), db=Depends(get_db)):
    for follow in data.follows:
        if not db.get(Source, follow.source_key):
            problem("SOURCE_NOT_FOUND", "该关注对象尚未接入")
    config = {**user.config, "follows": list({f.source_key: f.model_dump() for f in data.follows}.values())}
    save_config(db, user, config, data.expected_revision)
    db.commit()
    return user_view(db, user)


@app.patch("/api/v1/me/preferences", response_model=CalendarUserView)
def preferences(data: SavePreferences, user=Depends(me), db=Depends(get_db)):
    save_config(
        db, user, {**user.config, "preferences": data.preferences.model_dump()}, data.expected_revision
    )
    db.commit()
    return user_view(db, user)


@app.put("/api/v1/events/{event_id}/selection", response_model=EventView)
def selection(event_id: str, data: OverrideInput, user=Depends(me), db=Depends(get_db)):
    event = find_event(db, event_id)
    overrides = [x for x in user.config["event_overrides"] if x["event_key"] != event.source_key]
    if data.state != "reset":
        overrides.append({"event_key": event.source_key, "state": data.state})
    save_config(db, user, {**user.config, "event_overrides": overrides}, data.expected_revision)
    db.commit()
    return event_view(db, event, user)


@app.post("/api/v1/events/{event_id}/links", response_model=LinkAddedView)
def add_link(event_id: str, data: AddLink, user=Depends(me), db=Depends(get_db)):
    event = find_event(db, event_id)
    link = attach_link(db, user, event, data.url, data.title, data.kind)
    db.commit()
    return {"id": link.id, "event": event_view(db, event, user)}


@app.post("/api/v1/me/links/{link_id}/block")
def block(link_id: str, user=Depends(me), db=Depends(get_db)):
    link = db.get(Link, link_id)
    if not link or link.owner_id not in {user.id, "public"}:
        problem("NOT_FOUND", "未找到此链接", 404)
    event = find_event(db, link.event_id)
    overrides = [
        x for x in user.config["link_overrides"] if (x["event_key"], x["url"]) != (event.source_key, link.url)
    ]
    overrides.append({"event_key": event.source_key, "url": link.url, "state": "block"})
    save_config(db, user, {**user.config, "link_overrides": overrides}, user.revision)
    db.commit()
    return {"blocked": True}


@app.post("/api/v1/me/creators/resolve", response_model=CreatorIdentity)
def creator_resolve(data: ResolveCreator, user=Depends(me)):
    details = resolve_creator(data.url.strip())
    return {
        "channel_id": details["channel_id"],
        "name": details["name"],
        "url": "https://www.youtube.com/channel/" + details["channel_id"],
    }


@app.post("/api/v1/me/creators", response_model=CalendarUserView)
def creator_add(data: AddCreator, user=Depends(me), db=Depends(get_db)):
    from app.content import save_creator

    details = resolve_creator(data.url.strip())
    save_creator(db, user, details, data.scope_keys, data.preview, data.recap, True, data.expected_revision)
    db.commit()
    return user_view(db, user)


def followed_creator(db, user, channel_id):
    creator = db.get(Creator, channel_id)
    if not creator or not any(c["channel_id"] == channel_id for c in user.config["creators"]):
        problem("NOT_FOUND", "未关注此创作者", 404)
    return creator


@app.patch("/api/v1/me/creators/{channel_id}", response_model=CalendarUserView)
def creator_update(channel_id: str, data: UpdateCreator, user=Depends(me), db=Depends(get_db)):
    from app.content import save_creator

    creator = followed_creator(db, user, channel_id)
    details = {"channel_id": creator.channel_id, "name": creator.name, "uploads_id": creator.uploads_id}
    save_creator(
        db,
        user,
        details,
        data.scope_keys,
        data.preview,
        data.recap,
        data.enabled,
        data.expected_revision,
        refresh_metadata=False,
    )
    db.commit()
    return user_view(db, user)


@app.get("/api/v1/me/creators/{channel_id}/impact", response_model=CreatorRemovalImpact)
def creator_impact(channel_id: str, user=Depends(me), db=Depends(get_db)):
    from app.content import removal_impact

    followed_creator(db, user, channel_id)
    return removal_impact(db, user, channel_id)


@app.delete("/api/v1/me/creators/{channel_id}", response_model=CalendarUserView)
def creator_delete(
    channel_id: str, expected_revision: int, confirmed: bool = False, user=Depends(me), db=Depends(get_db)
):
    from app.content import remove_creator

    followed_creator(db, user, channel_id)
    if not confirmed:
        problem("CONFIRM_REQUIRED", "请查看删除影响并确认")
    remove_creator(db, user, channel_id, expected_revision)
    db.commit()
    return user_view(db, user)


@app.post("/api/v1/me/creators/{channel_id}/refresh")
def creator_refresh(channel_id: str, user=Depends(me), db=Depends(get_db)):
    from app.content import enqueue_channel

    followed_creator(db, user, channel_id)
    follow = next(c for c in user.config["creators"] if c["channel_id"] == channel_id)
    if not follow["enabled"]:
        problem("CREATOR_PAUSED", "请先恢复此创作者的更新", 409)
    enqueue_channel(db, channel_id)
    db.commit()
    return {"queued": True}


@app.get("/api/v1/me/reviews", response_model=ReviewList)
def reviews(user=Depends(me), db=Depends(get_db)):
    from app.content import review_list

    return review_list(db, user)


@app.post("/api/v1/me/reviews/{match_id}")
def review_decide(match_id: str, data: ReviewDecision, user=Depends(me), db=Depends(get_db)):
    from app.content import decide_review

    decide_review(db, user, match_id, data.decision, data.kind, data.expected_updated_at)
    db.commit()
    return {"saved": True}


@app.post("/api/v1/me/links/{link_id}/pin")
def link_pin(link_id: str, user=Depends(me), db=Depends(get_db)):
    from app.content import pin_link

    pin_link(db, user, link_id)
    db.commit()
    return {"pinned": True}


@app.get("/webhooks/youtube/{callback_id}", include_in_schema=False)
def youtube_verify(callback_id: str, request: Request, db=Depends(get_db)):
    from app.websub import verify_subscription

    challenge = verify_subscription(db, callback_id, request.query_params)
    db.commit()
    return Response(challenge, media_type="text/plain", headers={"Cache-Control": "no-store"})


@app.post("/webhooks/youtube/{callback_id}", include_in_schema=False)
async def youtube_notification(callback_id: str, request: Request, db=Depends(get_db)):
    from app.websub import notification

    body = bytearray()
    async for part in request.stream():
        if len(body) + len(part) > 65536:
            return Response(status_code=413)
        body.extend(part)
    notification(db, callback_id, bytes(body), request.headers.get("x-hub-signature"))
    db.commit()
    return Response(status_code=204)


@app.get("/api/v1/me/config/export", response_model=Config)
def export_config(user=Depends(me)):
    return Config.model_validate(user.config)


@app.post("/api/v1/me/config/import", response_model=ImportPreviewView)
def import_config(data: ImportInput, user=Depends(me), db=Depends(get_db)):
    if data.expected_revision != user.revision:
        problem("REVISION_CONFLICT", "配置已更新，请重新预览", 409)
    config, preview = import_preview(db, user, data)
    if not data.dry_run:
        try:
            valid = settings().cipher().decrypt(
                (data.confirmation or "").encode()
            ) == settings().cipher().decrypt(preview["confirmation"].encode())
        except Exception:
            valid = False
        if not valid:
            problem("PREVIEW_REQUIRED", "请先预览并确认本次导入")
        if preview["unresolved"]:
            problem("UNRESOLVED_CONFIG", "存在无法解析的对象，请修正后导入")
        save_config(db, user, config, data.expected_revision)
        db.commit()
    return {**preview, "applied": not data.dry_run}


@app.get("/api/v1/me/feed/address")
def address(user=Depends(me), db=Depends(get_db)):
    feed = db.scalar(select(Feed).where(Feed.owner_id == user.id))
    token = settings().cipher().decrypt(feed.token_ciphertext.encode()).decode()
    return {"url": f"{settings().public_url}/feeds/{token}.ics", "local_only": settings().env == "local"}


@app.post("/api/v1/me/feed/rotate")
def rotate(data: FeedAction, user=Depends(me), db=Depends(get_db)):
    import secrets

    if not data.confirmed:
        problem("CONFIRM_REQUIRED", "请确认撤销旧订阅地址")
    feed = db.scalar(select(Feed).where(Feed.owner_id == user.id).with_for_update())
    token = secrets.token_urlsafe(32)
    feed.token_hash = digest(token)
    feed.token_ciphertext = settings().cipher().encrypt(token.encode()).decode()
    db.commit()
    return {"rotated": True, "uid_unchanged": True}


@app.post("/api/v1/me/feed/pause")
def pause(data: FeedAction, user=Depends(me), db=Depends(get_db)):
    feed = db.scalar(select(Feed).where(Feed.owner_id == user.id))
    feed.paused = data.confirmed
    if not feed.paused:
        enqueue(db, "projection", {"user_id": user.id})
    db.commit()
    return {"paused": feed.paused}


@app.get("/api/v1/me/feed/preview")
def feed_preview(user=Depends(me), db=Depends(get_db)):
    feed = db.scalar(select(Feed).where(Feed.owner_id == user.id))
    if not feed.body:
        problem("FEED_BUILDING", "订阅源尚未发布，请稍后重试", 503)
    return Response(
        feed.body,
        media_type="text/calendar",
        headers={"Content-Disposition": 'attachment; filename="anke-sports.ics"'},
    )


@app.api_route("/feeds/{token}.ics", methods=["GET", "HEAD"], include_in_schema=False)
def get_feed(token: str, request: Request, db=Depends(get_db)):
    feed = db.scalar(select(Feed).where(Feed.token_hash == digest(token), Feed.revoked.is_(False)))
    if not feed:
        problem("FEED_NOT_FOUND", "订阅地址不存在或已撤销", 404)
    if not feed.body:
        problem("FEED_BUILDING", "订阅源尚未发布，请稍后重试", 503)
    etag = f'"{feed.etag}"'
    changed = datetime.fromisoformat(feed.updated_at).replace(microsecond=0)
    headers = {
        "ETag": etag,
        "Last-Modified": format_datetime(changed, usegmt=True),
        "Cache-Control": "private, no-cache",
        "X-Robots-Tag": "noindex, nofollow",
    }
    incoming = request.headers.get("if-none-match")
    unmodified = incoming and any(x.strip().removeprefix("W/") in {etag, "*"} for x in incoming.split(","))
    if not incoming and request.headers.get("if-modified-since"):
        try:
            unmodified = parsedate_to_datetime(request.headers["if-modified-since"]) >= changed
        except (TypeError, ValueError):
            pass
    if unmodified:
        return Response(status_code=304, headers=headers)
    return Response(
        "" if request.method == "HEAD" else feed.body,
        media_type="text/calendar; charset=utf-8",
        headers=headers,
    )


@app.post("/api/v1/local/providers/{provider}/sync")
def trigger_sync(provider: str, request: Request, user=Depends(me), db=Depends(get_db)):
    if not local_allowed(request):
        problem("NOT_FOUND", "未找到", 404)
    if provider not in {"balldontlie", "football-data", "jolpica"}:
        problem("UNKNOWN_PROVIDER", "未知数据源")
    enqueue(db, "provider", {"provider": provider})
    db.commit()
    return {"queued": True}


@app.delete("/api/v1/me")
def delete_account(data: FeedAction, user=Depends(me), db=Depends(get_db)):
    if not data.confirmed:
        problem("CONFIRM_REQUIRED", "请确认删除账号数据")
    feed = db.scalar(select(Feed).where(Feed.owner_id == user.id))
    db.execute(delete(Projection).where(Projection.feed_id == feed.id))
    db.execute(delete(Link).where(Link.owner_id == user.id))
    db.execute(delete(VideoMatch).where(VideoMatch.owner_id == user.id))
    db.execute(delete(Session).where(Session.user_id == user.id))
    db.execute(delete(Job).where(Job.payload["user_id"].as_string() == user.id))
    feed.revoked, feed.body, feed.token_ciphertext = True, "", ""
    user.deleted, user.config, user.display_name = True, {}, "Deleted account"
    db.commit()
    return {"deleted": True, "external_cache": "请在系统日历中删除旧订阅以清除缓存"}
