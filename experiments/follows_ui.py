"""Disposable follow-preview browser fixture; synthetic schedules, no platform calls.

Run the production client on 3002, then `uv run python -m experiments.follows_ui`.
Open http://localhost:3003/following and enter the local experience.
"""

# ruff: noqa: E402
import asyncio
import os
import tempfile
from contextlib import asynccontextmanager

from cryptography.fernet import Fernet

if os.getenv("WEBSITE_INSTANCE_ID") or os.getenv("ANKE_SPORTS_ENV", "local") != "local":
    raise RuntimeError("Disposable local fixture only")

storage = tempfile.TemporaryDirectory(prefix="anke-follows-ui-")
os.environ.update(
    {
        "ANKE_SPORTS_ENV": "local",
        "ANKE_SPORTS_DATABASE_URL": "sqlite:///" + storage.name + "/fixture.db",
        "ANKE_SPORTS_LOCAL_PREVIEW": "true",
        "ANKE_SPORTS_WEB_URL": "http://localhost:3003",
        "ANKE_SPORTS_PUBLIC_URL": "http://localhost:3003",
        "ANKE_SPORTS_FIREBASE_PROJECT_ID": "",
        "ANKE_SPORTS_ENCRYPTION_KEY": Fernet.generate_key().decode(),
        "ANKE_SPORTS_BROADCAST_CHECKS_ENABLED": "false",
        "ANKE_SPORTS_YOUTUBE_WEBSUB_ENABLED": "false",
    }
)

import httpx
import uvicorn
from fastapi import Request, Response
from app import main
from app.db import Base, SessionLocal, engine
from app.seed import seed_demo
from app.service import ensure_user, save_config
from app.worker import run_one


@asynccontextmanager
async def lifecycle(app):
    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        seed_demo(db)
        user = ensure_user(db, "local-reviewer")
        user.display_name = "合成关注验收"
        save_config(
            db,
            user,
            {
                **user.config,
                "follows": [
                    {"type": "team", "source_key": "demo:lakers"},
                    {"type": "team", "source_key": "demo:warriors"},
                ],
            },
            user.revision,
        )
        db.commit()
    print("Disposable fixture database:", storage.name + "/fixture.db", flush=True)

    async def worker():
        while True:
            await asyncio.to_thread(run_one)
            await asyncio.sleep(0.25)

    task = asyncio.create_task(worker())
    try:
        async with main.mcp_lifespan():
            yield
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        engine.dispose()
        storage.cleanup()


main.app.router.lifespan_context = lifecycle


@main.app.get("/{path:path}", include_in_schema=False)
async def desktop(path: str, request: Request):
    async with httpx.AsyncClient(timeout=30) as client:
        result = await client.get("http://127.0.0.1:3002/" + path, params=request.query_params)
    return Response(
        result.content,
        status_code=result.status_code,
        headers={"content-type": result.headers.get("content-type", "text/plain")},
    )


if __name__ == "__main__":
    uvicorn.run(main.app, host="127.0.0.1", port=3003, access_log=False)
