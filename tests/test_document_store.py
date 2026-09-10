"""Local transaction invariants and the real Cosmos SDK over an offline transport."""

from concurrent.futures import ThreadPoolExecutor
import json
import logging
import sqlite3
from threading import Barrier
from urllib.parse import urlsplit

from azure.core.credentials import AccessToken
from azure.core.pipeline.transport import HttpResponse, HttpTransport
from azure.cosmos import CosmosClient
from azure.cosmos.exceptions import CosmosBatchOperationError, CosmosHttpResponseError
import pytest

from app.document_accounts import document
from app.document_store import (
    Conflict,
    CosmosDocumentStore,
    LocalDocumentStore,
    StoreError,
    Write,
    clean,
    partition_items,
    private_sdk_logger,
)


def test_batch_conflict_rolls_back_every_write_and_rejects_stale_writer(tmp_path):
    store = LocalDocumentStore(tmp_path / "documents.db")
    account = document("user:one", "account", "account", revision=0)
    store.batch("state", account["pk"], [Write("create", account["id"], account)])
    original = store.get("state", account["pk"], account["id"])
    receipt = document(account["pk"], "receipt", "receipt", value="must-not-exist")
    with pytest.raises(Conflict):
        store.batch(
            "state",
            account["pk"],
            [Write("create", "receipt", receipt), Write("replace", "account", account, "stale-etag")],
        )
    assert store.get("state", account["pk"], "receipt") is None
    assert store.get("state", account["pk"], "account") == original
    barrier = Barrier(2)

    def contender(revision):
        client = LocalDocumentStore(store.path)
        value = client.get("state", account["pk"], "account")
        changed = clean(value)
        changed["payload"]["revision"] = revision
        barrier.wait(timeout=3)
        try:
            client.batch("state", account["pk"], [Write("replace", "account", changed, value["_etag"])])
            return True
        except Conflict:
            return False

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(contender, [1, 2])) == [False, True]


@pytest.mark.parametrize(
    "writes,code",
    [
        ([Write("create", "x", document("other", "x", "test"))], "PARTITION_MISMATCH"),
        ([Write("replace", "x", document("p", "x", "test"))], "ETAG_REQUIRED"),
        ([Write("delete", "x", etag="*")], "ETAG_REQUIRED"),
        ([Write("create", "x", document("p", "x", "test", text="文" * 180000))], "TOO_LARGE"),
        ([Write("create", str(i), document("p", str(i), "test")) for i in range(101)], "BATCH_INVALID"),
        (
            [Write("create", str(i), document("p", str(i), "test", text="a" * 400000)) for i in range(3)],
            "BATCH_TOO_LARGE",
        ),
    ],
)
def test_invalid_batch_never_writes_a_partial_subset(tmp_path, writes, code):
    store = LocalDocumentStore(tmp_path / "documents.db")
    with pytest.raises(StoreError, match=code):
        store.batch("state", "p", writes)
    assert store.page("state", "p", "test") == []


def test_local_store_cannot_open_the_existing_business_database(tmp_path):
    path = tmp_path / "existing.db"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE users (id TEXT)")
        db.execute("INSERT INTO users VALUES ('keep')")
    path.chmod(0o600)
    with pytest.raises(StoreError, match="DATABASE_NOT_OWNED"):
        LocalDocumentStore(path)
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT * FROM users").fetchall() == [("keep",)]
        assert db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall() == [("users",)]


def test_paginated_partition_queries_do_not_mix_owners_or_kinds(tmp_path):
    store = LocalDocumentStore(tmp_path / "documents.db")
    for pk in ["user:one", "user:two"]:
        for start in [0, 100, 200]:
            rows = [document(pk, f"row:{i:04}", "outbox") for i in range(start, min(start + 100, 215))]
            store.batch("state", pk, [Write("create", row["id"], row) for row in rows])
        extra = document(pk, "row:extra", "secret-config")
        store.batch("state", pk, [Write("create", extra["id"], extra)])
    found = list(partition_items(store, "state", "user:one", "outbox"))
    assert len(found) == 215 and len({row["id"] for row in found}) == 215
    assert {row["pk"] for row in found} == {"user:one"}
    assert {row["kind"] for row in found} == {"outbox"}


class OfflineResponse(HttpResponse):
    def __init__(self, request, body, status=200, headers=None):
        super().__init__(request, None)
        self.status_code, self.reason = status, "Offline fixture"
        self.headers = {"content-type": "application/json", "x-ms-request-charge": "2.5", **(headers or {})}
        self.content_type, self.value = "application/json", json.dumps(body).encode()

    def body(self):
        return self.value


class OfflineTransport(HttpTransport):
    """All SDK network paths are captured; unexpected requests fail, never reach Azure."""

    def __init__(self):
        self.requests, self.batch_statuses = [], [200, 201]

    def open(self):
        pass

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def send(self, request, **kwargs):
        self.requests.append(request)
        path = urlsplit(request.url).path.rstrip("/")
        if not path:
            return OfflineResponse(
                request,
                {
                    "id": "offline",
                    "_self": "",
                    "enableMultipleWriteLocations": False,
                    "userConsistencyPolicy": {"defaultConsistencyLevel": "Strong"},
                    "writableLocations": [{"name": "East Asia", "databaseAccountEndpoint": endpoint}],
                    "readableLocations": [{"name": "East Asia", "databaseAccountEndpoint": endpoint}],
                },
            )
        if path == "/dbs/anke-sports/colls/state":
            return OfflineResponse(
                request,
                {
                    "id": "state",
                    "_rid": "offline-container",
                    "_self": "dbs/offline/colls/state/",
                    "partitionKey": {"paths": ["/pk"], "kind": "Hash", "version": 2},
                },
            )
        if path.endswith("/docs") and request.headers.get("x-ms-cosmos-is-batch-request") == "True":
            return OfflineResponse(
                request,
                [
                    {
                        "statusCode": status,
                        "requestCharge": 1.25,
                        "eTag": f"offline-{i}",
                        "resourceBody": {"id": f"item{i}"},
                    }
                    for i, status in enumerate(self.batch_statuses)
                ],
            )
        if path.endswith("/docs") and request.headers.get("x-ms-documentdb-isquery") == "true":
            return OfflineResponse(request, {"Documents": [document("p", "row:1", "outbox")], "_count": 1})
        if "/docs/" in path and request.method == "GET":
            return OfflineResponse(
                request, {"code": "NotFound", "message": "synthetic-private-not-found"}, status=404
            )
        raise AssertionError("SDK attempted an unexpected offline request: " + request.method + " " + path)


endpoint = "https://anke-sports-offline.documents.azure.com/"


class OfflineCredential:
    def get_token(self, *_, **__):
        return AccessToken("synthetic-offline-token", 9999999999)


def test_actual_sdk_serializes_atomic_batch_and_if_match_without_logging_payload(caplog):
    transport = OfflineTransport()
    with CosmosClient(
        endpoint,
        OfflineCredential(),
        transport=transport,
        consistency_level="Strong",
        retry_total=0,
        retry_write=0,
        logging_enable=False,
        logger=private_sdk_logger(),
    ) as client:
        store = CosmosDocumentStore(client, "anke-sports")
        row = document("user:private-fixture", "account", "account", secret="synthetic-private-value")
        job = document(row["pk"], "job:one", "outbox")
        with caplog.at_level(logging.INFO):
            store.batch(
                "state",
                row["pk"],
                [Write("replace", "account", row, '"etag-one"'), Write("create", "job:one", job)],
            )
        request = next(
            r for r in transport.requests if r.headers.get("x-ms-cosmos-is-batch-request") == "True"
        )
        sent = json.loads(request.body)
        assert sent[0]["operationType"] == "Replace" and sent[0]["ifMatch"] == '"etag-one"'
        assert sent[1]["operationType"] == "Create"
        assert json.loads(request.headers["x-ms-documentdb-partitionkey"]) == [row["pk"]]
        assert request.headers["x-ms-cosmos-batch-atomic"] == "True"
        assert request.headers["x-ms-consistency-level"] == "Strong"
        assert "ru=2.500" in caplog.text
        assert all(
            value not in caplog.text
            for value in [row["pk"], "synthetic-private-value", "synthetic-offline-token"]
        )
        transport.batch_statuses = [412, 424]
        with pytest.raises(Conflict):
            store.batch(
                "state",
                row["pk"],
                [Write("replace", "account", row, '"etag-one"'), Write("create", "job:one", job)],
            )


def test_actual_sdk_partition_query_and_not_found_are_bounded_and_read_only():
    transport = OfflineTransport()
    with CosmosClient(
        endpoint,
        OfflineCredential(),
        transport=transport,
        consistency_level="Strong",
        retry_total=0,
        logger=private_sdk_logger(),
    ) as client:
        store = CosmosDocumentStore(client, "anke-sports")
        assert store.get("state", "p", "missing") is None
        assert [row["id"] for row in store.page("state", "p", "outbox", after="row:0", limit=25)] == ["row:1"]
    queries = [r for r in transport.requests if r.headers.get("x-ms-documentdb-isquery") == "true"]
    assert len(queries) == 1
    assert json.loads(queries[0].headers["x-ms-documentdb-partitionkey"]) == ["p"]
    params = {p["name"]: p["value"] for p in json.loads(queries[0].body)["parameters"]}
    assert params == {"@limit": 25, "@kind": "outbox", "@after": "row:0"}


@pytest.mark.parametrize(
    "error,expected,retry",
    [
        (
            CosmosBatchOperationError(
                status_code=424,
                headers={},
                operation_responses=[{"statusCode": 424}, {"statusCode": 409}],
                message="synthetic-secret",
            ),
            Conflict,
            False,
        ),
        (
            CosmosHttpResponseError(
                status_code=429,
                response=OfflineResponse(None, {}, status=429, headers={"x-ms-retry-after-ms": "2500"}),
                message="synthetic-secret",
            ),
            StoreError,
            True,
        ),
        (CosmosHttpResponseError(status_code=403, message="synthetic-secret"), StoreError, False),
    ],
)
def test_sdk_errors_report_safe_codes_and_preserve_retry_after(error, expected, retry, caplog):
    class FailedContainer:
        def execute_item_batch(self, **kwargs):
            raise error

    store = object.__new__(CosmosDocumentStore)
    store.containers = {"state": FailedContainer()}
    row = document("p", "x", "test")
    with pytest.raises(expected) as caught:
        store.batch("state", "p", [Write("create", "x", row)])
    assert caught.value.retryable == retry
    if retry:
        assert caught.value.retry_after == 2.5
    assert "synthetic-secret" not in str(caught.value) + caplog.text


def test_actual_sdk_change_feed_resumes_across_ranges_and_preserves_empty_cursor(caplog):
    class ChangeTransport(OfflineTransport):
        def send(self, request, **kwargs):
            if len(self.requests) >= 20:
                raise AssertionError(
                    [
                        (
                            urlsplit(r.url).path,
                            r.headers.get("If-None-Match"),
                            r.headers.get("x-ms-documentdb-partitionkeyrangeid"),
                        )
                        for r in self.requests
                    ]
                )
            path = urlsplit(request.url).path.rstrip("/")
            if path.endswith("/pkranges"):
                self.requests.append(request)
                if request.headers.get("If-None-Match") == '"ranges"':
                    response = OfflineResponse(request, {}, status=304, headers={"etag": '"ranges"'})
                    response.value = b""
                    return response
                return OfflineResponse(
                    request,
                    {
                        "PartitionKeyRanges": [
                            {"id": "0", "minInclusive": "", "maxExclusive": "80"},
                            {"id": "1", "minInclusive": "80", "maxExclusive": "FF"},
                        ],
                        "_count": 2,
                    },
                    headers={"etag": '"ranges"'},
                )
            if path.endswith("/docs") and request.method == "GET":
                self.requests.append(request)
                partition = request.headers["x-ms-documentdb-partitionkeyrangeid"]
                if request.headers.get("If-None-Match") == '"1"':
                    response = OfflineResponse(request, {}, status=304, headers={"etag": '"1"'})
                    response.value = b""
                    return response
                return OfflineResponse(
                    request,
                    {"Documents": [document("p" + partition, "job:" + partition, "outbox")], "_count": 1},
                    headers={"etag": '"1"'},
                )
            return super().send(request, **kwargs)

    transport = ChangeTransport()
    with CosmosClient(
        endpoint,
        OfflineCredential(),
        transport=transport,
        consistency_level="Strong",
        retry_total=0,
        logger=private_sdk_logger(),
    ) as client:
        store = CosmosDocumentStore(client, "anke-sports")
        found, cursor = [], None
        with caplog.at_level(logging.INFO):
            for _ in range(5):
                rows, cursor = store.changes("state", cursor, limit=1)
                found.extend(row["id"] for row in rows)
                if not rows:
                    break
        assert sorted(found) == ["job:0", "job:1"]
        assert cursor and cursor != '"1"'  # Composite SDK token, not one range's ETag.
        empty, resumed = store.changes("state", cursor, limit=1)
        assert empty == [] and resumed == cursor
        assert "synthetic-offline-token" not in caplog.text and cursor not in caplog.text
    requests = [
        request for request in transport.requests if urlsplit(request.url).path.rstrip("/").endswith("/docs")
    ]
    assert {request.headers["x-ms-documentdb-partitionkeyrangeid"] for request in requests} == {"0", "1"}
    assert all(request.headers["x-ms-max-item-count"] == "1" for request in requests)
