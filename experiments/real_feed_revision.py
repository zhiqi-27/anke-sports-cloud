"""Verify real feed description updates over live loopback HTTP, restoring preferences."""
import json
import logging
import time
from pathlib import Path
import httpx
from icalendar import Calendar


def main():
    logging.disable(logging.CRITICAL)
    output = Path('evidence/real-feed-revision-2026-09-11.json')
    if output.exists():
        raise RuntimeError('OUTPUT_EXISTS')
    report = {'environment': 'live loopback HTTP; local identity and document worker', 'device_tested': False}
    with httpx.Client(base_url='http://localhost:3009', headers={'Origin':'http://localhost:3009'}, trust_env=False) as c:
        c.post('/api/v1/auth/local').raise_for_status()
        original = c.get('/api/v1/me/calendar').json()['config']['preferences']
        address = c.get('/api/v1/me/feed/address').json()['url']
        assert address.startswith('http://localhost:3009/feeds/')
        def snapshot():
            r = c.get(address)
            r.raise_for_status()
            return r, {str(e['UID']):e for e in Calendar.from_ical(r.content).walk('VEVENT')}
        def save(preferences):
            revision = c.get('/api/v1/me/calendar').json()['revision']
            c.patch('/api/v1/me/preferences', json={'expected_revision':revision,'preferences':preferences}).raise_for_status()
        def updated(etag):
            until = time.monotonic()+45
            while time.monotonic()<until:
                r, rows = snapshot()
                if r.headers['etag'] != etag:
                    return r, rows
                time.sleep(.3)
            raise RuntimeError('PUBLICATION_TIMEOUT')
        before, rows = snapshot()
        target = next(uid for uid,e in rows.items() if 'uptj3to1l7o' in str(e.get('DESCRIPTION','')))
        try:
            save({**original,'spoiler_free':not original['spoiler_free']})
            changed, after = updated(before.headers['etag'])
            assert rows.keys() == after.keys()
            assert str(rows[target]['DESCRIPTION']) != str(after[target]['DESCRIPTION'])
            assert int(after[target]['SEQUENCE']) == int(rows[target]['SEQUENCE'])+1
            assert 'uptj3to1l7o' in str(after[target]['DESCRIPTION'])
            report['version_before'] = int(rows[target]['SEQUENCE'])
            report['version_changed'] = int(after[target]['SEQUENCE'])
            report['events'] = len(rows)
        finally:
            save(original)
        restored, final = updated(changed.headers['etag'])
        assert rows.keys() == final.keys()
        assert str(rows[target]['DESCRIPTION']) == str(final[target]['DESCRIPTION'])
        assert int(final[target]['SEQUENCE']) == int(after[target]['SEQUENCE'])+1
        assert c.get(address, headers={'If-None-Match':restored.headers['etag']}).status_code==304
        assert c.get('/api/v1/me/calendar').json()['config']['preferences']==original
        report.update(success=True, preferences_restored=True, version_restored=int(final[target]['SEQUENCE']), uid_stable=True, conditional_304=True)
    output.write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report))


if __name__=='__main__':
    main()
