"""Disposable deletion UI fixture; synthetic identity, no Firebase/platform calls.

Run the production client on 3002, then `uv run python -m experiments.privacy_ui`.
Open http://[::1]:3004/fixture/login. IPv6 keeps cookies apart from main previews.
Only this fixture overrides the status UI flag to exercise account deletion;
production auth/routes and the shared local account protection remain unchanged.
"""

# ruff: noqa: E402
import os
import tempfile
from contextlib import asynccontextmanager

from cryptography.fernet import Fernet

if os.getenv("WEBSITE_INSTANCE_ID") or os.getenv("ANKE_SPORTS_ENV", "local") != "local":
    raise RuntimeError("Disposable local fixture only")

storage = tempfile.TemporaryDirectory(prefix="anke-privacy-ui-")
os.environ.update(
    {
        "ANKE_SPORTS_ENV": "local",
        "ANKE_SPORTS_DATABASE_URL": "sqlite:///" + storage.name + "/fixture.db",
        "ANKE_SPORTS_LOCAL_PREVIEW": "true",
        "ANKE_SPORTS_WEB_URL": "http://[::1]:3004",
        # MCP SDK's development issuer allowlist uses localhost; this fixture
        # exercises Web deletion only, accessed through the separate IPv6 host.
        "ANKE_SPORTS_PUBLIC_URL": "http://localhost:3004",
        "ANKE_SPORTS_FIREBASE_PROJECT_ID": "",
        "ANKE_SPORTS_ENCRYPTION_KEY": Fernet.generate_key().decode(),
        "ANKE_SPORTS_BROADCAST_CHECKS_ENABLED": "false",
        "ANKE_SPORTS_YOUTUBE_WEBSUB_ENABLED": "false",
    }
)

import httpx
import uvicorn
from fastapi import Request, Response
from fastapi.responses import RedirectResponse
from sqlalchemy import func, select
from app import main
from app.db import Base, Feed, Job, Link, Projection, Session, SessionLocal, User, engine
from app.security import local_session
from app.seed import seed_demo
from app.service import ensure_user
from app.worker import run_one


@asynccontextmanager
async def lifecycle(app):
    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        seed_demo(db)
        user = ensure_user(db, "local-reviewer")
        user.display_name = "隔离删除验收（合成账号）"
        ensure_user(db, "preserved-fixture-owner")
        db.commit()
    while run_one():
        pass
    try:
        async with main.mcp_lifespan():
            yield
    finally:
        engine.dispose()
        storage.cleanup()


main.app.router.lifespan_context = lifecycle
main.app.router.routes = [
    route for route in main.app.router.routes if getattr(route, "path", "") != "/api/v1/status"
]


@main.app.get("/api/v1/status", include_in_schema=False)
def fixture_status():
    return {
        "local_preview": False,
        "firebase_configured": False,
        "providers": [],
        "integrations": {"identity": "synthetic_deletion_fixture"},
    }


@main.app.get("/fixture/login", include_in_schema=False)
def fixture_login():
    with SessionLocal() as db:
        ensure_user(db, "local-reviewer")
        token = local_session(db)
        db.commit()
    response = RedirectResponse("/settings", status_code=303)
    response.set_cookie("anke_sports_session", token, httponly=True, samesite="strict")
    return response


@main.app.get("/fixture/evidence", include_in_schema=False)
async def fixture_evidence():
    with SessionLocal() as db:
        owner = db.get(User, "local-reviewer")
        feed = db.scalar(select(Feed).where(Feed.owner_id == owner.id))
        return {
            "fixture": "synthetic identity; temporary SQLite; no Firebase calls",
            "deleted": owner.deleted,
            "config_empty": owner.config == {},
            "feed_revoked": feed.revoked,
            "feed_body_empty": not feed.body,
            "feed_secret_erased": not feed.token_ciphertext,
            "personal_projections": db.scalar(
                select(func.count()).select_from(Projection).where(Projection.feed_id == feed.id)
            ),
            "personal_links": db.scalar(
                select(func.count()).select_from(Link).where(Link.owner_id == owner.id)
            ),
            "personal_sessions": db.scalar(
                select(func.count()).select_from(Session).where(Session.user_id == owner.id)
            ),
            "personal_jobs": db.scalar(
                select(func.count()).select_from(Job).where(Job.payload["user_id"].as_string() == owner.id)
            ),
            "other_owner_preserved": not db.get(User, "preserved-fixture-owner").deleted,
        }


@main.app.get("/{path:path}", include_in_schema=False)
async def desktop(path: str, request: Request):
    async with httpx.AsyncClient(timeout=30, trust_env=False) as client:
        result = await client.get("http://127.0.0.1:3002/" + path, params=request.query_params)
    return Response(
        result.content,
        status_code=result.status_code,
        headers={"content-type": result.headers.get("content-type", "text/plain")},
    )


if __name__ == "__main__":
    uvicorn.run(main.app, host="::1", port=3004, access_log=False)
