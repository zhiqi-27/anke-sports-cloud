"""Isolated document API + separate persistent worker + existing desktop client.

Synthetic schedules only. Requires the existing production client at :3002.
Run: uv run python -m experiments.document_ui ; open localhost:3006/following.
Stopping the experiment removes its temporary documents, key and local sessions.
"""

# ruff: noqa: E402
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
import os
import subprocess
import sys
import tempfile

from cryptography.fernet import Fernet

if os.getenv("WEBSITE_INSTANCE_ID") or os.getenv("ANKE_SPORTS_ENV", "local") != "local":
    raise RuntimeError("LOCAL_EXPERIMENT_ONLY")

preview_port = int(os.environ.get("ANKE_DOCUMENT_UI_PORT", "3006"))
if not 1024 <= preview_port <= 65535:
    raise RuntimeError("LOCAL_PREVIEW_PORT_INVALID")
storage = tempfile.TemporaryDirectory(prefix="anke-document-ui-")
os.environ.update(
    {
        "ANKE_SPORTS_ENV": "local",
        "ANKE_SPORTS_STORAGE_BACKEND": "documents-local",
        "ANKE_SPORTS_DOCUMENT_LOCAL_PATH": storage.name + "/documents.db",
        "ANKE_SPORTS_LOCAL_PREVIEW": "true",
        "ANKE_SPORTS_WEB_URL": f"http://localhost:{preview_port}",
        "ANKE_SPORTS_PUBLIC_URL": f"http://localhost:{preview_port}",
        "ANKE_SPORTS_FIREBASE_PROJECT_ID": "",
        "ANKE_SPORTS_ENABLED_SPORTS_PROVIDERS": "[]",
        "ANKE_SPORTS_ENCRYPTION_KEY": Fernet.generate_key().decode(),
    }
)

import httpx
import uvicorn
from fastapi import Request, Response
from app.config import settings
from app.document_catalog import Catalog
from app.document_store import LocalDocumentStore
from app.main import app

store = LocalDocumentStore(settings().document_local_path)
source = {
    "id": "document-demo:league",
    "name": "演示篮球联赛",
    "short_name": "演示篮球",
    "sport": "basketball",
    "kind": "competition",
    "color": "#16844a",
    "demo": True,
}
teams = [
    {"id": "document-demo:blue", "name": "演示蓝队", "short_name": "蓝队", "color": "#3989fa"},
    {"id": "document-demo:red", "name": "演示红队", "short_name": "红队", "color": "#ff504b"},
]
events = []
instant = datetime.now(timezone.utc).replace(hour=12, minute=0, second=0, microsecond=0)
for index in range(12):
    start = instant + timedelta(days=index - 2)
    events.append(
        {
            "id": f"document-demo-{index:02}",
            "source_key": f"document-demo:event:{index}",
            "competition_id": source["id"],
            "sport": "basketball",
            "title": "演示蓝队 vs 演示红队",
            "starts_at": start.isoformat(),
            "local_date": start.date().isoformat(),
            "time_precision": "exact",
            "timezone": "Asia/Shanghai",
            "duration": 120,
            "venue": "演示场馆",
            "status": "scheduled",
            "participants": teams,
            "provider": "document-demo",
            "source_url": "",
            "updated_at": instant.isoformat(),
            "demo": True,
        }
    )
Catalog(store).publish(
    "document-demo",
    events,
    [source, *[{**team, "kind": "team", "sport": "basketball", "demo": True} for team in teams]],
    expected_revision=0,
    complete=True,
)
original_lifespan = app.router.lifespan_context
WORKER_COMMAND = [sys.executable, "-m", "app.document_worker"]


@asynccontextmanager
async def lifespan(application):
    with open(storage.name + "/worker.log", "wb") as log:
        worker = subprocess.Popen(WORKER_COMMAND, stdout=log, stderr=log)
        print(
            f"Document preview :{preview_port}; isolated worker pid={worker.pid}; synthetic events=12",
            flush=True,
        )
        try:
            async with original_lifespan(application):
                yield
        finally:
            worker.terminate()
            try:
                worker.wait(timeout=5)
            except subprocess.TimeoutExpired:
                worker.kill()
                worker.wait(timeout=5)
            storage.cleanup()


app.router.lifespan_context = lifespan


@app.get("/{path:path}", include_in_schema=False)
async def desktop(path: str, request: Request):
    if path.startswith(("api/", "feeds/", "mcp", "webhooks/")):
        from app.security import problem

        problem("DOCUMENT_FEATURE_UNAVAILABLE", "此功能尚未接入当前存储环境", 503)
    async with httpx.AsyncClient(timeout=30) as client:
        result = await client.get("http://127.0.0.1:3002/" + path, params=request.query_params)
    return Response(
        result.content,
        status_code=result.status_code,
        headers={"content-type": result.headers.get("content-type", "text/plain")},
    )


# Put the HTML proxy before the explicit unmigrated-route handler.
app.router.routes.insert(-1, app.router.routes.pop())

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=preview_port, access_log=False)
