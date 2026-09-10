"""The migrated calendar HTTP surface. Unmigrated routes fail explicitly."""

from contextlib import asynccontextmanager
import logging
import secrets
import time
from uuid import uuid4

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from app.config import settings
from app.document_accounts import document
from app.document_runtime import Runtime
from app.document_store import Conflict, StoreError, Write, open_document_store
from app.feed_delivery import calendar_response
from app.schemas import (
    AddLink,
    AddCreator,
    CalendarUserView,
    Config,
    CreatorIdentity,
    CreatorRemovalImpact,
    EventList,
    EventView,
    FeedAction,
    FollowPreviewView,
    ImportInput,
    ImportPreviewView,
    LinkAddedView,
    OverrideInput,
    ResolveCreator,
    ReviewDecision,
    ReviewList,
    SaveFollows,
    SavePreferences,
    ServiceStatusView,
    SourceList,
    UpdateCreator,
)
from app.security import check_origin, digest, firebase_subject, local_allowed, problem


def create_app(store=None, cfg=None):
    cfg = cfg or settings()

    @asynccontextmanager
    async def lifespan(app):
        owned = store is None
        opened = open_document_store(cfg) if owned else store
        app.state.runtime = Runtime(opened, cfg)
        try:
            yield
        finally:
            if owned:
                opened.close()

    app = FastAPI(title="Anke Sports API", version="0.1.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[cfg.web_url],
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        allow_headers=["Authorization", "Content-Type", "Idempotency-Key"],
    )

    def error_response(request, code, message, status, retryable=False):
        return JSONResponse(
            {
                "error": {
                    "code": code,
                    "message": message,
                    "retryable": retryable,
                    "request_id": request.state.request_id,
                }
            },
            status_code=status,
        )

    @app.exception_handler(HTTPException)
    async def http_error(request, exc):
        data = (
            exc.detail
            if isinstance(exc.detail, dict)
            else {"code": "REQUEST_FAILED", "message": "请求未完成"}
        )
        return error_response(
            request, data["code"], data["message"], exc.status_code, data.get("retryable", False)
        )

    @app.exception_handler(RequestValidationError)
    async def invalid_input(request, exc):
        return error_response(request, "INVALID_INPUT", "输入格式无效，请检查内容和必填项", 422)

    @app.middleware("http")
    async def boundary(request, call_next):
        request.state.request_id = uuid4().hex[:16]
        try:
            if request.method not in {"GET", "HEAD", "OPTIONS"}:
                check_origin(request)
            response = await call_next(request)
        except HTTPException as exc:
            response = await http_error(request, exc)
        except Conflict:
            response = error_response(request, "REVISION_CONFLICT", "内容已更新，请刷新后重试", 409)
        except Exception as exc:
            code = exc.code if isinstance(exc, StoreError) else "SERVICE_UNAVAILABLE"
            logging.warning("REQUEST_FAILED request_id=%s code=%s", request.state.request_id, code)
            response = error_response(
                request, code, "请求未完成，请稍后重试", 503, getattr(exc, "retryable", True)
            )
        response.headers.update(
            {
                "X-Request-ID": request.state.request_id,
                "X-Content-Type-Options": "nosniff",
                "Referrer-Policy": "no-referrer",
            }
        )
        if not request.url.path.startswith("/feeds/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    def runtime(request: Request):
        return request.app.state.runtime

    def identity(request, rt, required=True):
        bearer = request.headers.get("Authorization", "")
        uid = None
        if bearer.startswith("Bearer as_at_"):
            problem("DOCUMENT_FEATURE_UNAVAILABLE", "此存储环境尚未启用外部应用授权", 503)
        if bearer.startswith("Bearer "):
            uid = firebase_subject(bearer[7:])
        elif (token := request.cookies.get("anke_sports_session")) and local_allowed(request):
            session = rt.store.get("state", "local:sessions", digest(token))
            if session and session["payload"]["expires_at"] > time.time():
                uid = session["payload"]["user_id"]
        if uid:
            return rt.accounts.ensure(uid)["payload"]
        if required:
            problem("AUTH_REQUIRED", "登录后保存到个人日历", 401)
        return None

    def me(request: Request, rt=Depends(runtime)):
        return identity(request, rt)

    @app.get("/api/v1/health")
    def health():
        return {
            "status": "ok",
            "product": "Anke Sports",
            "environment": cfg.env,
            "storage_backend": cfg.storage_backend,
        }

    @app.get("/api/v1/status", response_model=ServiceStatusView)
    def status(request: Request, rt=Depends(runtime)):
        return {
            "local_preview": local_allowed(request),
            "firebase_configured": bool(cfg.firebase_project_id),
            "youtube_budget": rt.youtube_budget.status(),
            "providers": rt.providers.statuses(),
            "integrations": {
                "storage": cfg.storage_backend,
                "content_migration": "not_ready",
                "ics_device_test": "not_tested",
                "youtube_push": "not_tested",
                "youtube_discovery": "polling_available",
                "app_links": "not_tested",
            },
        }

    @app.post("/api/v1/me/creators/resolve", response_model=CreatorIdentity)
    def creator_resolve(data: ResolveCreator, user=Depends(me), rt=Depends(runtime)):
        details = rt.resolve_creator(data.url)
        return {
            "channel_id": details["channel_id"],
            "name": details["name"],
            "url": "https://www.youtube.com/channel/" + details["channel_id"],
        }

    @app.post("/api/v1/me/creators", response_model=CalendarUserView)
    def creator_add(
        data: AddCreator, idempotency_key: str | None = Header(None), user=Depends(me), rt=Depends(runtime)
    ):
        return rt.creators.save(user["user_id"], data, key=idempotency_key)

    @app.patch("/api/v1/me/creators/{channel_id}", response_model=CalendarUserView)
    def creator_update(channel_id: str, data: UpdateCreator, user=Depends(me), rt=Depends(runtime)):
        return rt.creators.save(user["user_id"], data, ident=channel_id)

    @app.get("/api/v1/me/creators/{channel_id}/impact", response_model=CreatorRemovalImpact)
    def creator_impact(channel_id: str, user=Depends(me), rt=Depends(runtime)):
        return rt.creators.impact(user, channel_id)

    @app.delete("/api/v1/me/creators/{channel_id}", response_model=CalendarUserView)
    def creator_delete(
        channel_id: str,
        expected_revision: int,
        confirmed: bool = False,
        user=Depends(me),
        rt=Depends(runtime),
    ):
        return rt.creators.remove(user["user_id"], channel_id, expected_revision, confirmed)

    @app.post("/api/v1/me/creators/{channel_id}/refresh")
    def creator_refresh(channel_id: str, user=Depends(me), rt=Depends(runtime)):
        return rt.creators.refresh(user["user_id"], channel_id)

    @app.get("/api/v1/me/reviews", response_model=ReviewList)
    def reviews(user=Depends(me), rt=Depends(runtime)):
        return rt.matches.reviews(user)

    @app.post("/api/v1/me/reviews/{match_id}")
    def review_decide(match_id: str, data: ReviewDecision, user=Depends(me), rt=Depends(runtime)):
        return rt.matches.decide(user["user_id"], match_id, data)

    @app.get("/webhooks/youtube/{callback_id}", include_in_schema=False)
    def youtube_verify(callback_id: str, request: Request, rt=Depends(runtime)):
        challenge = rt.websub.verify(callback_id, request.query_params)
        return Response(challenge, media_type="text/plain", headers={"Cache-Control": "no-store"})

    @app.post("/webhooks/youtube/{callback_id}", include_in_schema=False)
    async def youtube_notification(callback_id: str, request: Request, rt=Depends(runtime)):
        body = bytearray()
        async for part in request.stream():
            if len(body) + len(part) > 65536:
                return Response(status_code=413)
            body.extend(part)
        await run_in_threadpool(
            rt.websub.notification, callback_id, bytes(body), request.headers.get("x-hub-signature")
        )
        return Response(status_code=204)

    @app.post("/api/v1/auth/local", response_model=CalendarUserView)
    def local_login(request: Request, response: Response, rt=Depends(runtime)):
        if not local_allowed(request):
            problem("NOT_FOUND", "未找到", 404)
        user = rt.accounts.ensure("local-reviewer")["payload"]
        token = secrets.token_urlsafe(32)
        row = document(
            "local:sessions",
            digest(token),
            "session",
            user_id="local-reviewer",
            expires_at=int(time.time()) + 7 * 86400,
        )
        rt.store.batch("state", row["pk"], [Write("create", row["id"], row)])
        response.set_cookie("anke_sports_session", token, httponly=True, samesite="strict", max_age=7 * 86400)
        return rt.user_view(user)

    @app.post("/api/v1/auth/logout")
    def logout(request: Request, response: Response, rt=Depends(runtime)):
        token = request.cookies.get("anke_sports_session")
        if token and local_allowed(request):
            row = rt.store.get("state", "local:sessions", digest(token))
            if row:
                rt.store.batch("state", row["pk"], [Write("delete", row["id"], etag=row["_etag"])])
        response.delete_cookie("anke_sports_session")
        return {"signed_out": True}

    @app.get("/api/v1/sources", response_model=SourceList)
    def sources(q: str = "", dataset: str = "real", rt=Depends(runtime)):
        if dataset not in {"real", "demo"} or len(q) > 200:
            problem("INVALID_QUERY", "查询条件无效")
        snapshot = rt.catalog.capture()
        rows = sorted(snapshot.sources(), key=lambda row: (row.kind, row.name))
        return {
            "items": [
                vars(row)
                for row in rows
                if row.demo == (dataset == "demo") and q.casefold() in (row.name + row.short_name).casefold()
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
        limit: int = Query(500, ge=1, le=500),
        cursor: str | None = None,
        rt=Depends(runtime),
    ):
        return rt.schedule(from_, to, dataset, followed, q, identity(request, rt, followed), limit, cursor)

    @app.get("/api/v1/events/{event_id}", response_model=EventView)
    def event_detail(event_id: str, request: Request, rt=Depends(runtime)):
        user = identity(request, rt, False)
        event = rt.catalog.capture().event(event_id)
        if not event:
            problem("EVENT_NOT_FOUND", "未找到这场比赛", 404)
        return rt.event_view(event, user)

    @app.post("/api/v1/events/{event_id}/links", response_model=LinkAddedView)
    def add_link(
        event_id: str,
        data: AddLink,
        idempotency_key: str | None = Header(None),
        user=Depends(me),
        rt=Depends(runtime),
    ):
        return rt.content.attach(user["user_id"], event_id, data, idempotency_key)

    @app.post("/api/v1/me/links/{link_id}/block")
    def block_link(
        link_id: str, idempotency_key: str | None = Header(None), user=Depends(me), rt=Depends(runtime)
    ):
        return rt.content.override_link(user["user_id"], link_id, "block", idempotency_key)

    @app.post("/api/v1/me/links/{link_id}/pin")
    def pin_link(link_id: str, user=Depends(me), rt=Depends(runtime)):
        return rt.content.override_link(user["user_id"], link_id, "pin")

    @app.put("/api/v1/events/{event_id}/selection", response_model=EventView)
    def selection(event_id: str, data: OverrideInput, user=Depends(me), rt=Depends(runtime)):
        return rt.content.selection(user["user_id"], event_id, data)

    @app.get("/api/v1/me/calendar", response_model=CalendarUserView)
    def calendar(user=Depends(me), rt=Depends(runtime)):
        return rt.user_view(user)

    @app.post("/api/v1/me/follows/preview", response_model=FollowPreviewView)
    def preview(data: SaveFollows, user=Depends(me), rt=Depends(runtime)):
        return rt.follows(user, data, preview=True)

    @app.put("/api/v1/me/follows", response_model=CalendarUserView)
    def follows(
        data: SaveFollows, idempotency_key: str | None = Header(None), user=Depends(me), rt=Depends(runtime)
    ):
        return rt.save_follows(user["user_id"], data, idempotency_key)

    @app.patch("/api/v1/me/preferences", response_model=CalendarUserView)
    def preferences(data: SavePreferences, user=Depends(me), rt=Depends(runtime)):
        result = rt.accounts.save_config(
            user["user_id"],
            {**user["config"], "preferences": data.preferences.model_dump()},
            data.expected_revision,
        )
        return rt.user_view(result)

    @app.post("/api/v1/me/config/import", response_model=ImportPreviewView)
    def import_config(
        data: ImportInput, idempotency_key: str | None = Header(None), user=Depends(me), rt=Depends(runtime)
    ):
        return rt.content.import_config(user["user_id"], data, idempotency_key)

    @app.get("/api/v1/me/config/export", response_model=Config)
    def export(user=Depends(me)):
        return user["config"]

    @app.post("/api/v1/local/providers/{provider}/sync")
    def trigger_sync(provider: str, request: Request, user=Depends(me), rt=Depends(runtime)):
        if not local_allowed(request):
            problem("NOT_FOUND", "未找到", 404)
        from app.provider_adapters import PROVIDERS

        if provider not in PROVIDERS:
            problem("UNKNOWN_PROVIDER", "未知数据源")
        rt.providers.enqueue(provider)
        return {"queued": True}

    @app.get("/api/v1/me/feed/address")
    def address(user=Depends(me), rt=Depends(runtime)):
        token = rt.accounts.address(user["user_id"])
        return {
            "url": cfg.public_url.rstrip("/") + "/feeds/" + token + ".ics",
            "local_only": cfg.env == "local",
        }

    @app.post("/api/v1/me/feed/rotate")
    def rotate(data: FeedAction, user=Depends(me), rt=Depends(runtime)):
        if not data.confirmed:
            problem("CONFIRM_REQUIRED", "请确认撤销旧订阅地址")
        rt.accounts.rotate(user["user_id"])
        return {"rotated": True, "uid_unchanged": True}

    @app.post("/api/v1/me/feed/pause")
    def pause(data: FeedAction, user=Depends(me), rt=Depends(runtime)):
        rt.accounts.pause(user["user_id"], data.confirmed)
        return {"paused": data.confirmed}

    @app.get("/api/v1/me/feed/preview")
    def feed_preview(user=Depends(me), rt=Depends(runtime)):
        feed = rt.publisher.read(rt.accounts.address(user["user_id"]))
        return Response(
            feed.body,
            media_type="text/calendar",
            headers={"Content-Disposition": 'attachment; filename="anke-sports.ics"'},
        )

    @app.api_route("/feeds/{token}.ics", methods=["GET", "HEAD"], include_in_schema=False)
    def feed(token: str, request: Request, rt=Depends(runtime)):
        return calendar_response(rt.publisher.read(token), request)

    @app.api_route(
        "/{path:path}", methods=["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE"], include_in_schema=False
    )
    def unmigrated(path: str):
        problem("DOCUMENT_FEATURE_UNAVAILABLE", "此功能尚未接入当前存储环境", 503)

    return app
