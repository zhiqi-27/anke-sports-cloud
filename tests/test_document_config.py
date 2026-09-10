import json
import os
import subprocess
import sys
from types import SimpleNamespace

import pytest

from app.config import Settings
from app.document_store import StoreError, open_cosmos_store, open_document_store


@pytest.mark.parametrize(
    "updates,code",
    [
        ({"storage_backend": "sql"}, "DOCUMENT_BACKEND_NOT_SELECTED"),
        ({"storage_backend": "documents-local", "env": "production"}, "DOCUMENT_BACKEND_NOT_SELECTED"),
        ({"cosmos_endpoint": "https://other-product.documents.azure.com"}, "INDEPENDENT_ENDPOINT_REQUIRED"),
        ({"cosmos_endpoint": "http://anke-sports-dev.documents.azure.com"}, "INDEPENDENT_ENDPOINT_REQUIRED"),
        ({"cosmos_auth": "azure_cli", "env": "production"}, "EXPLICIT_LOCAL_IDENTITY_REQUIRED"),
        ({"cosmos_client_id": ""}, "MANAGED_IDENTITY_REQUIRED"),
    ],
)
def test_no_implicit_credentials_or_sql_fallback(updates, code):
    config = Settings(
        _env_file=None,
        storage_backend="cosmos",
        env="local",
        cosmos_endpoint="https://anke-sports-dev.documents.azure.com",
        cosmos_client_id="fixture",
    )
    config = config.model_copy(update=updates)
    with pytest.raises(StoreError, match=code):
        open_document_store(config)


def test_production_sdk_uses_only_dedicated_mi_and_keeps_strong_reads(monkeypatch):
    created = {}

    class Credential:
        def __init__(self, **kwargs):
            created["identity"] = kwargs

        def close(self):
            created["closed_identity"] = True

    class Client:
        def __init__(self, endpoint, credential, **kwargs):
            created.update(endpoint=endpoint, credential=credential, options=kwargs)

        def get_database_client(self, name):
            created["database"] = name
            return SimpleNamespace(get_container_client=lambda name: name)

        def close(self):
            created["closed_client"] = True

    monkeypatch.setattr("azure.identity.ManagedIdentityCredential", Credential)
    monkeypatch.setattr("azure.cosmos.CosmosClient", Client)
    config = Settings(
        _env_file=None,
        storage_backend="cosmos",
        env="production",
        cosmos_endpoint="https://anke-sports-dev.documents.azure.com",
        cosmos_client_id="dedicated-id",
    )
    store = open_cosmos_store(config)
    assert created["identity"] == {"client_id": "dedicated-id"}
    assert created["options"]["consistency_level"] == "Strong"
    assert created["options"]["retry_write"] == 0
    assert created["options"]["logger"].disabled and not created["options"]["logger"].propagate
    assert store.containers == {"state": "state", "indexes": "indexes"}
    store.close()
    assert created["closed_identity"] and created["closed_client"]


def test_document_process_rejects_import_of_legacy_sql_runtime(tmp_path):
    # Even a valid existing database URL must not turn into a document-mode fallback.
    env = {
        **os.environ,
        "ANKE_SPORTS_ENV": "local",
        "ANKE_SPORTS_STORAGE_BACKEND": "documents-local",
        "ANKE_SPORTS_DATABASE_URL": "sqlite:///" + str(tmp_path / "must-not-be-created.db"),
    }
    code = """import json
try:
    import app.db
except RuntimeError as error:
    assert str(error).startswith('SQL_ADAPTER_DISABLED')
    print(json.dumps({'sql_runtime_rejected':True}))
else:
    raise SystemExit(2)
"""
    result = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {"sql_runtime_rejected": True}
    assert not (tmp_path / "must-not-be-created.db").exists()
