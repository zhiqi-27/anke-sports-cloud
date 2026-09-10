"""Build a deterministic Functions source archive from an explicit runtime allowlist."""

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import zipfile


ROOT_FILES = {"function_app.py", "host.json", "requirements.txt", "alembic.ini"}


def build_package(root: Path, output: Path) -> dict:
    root = root.resolve()
    tracked = subprocess.check_output(["git", "ls-files", "-z"], cwd=root).decode().split("\0")
    names = sorted(
        name for name in tracked
        if name in ROOT_FILES or (name.startswith(("app/", "migrations/")) and name.endswith(".py"))
    )
    if not ROOT_FILES.issubset(names) or "app/main.py" not in names or "migrations/env.py" not in names:
        raise RuntimeError("FUNCTIONS_PACKAGE_MISSING_RUNTIME_FILES")
    contents = {}
    for name in names:
        path = root / name
        if path.is_symlink() or not path.resolve().is_relative_to(root) or not path.is_file():
            raise RuntimeError("FUNCTIONS_PACKAGE_UNSAFE_SOURCE")
        # Parent symlinks must not pull untracked files into an otherwise tracked path.
        if any(parent.is_symlink() for parent in path.parents if parent != root and root in parent.parents):
            raise RuntimeError("FUNCTIONS_PACKAGE_UNSAFE_SOURCE")
        contents[name] = path.read_bytes()
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in contents.items():
            info = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, content)
    return {
        "format": "python-functions-source-remote-build-required",
        "sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
        "files": {name: hashlib.sha256(content).hexdigest() for name, content in contents.items()},
        "bytes": output.stat().st_size,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("data/anke-sports-functions.zip"))
    args = parser.parse_args()
    result = build_package(Path(__file__).resolve().parents[1], args.output)
    args.output.with_suffix(".manifest.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({key: value for key, value in result.items() if key != "files"}))


if __name__ == "__main__":
    main()
