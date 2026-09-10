"""Isolated synthetic matching review: http://[::1]:3004/creators.

Requires the current client build on 127.0.0.1:3002. No cloud credentials,
upstream requests, existing database or user's browser cookies are used.
"""

# ruff: noqa: E402
import asyncio
import os
import tempfile
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

if os.getenv("WEBSITE_INSTANCE_ID") or os.getenv("ANKE_SPORTS_ENV", "local") != "local":
    raise RuntimeError("LOCAL_ONLY")

scratch = tempfile.TemporaryDirectory(prefix="anke-sports-matching-review-")
for key, value in {
    "ANKE_SPORTS_ENV": "local",
    "ANKE_SPORTS_DATABASE_URL": "sqlite:///" + scratch.name + "/fixture.db",
    "ANKE_SPORTS_LOCAL_PREVIEW": "true",
    # SDK issuer validation permits localhost HTTP; the UI uses IPv6 to keep
    # fixture cookies separate. This fixture does not test OAuth issuer routing.
    "ANKE_SPORTS_PUBLIC_URL": "http://localhost:3004",
    "ANKE_SPORTS_WEB_URL": "http://[::1]:3004",
    "ANKE_SPORTS_YOUTUBE_WEBSUB_ENABLED": "false",
    "ANKE_SPORTS_FIREBASE_PROJECT_ID": "",
    "YOUTUBE_API_KEY": "",
}.items():
    os.environ[key] = value

import httpx
import uvicorn
from fastapi import Request, Response

from app.calendar import rebuild_feed
from app.content import match_video
from app.db import Base, ChannelSync, Creator, Event, SessionLocal, Video, engine
from app.main import app
from app.schemas import CreatorFollow
from app.service import ensure_user, save_config
from app.worker import run_one

CHANNEL = "UC" + "m" * 22


@asynccontextmanager
async def lifespan(app):
    Base.metadata.create_all(engine)
    instant = datetime.now(timezone.utc)
    start = (instant + timedelta(days=2)).replace(hour=12, minute=0, second=0, microsecond=0)
    following = (start + timedelta(days=1)).date()
    with SessionLocal() as db:
        user = ensure_user(db, "local-reviewer")
        user.display_name = "合成数据 · 匹配规则验证"
        event = Event(
            source_key="fixture:matching", competition_id="fixture:nba", sport="basketball",
            title="【合成测试】湖人 vs 勇士", starts_at=start.isoformat(), timezone="UTC",
            duration=150, provider="LOCAL_FIXTURE", demo=True,
            participants=[
                {"id": "fixture:LAL", "name": "Lakers", "short_name": "LAL", "color": "#fff"},
                {"id": "fixture:GSW", "name": "Warriors", "short_name": "GSW", "color": "#fff"},
            ],
        )
        db.add_all([
            event, Creator(channel_id=CHANNEL, name="合成创作者 · 匹配验证", uploads_id="UUfixture"),
            ChannelSync(channel_id=CHANNEL),
        ])
        db.flush()
        save_config(db, user, {
            **user.config,
            "creators": [CreatorFollow(channel_id=CHANNEL).model_dump()],
            "event_overrides": [{"event_key": event.source_key, "state": "include"}],
        }, user.revision)
        examples = [
            ("localrev001", "【合成测试】日常记录", f"Lakers Warriors {start.date()} preview"),
            ("localrev002", f"【合成测试】Lakers Warriors {start.date()} / {following} preview", ""),
            ("localrev003", f"【合成测试】Lakers Warriors {start.date()}", "Match preview"),
            ("localrev004", f"【合成测试】Lakers Warriors {start.date()} preview", ""),
        ]
        for ident, title, description in examples:
            video = Video(id=ident, channel_id=CHANNEL, title=title, description=description,
                          published_at=instant.isoformat())
            db.add(video)
            db.flush()
            match_video(db, video)
        rebuild_feed(db, user.id)
        db.commit()

    async def publish():
        while True:
            await asyncio.to_thread(run_one)
            await asyncio.sleep(0.25)

    task = asyncio.create_task(publish())
    try:
        yield
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        engine.dispose()
        scratch.cleanup()


app.router.lifespan_context = lifespan


@app.get("/{path:path}", include_in_schema=False)
async def desktop_proxy(path: str, request: Request):
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.get("http://127.0.0.1:3002/" + path, params=request.query_params)
    return Response(response.content, response.status_code,
                    headers={"content-type": response.headers.get("content-type", "text/plain")})


if __name__ == "__main__":
    uvicorn.run(app, host="::1", port=3004, access_log=False)
