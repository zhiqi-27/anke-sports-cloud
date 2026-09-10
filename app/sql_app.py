from contextlib import asynccontextmanager
import logging
from uuid import uuid4

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from sqlalchemy import delete, select

from app import actions
from app.feed_delivery import calendar_response
from app.public_feeds import router as public_feed_router
from app.broadcast_routes import router as broadcast_router
from app.mcp_server import build_mcp
from app.calendar import event_view
from app.oauth_routes import auth_routes
from app.config import settings
from app.db import (
    Base,
    Creator,
    Feed,
    Session,
    User,
    engine,
    get_db,
)
from app.providers import provider_statuses, resolve_creator
from app.schemas import (
    CalendarUserView,
    AccountDeletionView,
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
    FollowPreviewView,
    SavePreferences,
    ConsentRequestView,
    ConsentDecision,
    ConsentRedirectView,
    ConnectionList,
    ResolveCreator,
    CreatorIdentity,
    UpdateCreator,
    CreatorRemovalImpact,
    ReviewList,
    ReviewDecision,
)
from app.security import actor, check_origin, digest, local_allowed, local_session, problem
from app.seed import seed_demo
from app.service import active_user, enqueue, ensure_user, save_config, user_view


@asynccontextmanager
async def lifespan(app):
    if settings().env == "local":
        Base.metadata.create_all(engine)
        if settings().local_preview:
            from app.db import SessionLocal

            with SessionLocal() as db:
                seed_demo(db)
                db.commit()
    async with mcp_lifespan():
        yield


app = FastAPI(title="Anke Sports API", version="0.1.0", lifespan=lifespan)
app.include_router(broadcast_router)
app.include_router(public_feed_router)
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
        if (
            request.method not in {"GET", "HEAD", "OPTIONS"}
            and not request.url.path.startswith("/webhooks/")
            and request.url.path
            not in {"/register", "/authorize", "/token", "/revoke", "/mcp", "/mcp/public"}
        ):
            check_origin(request)
        response = await call_next(request)
    except HTTPException as exc:
        response = JSONResponse(
            {"error": {**exc.detail, "request_id": request.state.request_id}}, status_code=exc.status_code
        )
    except Exception as exc:
        from app.jobs import error_code

        logging.warning("REQUEST_FAILED request_id=%s code=%s", request.state.request_id, error_code(exc))
        response = JSONResponse(
            {
                "error": {
                    "code": "SERVICE_UNAVAILABLE",
                    "message": "请求未完成，请稍后重试",
                    "retryable": True,
                    "request_id": request.state.request_id,
                }
            },
            status_code=503,
        )
    response.headers["X-Request-ID"] = request.state.request_id
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    if request.url.path.startswith(("/api/", "/mcp")) or request.url.path in {
        "/authorize",
        "/token",
        "/register",
        "/revoke",
    }:
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


def me_write(user=Depends(me), db=Depends(get_db)):
    return active_user(db, user.id)


def find_event(db, event_id):
    return actions.find_event(db, event_id)


@app.get("/api/v1/health")
def health():
    return {"status": "ok", "product": "Anke Sports", "environment": settings().env}


@app.get("/api/v1/status", response_model=ServiceStatusView)
def status(request: Request, db=Depends(get_db)):
    from app.youtube_budget import status as budget_status

    return {
        "youtube_budget": budget_status(db),
        "local_preview": local_allowed(request),
        "firebase_configured": bool(settings().firebase_project_id),
        "providers": provider_statuses(db),
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
    return actions.search_sources(db, q, dataset)


@app.get("/api/v1/events", response_model=EventList)
def events(
    request: Request,
    from_: str = Query(alias="from"),
    to: str = Query(),
    dataset: str = "real",
    followed: bool = False,
    q: str = "",
    limit: int = Query(500, ge=1, le=500),
    cursor: str | None = None,
    db=Depends(get_db),
):
    user_id = actor(request, db, required=followed)
    user = db.get(User, user_id) if user_id else None
    return actions.get_schedule(db, from_, to, dataset, followed, q, user, limit, cursor)


@app.get("/api/v1/events/{event_id}", response_model=EventView)
def event_detail(event_id: str, request: Request, db=Depends(get_db)):
    user_id = actor(request, db, required=False)
    return event_view(db, find_event(db, event_id), db.get(User, user_id) if user_id else None)


@app.get("/api/v1/me/calendar", response_model=CalendarUserView)
def calendar(user=Depends(me), db=Depends(get_db)):
    return user_view(db, user)


@app.post("/api/v1/me/follows/preview", response_model=FollowPreviewView)
def follows_preview(data: SaveFollows, user=Depends(me_write), db=Depends(get_db)):
    from app.follow_changes import preview_follows

    return preview_follows(db, user, data)


@app.put("/api/v1/me/follows", response_model=CalendarUserView)
def follows(
    data: SaveFollows, idempotency_key: str | None = Header(None), user=Depends(me_write), db=Depends(get_db)
):
    result = actions.command(
        db,
        user,
        "set_follows",
        idempotency_key,
        data.model_dump(exclude_none=True),
        lambda: actions.set_follows(db, user, data),
    )
    db.commit()
    return result


@app.patch("/api/v1/me/preferences", response_model=CalendarUserView)
def preferences(data: SavePreferences, user=Depends(me_write), db=Depends(get_db)):
    save_config(
        db, user, {**user.config, "preferences": data.preferences.model_dump()}, data.expected_revision
    )
    db.commit()
    return user_view(db, user)


@app.put("/api/v1/events/{event_id}/selection", response_model=EventView)
def selection(event_id: str, data: OverrideInput, user=Depends(me_write), db=Depends(get_db)):
    event = find_event(db, event_id)
    overrides = [x for x in user.config["event_overrides"] if x["event_key"] != event.source_key]
    if data.state != "reset":
        overrides.append({"event_key": event.source_key, "state": data.state})
    save_config(db, user, {**user.config, "event_overrides": overrides}, data.expected_revision)
    db.commit()
    return event_view(db, event, user)


@app.post("/api/v1/events/{event_id}/links", response_model=LinkAddedView)
def add_link(
    event_id: str,
    data: AddLink,
    idempotency_key: str | None = Header(None),
    user=Depends(me_write),
    db=Depends(get_db),
):
    result = actions.command(
        db,
        user,
        "attach_event_link",
        idempotency_key,
        {"event_id": event_id, **data.model_dump()},
        lambda: actions.add_link(db, user, event_id, data),
    )
    db.commit()
    return result


@app.post("/api/v1/me/links/{link_id}/block")
def block(
    link_id: str, idempotency_key: str | None = Header(None), user=Depends(me_write), db=Depends(get_db)
):
    result = actions.command(
        db,
        user,
        "remove_event_link",
        idempotency_key,
        {"link_id": link_id},
        lambda: actions.block_link(db, user, link_id),
    )
    db.commit()
    return result


@app.post("/api/v1/me/creators/resolve", response_model=CreatorIdentity)
def creator_resolve(data: ResolveCreator, user=Depends(me)):
    details = resolve_creator(data.url.strip())
    return {
        "channel_id": details["channel_id"],
        "name": details["name"],
        "url": "https://www.youtube.com/channel/" + details["channel_id"],
    }


@app.post("/api/v1/me/creators", response_model=CalendarUserView)
def creator_add(
    data: AddCreator, idempotency_key: str | None = Header(None), user=Depends(me), db=Depends(get_db)
):
    result = actions.command(
        db,
        user,
        "add_creator",
        idempotency_key,
        data.model_dump(),
        lambda details: actions.add_creator(db, user, data, details),
        prepare=lambda: resolve_creator(data.url.strip()),
    )
    db.commit()
    return result


def followed_creator(db, user, channel_id):
    creator = db.get(Creator, channel_id)
    if not creator or not any(c["channel_id"] == channel_id for c in user.config["creators"]):
        problem("NOT_FOUND", "未关注此创作者", 404)
    return creator


@app.patch("/api/v1/me/creators/{channel_id}", response_model=CalendarUserView)
def creator_update(channel_id: str, data: UpdateCreator, user=Depends(me_write), db=Depends(get_db)):
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
    channel_id: str,
    expected_revision: int,
    confirmed: bool = False,
    user=Depends(me_write),
    db=Depends(get_db),
):
    from app.content import remove_creator

    followed_creator(db, user, channel_id)
    if not confirmed:
        problem("CONFIRM_REQUIRED", "请查看删除影响并确认")
    remove_creator(db, user, channel_id, expected_revision)
    db.commit()
    return user_view(db, user)


@app.post("/api/v1/me/creators/{channel_id}/refresh")
def creator_refresh(channel_id: str, user=Depends(me_write), db=Depends(get_db)):
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
def review_decide(match_id: str, data: ReviewDecision, user=Depends(me_write), db=Depends(get_db)):
    from app.content import decide_review

    decide_review(db, user, match_id, data.decision, data.kind, data.expected_updated_at)
    db.commit()
    return {"saved": True}


@app.post("/api/v1/me/links/{link_id}/pin")
def link_pin(link_id: str, user=Depends(me_write), db=Depends(get_db)):
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
def import_config(
    data: ImportInput, idempotency_key: str | None = Header(None), user=Depends(me_write), db=Depends(get_db)
):
    result = actions.command(
        db,
        user,
        "import_config",
        idempotency_key if not data.dry_run else None,
        data.model_dump(),
        lambda: actions.import_config(db, user, data),
    )
    db.commit()
    return result


@app.get("/api/v1/me/feed/address")
def address(user=Depends(me), db=Depends(get_db)):
    return actions.feed_address(db, user)


@app.post("/api/v1/me/feed/rotate")
def rotate(data: FeedAction, user=Depends(me_write), db=Depends(get_db)):
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
def pause(data: FeedAction, user=Depends(me_write), db=Depends(get_db)):
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
    return calendar_response(feed, request)


@app.post("/api/v1/local/providers/{provider}/sync")
def trigger_sync(provider: str, request: Request, user=Depends(me_write), db=Depends(get_db)):
    if not local_allowed(request):
        problem("NOT_FOUND", "未找到", 404)
    if provider not in {"balldontlie", "football-data", "jolpica"}:
        problem("UNKNOWN_PROVIDER", "未知数据源")
    from app.providers import enqueue_provider

    enqueue_provider(db, provider)
    db.commit()
    return {"queued": True}


@app.delete("/api/v1/me", response_model=AccountDeletionView)
def delete_account(
    data: FeedAction, request: Request, response: Response, user=Depends(me_write), db=Depends(get_db)
):
    if not data.confirmed:
        problem("CONFIRM_REQUIRED", "请确认删除账号数据")
    from app.privacy import delete_account_data

    # me_write has already verified the bearer. Downstream MCP/extension tokens
    # cannot authorize this endpoint; a local cookie has no Firebase identity.
    firebase_project = (
        settings().firebase_project_id
        if request.headers.get("Authorization", "").startswith("Bearer ")
        else None
    )
    result = delete_account_data(db, user.id, firebase_project=firebase_project)
    db.commit()
    response.delete_cookie("anke_sports_session")
    return result


@app.get("/api/v1/me/connections/requests/{pending}", response_model=ConsentRequestView)
def connection_preview(pending: str, user=Depends(me), db=Depends(get_db)):
    from app.oauth import consent_preview

    return consent_preview(db, pending)[1]


@app.post("/api/v1/me/connections/requests/{pending}", response_model=ConsentRedirectView)
def connection_consent(pending: str, data: ConsentDecision, user=Depends(me_write), db=Depends(get_db)):
    from app.oauth import consent_decide

    url = consent_decide(db, user, pending, data.approved, data.scopes)
    db.commit()
    return {"redirect_url": url}


@app.get("/api/v1/me/connections", response_model=ConnectionList)
def connections(user=Depends(me), db=Depends(get_db)):
    from app.oauth import connection_list

    return connection_list(db, user)


@app.delete("/api/v1/me/connections/{grant_id}")
def connection_revoke(grant_id: str, user=Depends(me_write), db=Depends(get_db)):
    from app.oauth import revoke_connection

    revoke_connection(db, user, grant_id)
    db.commit()
    return {"revoked": True}


mcp_routes, mcp_lifespan = build_mcp()
app.router.routes.extend(auth_routes() + mcp_routes)
