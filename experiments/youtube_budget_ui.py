"""Disposable budget banner fixture. Temporary SQLite, synthetic identity, no upstream calls.

Start the production client at 3002; run `uv run python -m experiments.youtube_budget_ui`.
Open http://[::1]:3004/fixture/login. POST /fixture/resume releases the synthetic wait;
the existing creator page must notice via its normal polling without reloading.
"""

# ruff: noqa: E402
import os
import tempfile
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

from cryptography.fernet import Fernet

if os.getenv("WEBSITE_INSTANCE_ID") or os.getenv("ANKE_SPORTS_ENV", "local") != "local":
    raise RuntimeError("Disposable local fixture only")

storage = tempfile.TemporaryDirectory(prefix="anke-quota-ui-")
os.environ.update(
    {
        "ANKE_SPORTS_ENV": "local",
        "ANKE_SPORTS_DATABASE_URL": "sqlite:///" + storage.name + "/fixture.db",
        "ANKE_SPORTS_LOCAL_PREVIEW": "true",
        "ANKE_SPORTS_WEB_URL": "http://[::1]:3004",
        "ANKE_SPORTS_PUBLIC_URL": "http://localhost:3004",
        "ANKE_SPORTS_FIREBASE_PROJECT_ID": "",
        "ANKE_SPORTS_ENCRYPTION_KEY": Fernet.generate_key().decode(),
        "ANKE_SPORTS_YOUTUBE_PROJECT_ID": "synthetic-ui-fixture",
        "ANKE_SPORTS_YOUTUBE_DAILY_BUDGET": "6",
        "YOUTUBE_API_KEY": "synthetic-fixture-no-network",
        "ANKE_SPORTS_BROADCAST_CHECKS_ENABLED": "false",
        "ANKE_SPORTS_YOUTUBE_WEBSUB_ENABLED": "false",
    }
)

import httpx
import uvicorn
from fastapi import Request, Response
from fastapi.responses import RedirectResponse
from app import content, main, providers, youtube_budget
from app.db import Base, SessionLocal, YouTubeBudget, engine
from app.security import local_session, problem
from app.seed import seed_demo
from app.service import ensure_user


def no_network(*args, **kwargs):
    problem("SYNTHETIC_FIXTURE", "隔离界面夹具不访问 YouTube", 503)


providers.youtube_request = content.youtube_request = no_network


@asynccontextmanager
async def lifecycle(app):
    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        seed_demo(db)
        user = ensure_user(db, "local-reviewer")
        user.display_name = "合成配额验收 · 无外部请求"
        content.save_creator(
            db,
            user,
            {"channel_id": "UC" + "a" * 22, "name": "合成创作者 · 等待恢复", "uploads_id": "UUfixture"},
            [],
            True,
            True,
            True,
            user.revision,
        )
        instant = datetime.now(timezone.utc)
        period, _ = youtube_budget.window(instant)
        db.add(
            YouTubeBudget(
                project_id="synthetic-ui-fixture",
                period=period,
                daily_limit=6,
                reserved_units=1,
                blocked_until=(instant + timedelta(hours=1)).isoformat(),
                reason="YOUTUBE_RATE_LIMITED",
            )
        )
        db.commit()
    try:
        async with main.mcp_lifespan():
            yield
    finally:
        engine.dispose()
        storage.cleanup()


main.app.router.lifespan_context = lifecycle


@main.app.get("/fixture/login", include_in_schema=False)
def fixture_login():
    with SessionLocal() as db:
        token = local_session(db)
        db.commit()
    response = RedirectResponse("/creators", status_code=303)
    response.set_cookie("anke_sports_session", token, httponly=True, samesite="strict")
    return response


@main.app.post("/fixture/resume", include_in_schema=False)
def fixture_resume():
    with SessionLocal() as db:
        row = db.get(YouTubeBudget, "synthetic-ui-fixture")
        row.blocked_until, row.reason = None, ""
        db.commit()
    return {"synthetic_wait_released": True, "external_requests": 0}


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
