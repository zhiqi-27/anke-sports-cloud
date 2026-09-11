"""Loopback desktop for the retained real-content experiment; local identity, no Azure."""
import os
import json
import subprocess
import sys
from pathlib import Path
from contextlib import asynccontextmanager


def main():
    if os.getenv('WEBSITE_INSTANCE_ID'):
        raise RuntimeError('LOCAL_ONLY')
    key_path = Path('data/document-real-content.key')
    ledger = Path('data/document-youtube-live.db')
    if not key_path.is_file() or not ledger.is_file():
        raise RuntimeError('REAL_CONTENT_EXPERIMENT_REQUIRED')
    secret = json.loads(Path('data/youtube-dev.json').read_text())
    if secret.get('project_id') != 'anke-sports-dev':
        raise RuntimeError('PROJECT_MISMATCH')
    os.environ.update(ANKE_SPORTS_ENV='local', ANKE_SPORTS_LOCAL_PREVIEW='true',
        ANKE_SPORTS_STORAGE_BACKEND='documents-local',
        ANKE_SPORTS_DOCUMENT_LOCAL_PATH=str(ledger),
        ANKE_SPORTS_WEB_URL='http://localhost:3009', ANKE_SPORTS_PUBLIC_URL='http://localhost:3009',
        ANKE_SPORTS_ENCRYPTION_KEY=key_path.read_text(),
        ANKE_SPORTS_FIREBASE_PROJECT_ID='', ANKE_SPORTS_FIREBASE_CREDENTIALS_JSON='',
        ANKE_SPORTS_ENABLED_SPORTS_PROVIDERS='[]',
        ANKE_SPORTS_YOUTUBE_PROJECT_ID='anke-sports-dev', ANKE_SPORTS_YOUTUBE_DAILY_BUDGET='20',
        YOUTUBE_API_KEY=secret['api_key'])
    import httpx
    import uvicorn
    from fastapi import Request, Response
    from app.main import app
    original = app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(application):
        with Path('data/document-real-ui-worker.log').open('ab') as log:
            worker = subprocess.Popen([sys.executable, '-m', 'app.document_worker'], stdout=log, stderr=log)
            print(f'Real-content local preview :3009; worker pid={worker.pid}', flush=True)
            try:
                async with original(application):
                    yield
            finally:
                worker.terminate()
                try:
                    worker.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    worker.kill()
                    worker.wait()
    app.router.lifespan_context = lifespan

    @app.get('/{path:path}', include_in_schema=False)
    async def desktop(path: str, request: Request):
        if path.startswith(('api/', 'feeds/', 'mcp', 'webhooks/')):
            from app.security import problem
            problem('DOCUMENT_FEATURE_UNAVAILABLE', '此功能尚未接入当前存储环境', 503)
        async with httpx.AsyncClient(timeout=30, trust_env=False) as client:
            result = await client.get('http://127.0.0.1:3002/' + path, params=request.query_params)
        return Response(result.content, status_code=result.status_code,
            headers={'content-type': result.headers.get('content-type', 'text/plain')})
    app.router.routes.insert(-1, app.router.routes.pop())
    uvicorn.run(app, host='127.0.0.1', port=3009, access_log=False)


if __name__ == '__main__':
    main()
