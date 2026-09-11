"""Bounded real F1/YouTube verification; retained local document ledger, no cloud writes."""
import json
import logging
import os
from pathlib import Path
from datetime import datetime, timezone

from cryptography.fernet import Fernet
from icalendar import Calendar
from fastapi.testclient import TestClient


def main():
    logging.disable(logging.CRITICAL)
    output = Path('data/document-real-content-2026-09-11-run3.json')
    if output.exists():
        raise RuntimeError('NEW_OUTPUT_REQUIRED')
    secret = json.loads(Path('data/youtube-dev.json').read_text())
    assert secret['project_id'] == 'anke-sports-dev'
    assert secret['key_resource'] == 'projects/736683203171/locations/global/keys/anke-sports-youtube-dev'
    key_path = Path('data/document-real-content.key')
    if not key_path.exists():
        with key_path.open('xb') as f:
            os.chmod(key_path, 0o600)
            f.write(Fernet.generate_key())
    os.environ.update(ANKE_SPORTS_ENV='local', ANKE_SPORTS_LOCAL_PREVIEW='true',
        ANKE_SPORTS_STORAGE_BACKEND='documents-local',
        ANKE_SPORTS_DOCUMENT_LOCAL_PATH='data/document-youtube-live.db',
        ANKE_SPORTS_WEB_URL='http://testserver', ANKE_SPORTS_PUBLIC_URL='http://testserver',
        ANKE_SPORTS_ENCRYPTION_KEY=key_path.read_text(),
        ANKE_SPORTS_YOUTUBE_PROJECT_ID='anke-sports-dev', ANKE_SPORTS_YOUTUBE_DAILY_BUDGET='20',
        YOUTUBE_API_KEY=secret['api_key'])
    from app.config import settings
    from app.document_api import create_app
    from app.document_runtime import Runtime
    from app.document_store import LocalDocumentStore
    from app.document_worker import LocalQueue, dispatch
    from app.provider_adapters import fetch_schedule

    cfg = settings()
    store = LocalDocumentStore(cfg.document_local_path)
    rt = Runtime(store, cfg)
    report = {'started_at': datetime.now(timezone.utc).isoformat(), 'success': False,
        'environment': 'documents-local; ASGI TestClient; real upstream HTTP',
        'identity': 'isolated local-reviewer', 'device_tested': False, 'azure_tested': False,
        'checks': [], 'budget_before': rt.youtube_budget.status()}
    def check(ok, name):
        if not ok:
            raise RuntimeError(name)
        report['checks'].append(name)
        print(json.dumps({'check': name}), flush=True)
    def drain():
        queue = LocalQueue(store)
        for _ in range(1000):
            advanced, consumed = dispatch(store, queue.send), queue.consume(rt)
            if not advanced and not consumed:
                return
        raise RuntimeError('WORKER_NOT_IDLE')
    try:
        if not rt.providers.state('jolpica'):
            rt.providers.enqueue('jolpica')
        drain()
        check(bool(rt.providers.state('jolpica')['payload']['last_success']), 'real_schedule_published')
        with TestClient(create_app(store, cfg), headers={'Origin': cfg.web_url}) as client:
            check(client.post('/api/v1/auth/local').status_code == 200, 'local_identity')
            state = client.get('/api/v1/me/calendar').json()
            data = {'expected_revision': state['revision'], 'follows': [{'type': 'competition', 'source_key': 'jolpica:f1'}]}
            preview = client.post('/api/v1/me/follows/preview', json=data)
            check(preview.status_code == 200, 'follow_preview')
            data['confirmation'] = preview.json()['confirmation']
            saved = client.put('/api/v1/me/follows', json=data)
            check(saved.status_code == 200, 'follow_saved')
            drain()
            address = client.get('/api/v1/me/feed/address').json()['url']
            def feed():
                response = client.get(address)
                check(response.status_code == 200, 'feed_read')
                return response, {str(e['UID']): e for e in Calendar.from_ical(response.content).walk('VEVENT')}
            _, before = feed()
            if not state['config']['creators']:
                added = client.post('/api/v1/me/creators', json={'url': '@Formula1',
                    'scope_keys': ['jolpica:f1'], 'preview': True, 'recap': True,
                    'expected_revision': saved.json()['revision']}, headers={'Idempotency-Key': 'real-content-20260911'})
                check(added.status_code == 200, 'real_creator_saved')
            drain()
            response, after = feed()
            check(before.keys() == after.keys() and bool(before), 'stable_real_event_uids')
            linked = [e for e in after.values() if 'youtube.com/watch?v=' in str(e.get('DESCRIPTION', ''))]
            report['linked_events'] = [{'title': str(e['SUMMARY']), 'description': str(e['DESCRIPTION']),
                'sequence_before': int(before[str(e['UID'])]['SEQUENCE']), 'sequence_after': int(e['SEQUENCE'])} for e in linked]
            check(bool(linked), 'real_video_in_personal_ics')
            check(all(int(e['SEQUENCE']) >= int(before[str(e['UID'])]['SEQUENCE']) for e in linked), 'event_sequence_not_regressed')
            check(client.get(address, headers={'If-None-Match': response.headers['etag']}).status_code == 304, 'unchanged_feed_304')
            report['success'] = True
    except Exception as exc:
        report['failure'] = str(exc) if type(exc) is RuntimeError else type(exc).__name__
    finally:
        report['budget_after'] = rt.youtube_budget.status()
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n')
        store.close()
    print(json.dumps({k: report.get(k) for k in ['success', 'failure', 'budget_after']}))
    return 0 if report['success'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
