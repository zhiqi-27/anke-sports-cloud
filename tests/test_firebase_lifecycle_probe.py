"""Preflight and cleanup ownership checks only; never contact real Firebase in pytest."""

import json
from types import SimpleNamespace

import pytest

from experiments.firebase_lifecycle import (
    CheckFailed, PREFIX, PROJECT, credentials, exclusive_json, fixture, owns_record, private_json,
)


@pytest.mark.parametrize("mismatch", ["service", "web"])
def test_wrong_project_cannot_supply_probe_credentials(tmp_path, mismatch):
    service, web = tmp_path / "service.json", tmp_path / "web.json"
    exclusive_json(service, {"project_id": "other-project" if mismatch == "service" else PROJECT,
                             "type": "service_account", "private_key": "synthetic-secret"})
    exclusive_json(web, {"projectId": "other-project" if mismatch == "web" else PROJECT,
                         "apiKey": "synthetic-key", "authDomain": PROJECT + ".firebaseapp.com"})
    with pytest.raises(CheckFailed, match="independent_project_mismatch"):
        credentials(service, web)


def test_invalid_secret_json_does_not_echo_its_contents(tmp_path):
    path = tmp_path / "broken.json"
    path.write_text('{"private_key": "synthetic-do-not-print')
    path.chmod(0o600)
    with pytest.raises(CheckFailed) as error:
        private_json(path)
    assert str(error.value) == "credential_json_invalid"
    assert "synthetic-do-not-print" not in str(error.value)


def test_world_readable_and_symlink_credentials_rejected(tmp_path):
    path = tmp_path / "config.json"
    path.write_text('{}')
    path.chmod(0o644)
    with pytest.raises(CheckFailed, match="credential_file_requires_0600"):
        private_json(path)
    path.chmod(0o600)
    link = tmp_path / "linked.json"
    link.symlink_to(path)
    with pytest.raises(CheckFailed, match="credential_file_required"):
        private_json(link)


@pytest.mark.parametrize("change", [
    {"uid": "existing-real-owner"}, {"display_name": "Unrelated user"},
    {"email": "unrelated@example.invalid"}, {"provider_data": ["google.com"]},
])
def test_cleanup_refuses_unrelated_or_linked_identity(change):
    uid, marker = PREFIX + "a" * 32, "specific-run-marker"
    values = {"uid": uid, "display_name": marker, "email": None,
              "phone_number": None, "provider_data": []}
    assert owns_record(SimpleNamespace(**values), uid, marker)
    values.update(change)
    assert not owns_record(SimpleNamespace(**values), uid, marker)


def test_journal_never_overwrites_an_existing_cleanup_target(tmp_path):
    path = tmp_path / "cleanup.json"
    exclusive_json(path, {"uid": "preserve-existing-target"})
    with pytest.raises(FileExistsError):
        exclusive_json(path, {"uid": "replacement"})
    assert json.loads(path.read_text()) == {"uid": "preserve-existing-target"}


@pytest.mark.parametrize("key,value", [
    ("FIREBASE_AUTH_EMULATOR_HOST", "127.0.0.1:9099"), ("WEBSITE_INSTANCE_ID", "cloud-host"),
    ("ANKE_SPORTS_ENV", "production"),
])
def test_probe_refuses_emulator_and_deployed_environments(monkeypatch, key, value):
    monkeypatch.setenv(key, value)
    with pytest.raises(CheckFailed, match="fresh_local_process_without_emulator_required"):
        with fixture({}, {}):
            raise AssertionError("Preflight allowed unsafe environment")
