"""Immutable partition-local values for large configurations and exact receipts."""

import json
import re

from app.document_store import Conflict, StoreError, Write, encode
from app.security import digest


INLINE_BYTES = 96_000
CHUNK_BYTES = 128_000


def text_chunks(text):
    data, start = text.encode(), 0
    while start < len(data):
        end = min(len(data), start + CHUNK_BYTES)
        while end < len(data) and data[end] & 0xC0 == 0x80:
            end -= 1
        yield data[start:end].decode()
        start = end


class Values:
    def __init__(self, store):
        self.store = store

    def pack(self, pk, name, value):
        if len(json.dumps(value, ensure_ascii=True, allow_nan=False).encode()) <= INLINE_BYTES:
            return {name: value}
        return {name + "_ref": self.put(pk, value)}

    def unpack(self, pk, name, payload):
        if (name in payload) == (name + "_ref" in payload):
            raise StoreError("DOCUMENT_VALUE_REFERENCE_INVALID")
        return payload[name] if name in payload else self.get(pk, payload[name + "_ref"])

    def put(self, pk, value):
        serialized = encode(value)
        ident = digest(serialized)
        if self.store.get("state", pk, "value:" + ident):
            self.get(pk, ident)
            return ident
        chunks = list(text_chunks(serialized))
        if len(chunks) > 3000:
            raise StoreError("DOCUMENT_VALUE_TOO_LARGE")
        pieces = []
        for index, text in enumerate(chunks):
            chunk_id = f"value:{ident}:{index:04}"
            row = {"id": chunk_id, "pk": pk, "kind": "value_chunk", "schema": 1, "payload": {"text": text}}
            try:
                self.store.batch("state", pk, [Write("create", chunk_id, row)])
            except Conflict:
                existing = self.store.get("state", pk, chunk_id)
                if not existing or existing["kind"] != "value_chunk" or existing["payload"] != row["payload"]:
                    raise StoreError("DOCUMENT_VALUE_INCOMPLETE", retryable=True) from None
            pieces.append({"id": chunk_id, "hash": digest(text)})
        manifest_id = "value:" + ident
        manifest = {
            "id": manifest_id,
            "pk": pk,
            "kind": "value_manifest",
            "schema": 1,
            "payload": {"pieces": pieces, "hash": ident, "byte_length": len(serialized.encode())},
        }
        try:
            self.store.batch("state", pk, [Write("create", manifest_id, manifest)])
        except Conflict:
            pass
        self.get(pk, ident)
        return ident

    def get(self, pk, ident):
        if not isinstance(ident, str) or not re.fullmatch(r"[a-f0-9]{64}", ident):
            raise StoreError("DOCUMENT_VALUE_REFERENCE_INVALID")
        manifest = self.store.get("state", pk, "value:" + ident)
        if not manifest or manifest["kind"] != "value_manifest" or manifest["payload"]["hash"] != ident:
            raise StoreError("DOCUMENT_VALUE_INCOMPLETE", retryable=True)
        chunks = []
        for expected in manifest["payload"]["pieces"]:
            row = self.store.get("state", pk, expected["id"])
            if not row or row["kind"] != "value_chunk" or digest(row["payload"]["text"]) != expected["hash"]:
                raise StoreError("DOCUMENT_VALUE_INCOMPLETE", retryable=True)
            chunks.append(row["payload"]["text"])
        serialized = "".join(chunks)
        if digest(serialized) != ident or len(serialized.encode()) != manifest["payload"]["byte_length"]:
            raise StoreError("DOCUMENT_VALUE_INCOMPLETE", retryable=True)
        return json.loads(serialized)
