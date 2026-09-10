import subprocess
import zipfile

import pytest

from scripts.package_functions import ROOT_FILES, build_package


def fixture_repository(tmp_path):
    root = tmp_path / "source"
    root.mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    for name in ROOT_FILES | {"app/main.py", "migrations/env.py"}:
        path = root / name
        path.parent.mkdir(exist_ok=True)
        path.write_text("fixture runtime")
        subprocess.run(["git", "add", name], cwd=root, check=True)
    return root


def test_archive_excludes_tracked_secrets_and_untracked_runtime(tmp_path):
    root = fixture_repository(tmp_path)
    for name in ("data/service-account.json", ".env", "local.settings.json", "app/secret.json"):
        path = root / name
        path.parent.mkdir(exist_ok=True)
        path.write_text("CANARY_MUST_NOT_SHIP")
        subprocess.run(["git", "add", name], cwd=root, check=True)
    (root / "app/untracked.py").write_text("CANARY_MUST_NOT_SHIP")
    archive = tmp_path / "first.zip"
    first = build_package(root, archive)
    second = build_package(root, tmp_path / "second.zip")
    assert first == second
    with zipfile.ZipFile(archive) as result:
        assert set(result.namelist()) == ROOT_FILES | {"app/main.py", "migrations/env.py"}
        assert all(b"CANARY" not in result.read(name) for name in result.namelist())


def test_archive_refuses_symlinked_tracked_source(tmp_path):
    root = fixture_repository(tmp_path)
    (root / "app/main.py").unlink()
    outside = tmp_path / "outside.py"
    outside.write_text("DO_NOT_READ")
    (root / "app/main.py").symlink_to(outside)
    with pytest.raises(RuntimeError, match="UNSAFE_SOURCE"):
        build_package(root, tmp_path / "unsafe.zip")
    assert not (tmp_path / "unsafe.zip").exists()


def test_archive_refuses_missing_entrypoint(tmp_path):
    root = fixture_repository(tmp_path)
    subprocess.run(["git", "rm", "--cached", "-q", "function_app.py"], cwd=root, check=True)
    with pytest.raises(RuntimeError, match="MISSING_RUNTIME_FILES"):
        build_package(root, tmp_path / "missing.zip")
