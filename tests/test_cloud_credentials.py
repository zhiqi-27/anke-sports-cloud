import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from pydantic import SecretStr

from app import azure_queue, security


@pytest.fixture
def firebase_boundary(monkeypatch):
    import firebase_admin
    from firebase_admin import credentials

    config = SimpleNamespace(firebase_project_id="anke-fixture", firebase_credentials_json=SecretStr(""))
    monkeypatch.setattr(security, "settings", lambda: config)
    def missing(_):
        raise ValueError()
    monkeypatch.setattr(firebase_admin, "get_app", missing)
    initialize = MagicMock(return_value=SimpleNamespace(project_id="anke-fixture"))
    certificate = MagicMock(return_value=object())
    monkeypatch.setattr(firebase_admin, "initialize_app", initialize)
    monkeypatch.setattr(credentials, "Certificate", certificate)
    return config, initialize, certificate


def test_key_vault_json_creates_project_bound_firebase_identity(firebase_boundary):
    config, initialize, certificate = firebase_boundary
    payload = {"type": "service_account", "project_id": "anke-fixture", "private_key": "CANARY"}
    config.firebase_credentials_json = SecretStr(json.dumps(payload))
    assert security.firebase_app().project_id == "anke-fixture"
    certificate.assert_called_once_with(payload)
    assert initialize.call_args.kwargs["credential"] is certificate.return_value


@pytest.mark.parametrize("value", ["CANARY_INVALID_JSON", "[]", json.dumps({
    "type": "service_account", "project_id": "other-product", "private_key": "CANARY"})])
def test_bad_or_other_project_credentials_fail_without_echo(firebase_boundary, value):
    config, initialize, certificate = firebase_boundary
    config.firebase_credentials_json = SecretStr(value)
    with pytest.raises(ValueError) as result:
        security.firebase_app()
    assert str(result.value) == "FIREBASE_CREDENTIALS_INVALID_OR_WRONG_PROJECT"
    assert "CANARY" not in str(result.value)
    initialize.assert_not_called()
    certificate.assert_not_called()


def test_local_application_default_credentials_remain_available(firebase_boundary):
    _, initialize, certificate = firebase_boundary
    security.firebase_app()
    assert initialize.call_args.kwargs["credential"] is None
    certificate.assert_not_called()


@pytest.fixture
def queue_boundary(monkeypatch):
    for key in ("AzureQueueConnection", "AzureQueueConnection__queueServiceUri",
                "AzureQueueConnection__credential", "AzureQueueConnection__clientId"):
        monkeypatch.delenv(key, raising=False)
    identity, queue = MagicMock(), MagicMock()
    monkeypatch.setattr(azure_queue, "ManagedIdentityCredential", identity)
    monkeypatch.setattr(azure_queue, "QueueClient", queue)
    return identity, queue


def test_sender_uses_same_user_assigned_identity_as_trigger(monkeypatch, queue_boundary):
    identity, queue = queue_boundary
    monkeypatch.setenv("AzureQueueConnection__queueServiceUri", "https://ankefixture.queue.core.windows.net")
    monkeypatch.setenv("AzureQueueConnection__credential", "managedidentity")
    monkeypatch.setenv("AzureQueueConnection__clientId", "dedicated-identity")
    with azure_queue.outbox_queue() as client:
        client.send_message("fixture")
    identity.assert_called_once_with(client_id="dedicated-identity")
    assert queue.call_args.kwargs["credential"] is identity.return_value.__enter__.return_value
    queue.from_connection_string.assert_not_called()
    identity.return_value.__exit__.assert_called_once()
    queue.return_value.__exit__.assert_called_once()


def test_local_connection_does_not_request_cloud_identity(monkeypatch, queue_boundary):
    identity, queue = queue_boundary
    monkeypatch.setenv("AzureQueueConnection", "fixture connection")
    with azure_queue.outbox_queue():
        pass
    identity.assert_not_called()
    assert queue.from_connection_string.call_args.args == ("fixture connection", "anke-sports-jobs")


@pytest.mark.parametrize("endpoint,connection,mode", [
    ("", "", ""),
    ("https://ankefixture.queue.core.windows.net", "fixture", "managedidentity"),
    ("http://attacker.example", "", "managedidentity"),
    ("https://ankefixture.queue.core.windows.net", "", ""),
])
def test_invalid_connection_never_contacts_identity_or_storage(monkeypatch, queue_boundary,
                                                             endpoint, connection, mode):
    identity, queue = queue_boundary
    monkeypatch.setenv("AzureQueueConnection", connection)
    monkeypatch.setenv("AzureQueueConnection__queueServiceUri", endpoint)
    monkeypatch.setenv("AzureQueueConnection__credential", mode)
    with pytest.raises(RuntimeError):
        with azure_queue.outbox_queue():
            pass
    identity.assert_not_called()
    queue.assert_not_called()
    queue.from_connection_string.assert_not_called()
