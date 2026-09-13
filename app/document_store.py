"""Partition-scoped document operations. No SQL-session compatibility or fallback.

Cosmos uses native transactional batches; the explicit disk adapter is only for
local development and does not claim Cosmos consistency, RU or recovery proof.
"""

from contextlib import contextmanager
from dataclasses import dataclass
import json
import logging
from pathlib import Path
import re
import sqlite3
import stat
import time
from typing import Literal, Protocol
from uuid import uuid4


MAX_DOCUMENT_BYTES = 512_000
MAX_BATCH_BYTES = 1_000_000  # Conservative margin below the Python SDK's serialized batch limit.
CONTAINERS = {"state", "indexes"}


class StoreError(Exception):
    """Safe diagnostics only: never include an SDK response, document or token."""

    def __init__(self, code, *, retryable=False, retry_after=None):
        super().__init__(code)
        self.code, self.retryable, self.retry_after = code, retryable, retry_after


class Conflict(StoreError):
    def __init__(self):
        super().__init__("DOCUMENT_CONFLICT")


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def valid_key(value):
    return (
        isinstance(value, str)
        and 0 < len(value.encode()) <= 512
        and not re.search(r"[/\\?#\x00-\x1f]", value)
    )


@dataclass(frozen=True)
class Write:
    operation: Literal["create", "replace", "delete"]
    id: str
    body: dict | None = None
    etag: str | None = None


def check_batch(container, pk, writes):
    if container not in CONTAINERS or not valid_key(pk) or not 1 <= len(writes) <= 100:
        raise StoreError("DOCUMENT_BATCH_INVALID")
    ids, size = set(), 0
    for item in writes:
        if not valid_key(item.id) or item.id in ids or item.operation not in {"create", "replace", "delete"}:
            raise StoreError("DOCUMENT_BATCH_INVALID")
        ids.add(item.id)
        if item.operation != "create" and (not item.etag or item.etag == "*"):
            raise StoreError("DOCUMENT_ETAG_REQUIRED")
        if item.operation == "create" and item.etag is not None:
            raise StoreError("DOCUMENT_BATCH_INVALID")
        if item.operation != "delete":
            if not item.body or item.body.get("id") != item.id or item.body.get("pk") != pk:
                raise StoreError("DOCUMENT_PARTITION_MISMATCH")
            if any(key.startswith("_") for key in item.body):
                raise StoreError("DOCUMENT_SYSTEM_FIELD_WRITE")
            try:
                # The SDK defaults to escaped ASCII on the wire; count that larger form.
                size_bytes = len(
                    json.dumps(item.body, ensure_ascii=True, separators=(",", ":"), allow_nan=False).encode()
                )
            except (ValueError, TypeError):
                raise StoreError("DOCUMENT_JSON_INVALID") from None
            if size_bytes > MAX_DOCUMENT_BYTES:
                raise StoreError("DOCUMENT_TOO_LARGE")
            size += size_bytes
        size += 1024 + len(item.id.encode()) + len((item.etag or "").encode())
    if size > MAX_BATCH_BYTES:
        raise StoreError("DOCUMENT_BATCH_TOO_LARGE")


def clean(document):
    return {key: value for key, value in document.items() if not key.startswith("_")}


class DocumentStore(Protocol):
    def get(self, container: str, pk: str, ident: str) -> dict | None: ...
    def batch(self, container: str, pk: str, writes: list[Write]) -> None: ...
    def page(
        self, container: str, pk: str, kind: str, *, after: str = "", limit: int = 100
    ) -> list[dict]: ...
    def changes(self, container: str, cursor=None, *, limit=100) -> tuple[list[dict], str | None]: ...


def partition_items(store, container, pk, kind):
    """Bound every request to one partition and page; callers consume lazily."""
    after = ""
    while rows := store.page(container, pk, kind, after=after):
        yield from rows
        after = rows[-1]["id"]


class LocalDocumentStore:
    """Explicit local adapter with independent connections and atomic batches."""

    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.is_symlink():
            raise StoreError("LOCAL_DOCUMENT_PATH_INVALID")
        if not self.path.exists():
            import os

            fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.close(fd)
        if stat.S_IMODE(self.path.stat().st_mode) != 0o600:
            raise StoreError("LOCAL_DOCUMENT_FILE_REQUIRES_0600")
        with self.connection() as db:
            tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            marker = db.execute("PRAGMA application_id").fetchone()[0]
            if tables and (not tables <= {"documents", "document_changes"} or marker != 0x414E4B44):
                raise StoreError("LOCAL_DOCUMENT_DATABASE_NOT_OWNED")
            db.execute("PRAGMA application_id=1095650116")
            db.execute("PRAGMA journal_mode=WAL")
            db.execute(
                "CREATE TABLE IF NOT EXISTS documents (bucket TEXT NOT NULL, pk TEXT NOT NULL, "
                "id TEXT NOT NULL, kind TEXT NOT NULL, body TEXT NOT NULL, etag TEXT NOT NULL, "
                "PRIMARY KEY(bucket,pk,id))"
            )
            db.execute("CREATE INDEX IF NOT EXISTS documents_kind ON documents(bucket,pk,kind,id)")
            db.execute("BEGIN IMMEDIATE")
            try:
                exists = db.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='document_changes'"
                ).fetchone()
                db.execute(
                    "CREATE TABLE IF NOT EXISTS document_changes (seq INTEGER PRIMARY KEY, bucket TEXT NOT NULL, pk TEXT NOT NULL, id TEXT NOT NULL)"
                )
                db.execute("CREATE INDEX IF NOT EXISTS changes_bucket ON document_changes(bucket,seq)")
                if not exists:
                    db.execute(
                        "INSERT INTO document_changes(bucket,pk,id) SELECT bucket,pk,id FROM documents ORDER BY bucket,pk,id"
                    )
                db.commit()
            except BaseException:
                db.rollback()
                raise

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        try:
            yield db
        finally:
            db.close()

    def get(self, container, pk, ident):
        if container not in CONTAINERS or not valid_key(pk) or not valid_key(ident):
            raise StoreError("DOCUMENT_KEY_INVALID")
        with self.connection() as db:
            row = db.execute(
                "SELECT body,etag FROM documents WHERE bucket=? AND pk=? AND id=?", (container, pk, ident)
            ).fetchone()
        return {**json.loads(row[0]), "_etag": row[1]} if row else None

    def batch(self, container, pk, writes):
        check_batch(container, pk, writes)
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                for item in writes:
                    old = db.execute(
                        "SELECT etag FROM documents WHERE bucket=? AND pk=? AND id=?",
                        (container, pk, item.id),
                    ).fetchone()
                    if (item.operation == "create" and old) or (
                        item.operation != "create" and (not old or old[0] != item.etag)
                    ):
                        raise Conflict()
                    if item.operation == "delete":
                        db.execute(
                            "DELETE FROM documents WHERE bucket=? AND pk=? AND id=?", (container, pk, item.id)
                        )
                    else:
                        db.execute(
                            "INSERT OR REPLACE INTO documents VALUES (?,?,?,?,?,?)",
                            (
                                container,
                                pk,
                                item.id,
                                item.body.get("kind", ""),
                                encode(item.body),
                                uuid4().hex,
                            ),
                        )
                    # Only identity metadata: do not retain historical private payloads.
                    db.execute(
                        "INSERT INTO document_changes(bucket,pk,id) VALUES (?,?,?)", (container, pk, item.id)
                    )
                db.commit()
            except BaseException:
                db.rollback()
                raise

    def page(self, container, pk, kind, *, after="", limit=100):
        if container not in CONTAINERS or not valid_key(pk) or not 1 <= limit <= 1000:
            raise StoreError("DOCUMENT_QUERY_INVALID")
        with self.connection() as db:
            rows = db.execute(
                "SELECT body,etag FROM documents WHERE bucket=? AND pk=? AND (? IS NULL OR kind=?) AND id>? "
                "ORDER BY id LIMIT ?",
                (container, pk, kind, kind, after, limit),
            ).fetchall()
        return [{**json.loads(row[0]), "_etag": row[1]} for row in rows]

    def close(self):
        pass

    def changes(self, container, cursor=None, *, limit=100):
        if (
            container not in CONTAINERS
            or not 1 <= limit <= 1000
            or (cursor is not None and not re.fullmatch(r"[0-9]{1,20}", cursor))
        ):
            raise StoreError("CHANGE_CURSOR_INVALID")
        with self.connection() as db:
            rows = db.execute(
                "SELECT c.seq,d.body,d.etag FROM document_changes c LEFT JOIN documents d "
                "ON c.bucket=d.bucket AND c.pk=d.pk AND c.id=d.id "
                "WHERE c.bucket=? AND c.seq>? ORDER BY c.seq LIMIT ?",
                (container, int(cursor or 0), limit),
            ).fetchall()
        return (
            [{**json.loads(row[1]), "_etag": row[2]} for row in rows if row[1]],
            str(rows[-1][0]) if rows else cursor,
        )


class CosmosDocumentStore:
    def __init__(self, client, database, *, state="state", indexes="indexes", credential=None):
        self.client, self.credential = client, credential
        db = client.get_database_client(database)
        self.containers = {
            "state": db.get_container_client(state),
            "indexes": db.get_container_client(indexes),
        }

    def _call(self, operation, container, invoke):
        from azure.core.exceptions import AzureError
        from azure.cosmos.exceptions import CosmosBatchOperationError, CosmosHttpResponseError

        if container not in self.containers:
            raise StoreError("DOCUMENT_CONTAINER_INVALID")
        charge, status, started = 0.0, 200, time.monotonic()

        def record(headers, _):
            nonlocal charge
            try:
                charge += float(headers.get("x-ms-request-charge", 0))
            except (ValueError, TypeError):
                pass

        try:
            return invoke(self.containers[container], record)
        except (CosmosHttpResponseError, CosmosBatchOperationError) as error:
            status = error.status_code or 503
            # A batch may report FailedDependency globally: inspect the actual failed operation.
            for item in getattr(error, "operation_responses", None) or []:
                candidate = item.get("statusCode", 0)
                if candidate >= 400 and candidate != 424:
                    status = candidate
                    break
            if status in {409, 412}:
                raise Conflict() from None
            if status == 404 and operation == "read":
                return None
            retry_after = None
            try:
                retry_after = float((error.headers or {}).get("x-ms-retry-after-ms", "")) / 1000
            except (ValueError, TypeError):
                pass
            raise StoreError(
                "DOCUMENT_THROTTLED" if status == 429 else "DOCUMENT_UNAVAILABLE",
                retryable=status in {408, 410, 429, 449} or status >= 500,
                retry_after=retry_after,
            ) from None
        except AzureError:
            status = 503
            raise StoreError("DOCUMENT_UNAVAILABLE", retryable=True) from None
        finally:
            # Never log the partition, URL, document, token, SDK exception or identity.
            logging.info(
                "DOCUMENT_OPERATION operation=%s container=%s status=%d ru=%.3f elapsed_ms=%.1f",
                operation,
                container,
                status,
                charge,
                (time.monotonic() - started) * 1000,
            )

    def get(self, container, pk, ident):
        if not valid_key(pk) or not valid_key(ident):
            raise StoreError("DOCUMENT_KEY_INVALID")
        return self._call(
            "read", container, lambda c, hook: c.read_item(item=ident, partition_key=pk, response_hook=hook)
        )

    def batch(self, container, pk, writes):
        check_batch(container, pk, writes)
        operations = []
        for item in writes:
            args = (
                (item.body,)
                if item.operation == "create"
                else ((item.id, item.body) if item.operation == "replace" else (item.id,))
            )
            operations.append((item.operation, args, {"if_match_etag": item.etag} if item.etag else {}))
        self._call(
            "batch",
            container,
            lambda c, hook: c.execute_item_batch(
                batch_operations=operations, partition_key=pk, retry_write=0, response_hook=hook
            ),
        )

    def page(self, container, pk, kind, *, after="", limit=100):
        if not valid_key(pk) or not 1 <= limit <= 1000:
            raise StoreError("DOCUMENT_QUERY_INVALID")
        return self._call(
            "query",
            container,
            lambda c, hook: list(
                c.query_items(
                    query="SELECT TOP @limit * FROM c WHERE (IS_NULL(@kind) OR c.kind=@kind) AND c.id>@after ORDER BY c.id",
                    parameters=[
                        {"name": "@limit", "value": limit},
                        {"name": "@kind", "value": kind},
                        {"name": "@after", "value": after},
                    ],
                    partition_key=pk,
                    max_item_count=limit,
                    response_hook=hook,
                )
            ),
        )

    def close(self):
        self.client.close()
        if self.credential:
            self.credential.close()

    def changes(self, container, cursor=None, *, limit=100):
        """One latest-version page; dispatcher owns an isolated SDK client.

        SDK 4.17 stores the composite continuation in last_response_headers,
        including empty 304 pages. Do not share this client with API threads.
        """
        if not 1 <= limit <= 1000 or (
            cursor is not None and (not isinstance(cursor, str) or len(cursor) > 200_000)
        ):
            raise StoreError("CHANGE_CURSOR_INVALID")

        def invoke(c, hook):
            options = (
                {"continuation": cursor} if cursor else {"start_time": "Beginning", "mode": "LatestVersion"}
            )
            # SDK 4.17 rejects its own bootstrap continuation while an unvisited
            # range still has token=None. Consume one initial round using public
            # APIs; never parse, edit or synthesize an opaque SDK continuation.
            rounds = max(1, len(list(c.read_feed_ranges()))) if cursor is None else 1
            if rounds > 100:
                raise StoreError("CHANGE_BOOTSTRAP_RANGE_LIMIT")
            pages = c.query_items_change_feed(
                max_item_count=max(1, limit // rounds), response_hook=hook, **options
            ).by_page()
            rows = []
            for _ in range(rounds):
                page = list(next(pages, []))
                rows.extend(page)
                if not page:
                    break
            continuation = self.client.client_connection.last_response_headers.get("etag")
            if not continuation:
                raise StoreError("CHANGE_CURSOR_MISSING", retryable=True)
            return rows, continuation

        return self._call("changes", container, invoke)


def private_sdk_logger():
    # logging_enable=False does not disable CosmosHttpLoggingPolicy in SDK 4.17.
    # Use a dedicated disabled logger, without changing the host's other loggers.
    logger = logging.Logger("anke_sports.cosmos.transport")
    logger.disabled, logger.propagate = True, False
    return logger


def open_cosmos_store(config):
    """Explicit MI in Azure; explicit tenant-bound CLI only in local mode. No keys."""
    from azure.cosmos import CosmosClient
    from azure.identity import AzureCliCredential, ManagedIdentityCredential

    if not re.fullmatch(
        r"https://anke-sports-[a-z0-9-]+\.documents\.azure\.com(?::443)?/?", config.cosmos_endpoint
    ):
        raise StoreError("COSMOS_INDEPENDENT_ENDPOINT_REQUIRED")
    if config.cosmos_auth == "azure_cli":
        if config.env != "local" or not config.cosmos_tenant_id or not config.cosmos_subscription_id:
            raise StoreError("COSMOS_EXPLICIT_LOCAL_IDENTITY_REQUIRED")
        credential = AzureCliCredential(
            tenant_id=config.cosmos_tenant_id, subscription=config.cosmos_subscription_id
        )
    elif config.cosmos_auth == "managed_identity" and config.cosmos_client_id:
        credential = ManagedIdentityCredential(client_id=config.cosmos_client_id)
    else:
        raise StoreError("COSMOS_MANAGED_IDENTITY_REQUIRED")
    try:
        client = CosmosClient(
            config.cosmos_endpoint,
            credential,
            connection_timeout=10,
            timeout=20,
            consistency_level="Strong",
            retry_total=3,
            retry_write=0,
            enable_diagnostics_logging=False,
            logging_enable=False,
            logger=private_sdk_logger(),
        )
        return CosmosDocumentStore(
            client,
            config.cosmos_database,
            state=config.cosmos_state_container,
            indexes=config.cosmos_index_container,
            credential=credential,
        )
    except Exception:
        credential.close()
        raise StoreError("COSMOS_CLIENT_UNAVAILABLE", retryable=True) from None


def open_document_store(config):
    if config.storage_backend == "cosmos":
        return open_cosmos_store(config)
    if config.storage_backend == "documents-local" and config.env == "local":
        return LocalDocumentStore(config.document_local_path)
    raise StoreError("DOCUMENT_BACKEND_NOT_SELECTED")
