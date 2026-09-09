"""Disposable UI integration fixture; no YouTube credentials, real videos or shared preview DB.

Run `uv run python -m experiments.creator_ui`, then browse http://localhost:3001/creators.
First run `npm run build` then `npm run start -- --port 3002` in the client repo. Only this process
replaces the provider adapter. Never import this module from application code.
"""

# ruff: noqa: E402
# The fixture must isolate environment settings before importing application modules.
import asyncio
import os
import tempfile
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

if os.getenv("WEBSITE_INSTANCE_ID") or os.getenv("ANKE_SPORTS_ENV", "local") != "local":
    raise RuntimeError("This disposable fixture is local-only")

temporary = tempfile.TemporaryDirectory(prefix="anke-sports-ui-fixture-")
os.environ["ANKE_SPORTS_ENV"] = "local"
os.environ["ANKE_SPORTS_DATABASE_URL"] = "sqlite:///" + temporary.name + "/fixture.db"
os.environ["ANKE_SPORTS_LOCAL_PREVIEW"] = "true"
os.environ["ANKE_SPORTS_WEB_URL"] = "http://localhost:3001"
os.environ["ANKE_SPORTS_PUBLIC_URL"] = "http://localhost:3001"
os.environ["ANKE_SPORTS_YOUTUBE_WEBSUB_ENABLED"] = "false"
os.environ["ANKE_SPORTS_FIREBASE_PROJECT_ID"] = ""

import httpx
import uvicorn
from fastapi import Request, Response
from sqlalchemy import select

import app.content as content
import app.main as main
from app.db import Base, Event, SessionLocal, engine
from app.seed import seed_demo
from app.service import ensure_user, save_config
from app.worker import run_one

CHANNEL = "UC" + "x" * 22
IDENTITY = {"channel_id": CHANNEL, "name": "本地合成创作者 · UI 验证", "uploads_id": "UUfixture"}
start = (datetime.now(timezone.utc) + timedelta(days=2)).replace(hour=12, minute=0, second=0, microsecond=0)
published = datetime.now(timezone.utc).isoformat()
metadata = {
    "localdemo01": f"【合成测试】Lakers {start.date()} preview",
    "localdemo02": f"【合成测试】Lakers Warriors {start.date()} preview",
}


def fixture_request(endpoint, params):
    if endpoint == "channels":
        return {
            "items": [
                {
                    "id": CHANNEL,
                    "snippet": {"title": IDENTITY["name"]},
                    "contentDetails": {"relatedPlaylists": {"uploads": "UUfixture"}},
                }
            ]
        }
    if endpoint == "playlistItems":
        return {
            "items": [{"contentDetails": {"videoId": key, "videoPublishedAt": published}} for key in metadata]
        }
    if endpoint == "videos":
        return {
            "items": [
                {
                    "id": key,
                    "snippet": {
                        "channelId": CHANNEL,
                        "title": metadata[key],
                        "publishedAt": published,
                        "description": "仅用于本地界面验证；不对应真实 YouTube 视频。",
                    },
                    "status": {"privacyStatus": "public"},
                }
                for key in params["id"].split(",")
                if key in metadata
            ]
        }
    raise ValueError("FIXTURE_UNSUPPORTED_ENDPOINT")


main.resolve_creator = lambda value: IDENTITY.copy()
content.youtube_request = fixture_request


@asynccontextmanager
async def fixture_lifespan(app):
    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        seed_demo(db)
        user = ensure_user(db, "local-reviewer")
        user.display_name = "合成数据 · UI 验证"
        # One exact synthetic game avoids dependence on today's rotating demo schedule.
        sample = db.scalar(select(Event).where(Event.sport == "basketball"))
        event = Event(
            source_key="fixture:creator-game",
            competition_id="demo:nba",
            sport="basketball",
            title="【合成测试】湖人 vs 勇士",
            starts_at=start.isoformat(),
            timezone="UTC",
            duration=150,
            participants=sample.participants,
            provider="LOCAL_FIXTURE",
            demo=True,
        )
        db.add(event)
        save_config(
            db,
            user,
            {**user.config, "event_overrides": [{"event_key": event.source_key, "state": "include"}]},
            user.revision,
        )
        db.commit()

    async def worker():
        while True:
            await asyncio.to_thread(run_one)
            await asyncio.sleep(0.25)

    task = asyncio.create_task(worker())
    try:
        yield
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        engine.dispose()
        temporary.cleanup()


main.app.router.lifespan_context = fixture_lifespan


@main.app.get("/{path:path}", include_in_schema=False)
async def desktop_proxy(path: str, request: Request):
    # Fixed upstream; no user-controlled destination and no identity forwarding.
    async with httpx.AsyncClient(timeout=30) as client:
        result = await client.get("http://127.0.0.1:3002/" + path, params=request.query_params)
    return Response(
        result.content,
        status_code=result.status_code,
        headers={"content-type": result.headers.get("content-type", "text/plain")},
    )


if __name__ == "__main__":
    uvicorn.run(main.app, host="127.0.0.1", port=3001, access_log=False)
