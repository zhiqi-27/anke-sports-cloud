from tests.test_document_runtime import document_stack as document_stack
from tests.test_document_runtime import drain, ics_rows


def test_public_published_feed_is_anonymous_stable_and_account_independent(document_stack):
    client, rt, _, _ = document_stack
    drain(rt)
    info = client.get("/api/v1/public-feed", params={"source_key": "fixture:league"})
    assert info.status_code == 200, info.text
    assert info.json()["status"] == "published", info.text
    address = info.json()["url"]
    first = client.get(address)
    assert len(ics_rows(first)) == 3
    assert client.get(address, headers={"If-None-Match": first.headers["etag"]}).status_code == 304
    assert client.head(address).content == b""
    rt.public_feeds.schedule("second-pass")
    drain(rt)
    assert client.get(address).content == first.content
    assert client.get(address).headers["etag"] == first.headers["etag"]
    client.post("/api/v1/auth/local")
    rt.privacy.delete("local-reviewer")
    drain(rt)
    assert client.get(address).content == first.content


def test_production_source_allowlist_never_exposes_demo(document_stack):
    from fastapi import HTTPException
    import pytest

    client, rt, _, _ = document_stack
    drain(rt)
    address = rt.public_feeds.ident("fixture:league")
    rt.cfg = rt.cfg.model_copy(update={"env": "production", "public_feed_source_keys": ["fixture:league"]})
    assert rt.public_feeds.info("fixture:league")["status"] == "unavailable"
    with pytest.raises(HTTPException) as failure:
        rt.public_feeds.read(address)
    assert failure.value.status_code == 404
