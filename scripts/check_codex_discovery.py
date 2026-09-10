"""Check local MCP discovery with the installed Codex client, without an agent turn.

Uses documented app-server JSON-RPC. Credentials stay in Codex's credential store;
only an allow-listed summary is printed. No config files or threads are created.
"""

import argparse
from contextlib import contextmanager
import json
from pathlib import Path
from queue import Empty, Queue
import re
import shutil
import subprocess
from threading import Thread
import time
from urllib.parse import urlsplit


PUBLIC_TOOLS = {"search_sources", "get_schedule", "get_event"}
PRIVATE_TOOLS = PUBLIC_TOOLS | {
    "get_my_calendar",
    "update_follows",
    "add_creator",
    "attach_event_link",
    "remove_event_link",
    "export_config",
    "import_config",
    "get_calendar_feed",
}


class ProbeError(RuntimeError):
    """Errors must not include raw RPC messages, config, URLs or credentials."""


@contextmanager
def client(binary, overrides):
    args = [binary]
    for override in overrides:
        args.extend(["-c", override])
    process = subprocess.Popen(
        args + ["app-server", "--stdio"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        bufsize=1,
    )
    messages = Queue()

    def consume():
        for line in process.stdout:
            try:
                messages.put(json.loads(line))
            except ValueError:
                messages.put(None)
        messages.put(None)

    reader = Thread(target=consume, daemon=True)
    reader.start()
    request_id = 0

    def rpc(method, params):
        nonlocal request_id
        request_id += 1
        process.stdin.write(json.dumps({"id": request_id, "method": method, "params": params}) + "\n")
        process.stdin.flush()
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            try:
                response = messages.get(timeout=max(0.01, deadline - time.monotonic()))
            except Empty:
                break
            if response is None:
                raise ProbeError("Codex protocol stream closed or was not JSON")
            if response.get("id") == request_id:
                if "error" in response:
                    raise ProbeError(f"{method}: Codex RPC error (details withheld)")
                return response["result"]
        raise ProbeError(f"{method}: timed out after 30 seconds")

    try:
        rpc(
            "initialize",
            {
                "clientInfo": {"name": "anke_sports_local_acceptance", "version": "0.1.0"},
                "capabilities": {"experimentalApi": True},
            },
        )
        process.stdin.write(json.dumps({"method": "initialized", "params": {}}) + "\n")
        process.stdin.flush()
        yield rpc
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
        reader.join(timeout=1)
        for stream in (process.stdin, process.stdout):
            stream.close()


def isolated_overrides(binary, name, url):
    """Process-only configuration; plugin names must be quoted inside TOML tables."""
    with client(binary, []) as rpc:
        config = rpc("config/read", {})["config"]
        servers = config.get("mcp_servers", {})
    if name in servers:
        raise ProbeError("Use a unique probe name; do not reuse an existing server configuration")
    disabled = ",".join(f"{json.dumps(n)}={{enabled=false}}" for n in servers)
    plugins = ",".join(f"{json.dumps(n)}={{enabled=false}}" for n in config.get("plugins", {}))
    overrides = [f"mcp_servers={{{disabled}}}", f"plugins={{{plugins}}}", "features.apps=false"]
    overrides.extend(
        [
            f"mcp_servers.{name}.url={json.dumps(url)}",
            f"mcp_servers.{name}.enabled=true",
            f"mcp_servers.{name}.startup_timeout_sec=10",
        ]
    )
    return overrides


def verify_isolation(rpc, name):
    effective = rpc("config/read", {})["config"]
    if (
        {n for n, v in effective.get("mcp_servers", {}).items() if v.get("enabled", True)} != {name}
        or any(v.get("enabled", True) for v in effective.get("plugins", {}).values())
        or effective.get("features", {}).get("apps") is not False
    ):
        raise ProbeError("Could not disable unrelated MCP servers, plugins and apps in this process")


def inventory(rpc, name, thread_id=None):
    params = {"detail": "toolsAndAuthOnly", "limit": 100}
    if thread_id:
        params["threadId"] = thread_id
    items = []
    while True:
        result = rpc("mcpServerStatus/list", params)
        items.extend(result["data"])
        cursor = result.get("nextCursor")
        if not cursor:
            break
        if cursor == params.get("cursor"):
            raise ProbeError("Codex inventory repeated a pagination cursor")
        params["cursor"] = cursor
    # Unscoped inventory has null runtimeStatus even for disabled entries;
    # thread-scoped inventory reports the actual runtime. Neither may expose
    # tools from another server. Null is allowed only for a configured disable.
    disabled = {
        n for n, v in rpc("config/read", {})["config"].get("mcp_servers", {}).items()
        if v.get("enabled") is False
    }
    if any(
        row.get("tools") or not (
            row.get("runtimeStatus") == "disabled" or (
                not thread_id and row.get("runtimeStatus") is None and row["name"] in disabled
            )
        )
        for row in items if row["name"] != name
    ):
        raise ProbeError("Unrelated MCP runtime was not disabled; refusing tool calls")
    rows = [item for item in items if item["name"] == name]
    if len(rows) != 1:
        raise ProbeError("Expected exactly one result for the requested server")
    return rows[0]


def check(binary, name, url, expect):
    overrides = isolated_overrides(binary, name, url)
    with client(binary, overrides) as rpc:
        verify_isolation(rpc, name)
        row = inventory(rpc, name)
    expected = (
        set() if expect == "unavailable" else PUBLIC_TOOLS if url.endswith("/public") else PRIVATE_TOOLS
    )
    actual = {tool["name"] for tool in row["tools"].values()}
    passed = actual == expected
    if expect == "available" and not url.endswith("/public"):
        passed = passed and row["authStatus"] == "oAuth"
    version = subprocess.run([binary, "--version"], capture_output=True, text=True, check=True).stdout.strip()
    return {
        "client": version,
        "transport": "Streamable HTTP",
        "endpoint": url,
        "expect": expect,
        "passed": passed,
        "auth_status": row["authStatus"],
        "tools": sorted(actual),
        "tool_count": len(actual),
        "evidence": "Installed Codex app-server MCP inventory; nonempty tools confirm HTTP discovery",
        "tool_calls_performed": False,
        "model_turns_started": False,
        "configuration_files_changed": False,
        "unrelated_mcp_config_disabled": True,
        "unrelated_tools_exposed": False,
        "limitations": [
            "No model tool selection, calendar query or write was exercised",
            "Unavailable alone does not establish why authentication or startup failed",
        ],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--codex", default=shutil.which("codex"))
    parser.add_argument("--name", default="anke_sports_local_check")
    parser.add_argument("--url", default="http://localhost:8787/mcp")
    parser.add_argument("--expect", choices=["available", "unavailable"], default="available")
    parser.add_argument("--output", type=Path, help="Optional new sanitized JSON evidence file")
    args = parser.parse_args()
    url = urlsplit(args.url)
    if not args.codex or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,79}", args.name):
        parser.error("A Codex executable and a simple, unique server name are required")
    if (
        url.scheme != "http"
        or url.hostname not in {"localhost", "127.0.0.1", "::1"}
        or url.username
        or url.password
        or url.query
        or url.fragment
        or url.path not in {"/mcp", "/mcp/public"}
    ):
        parser.error("This probe accepts only loopback HTTP MCP endpoints without credentials or query")
    if args.output and args.output.exists():
        parser.error("Choose a new output file; existing evidence is never overwritten")
    try:
        result = check(args.codex, args.name, args.url, args.expect)
    except (ProbeError, OSError, KeyError, ValueError, subprocess.SubprocessError) as exc:
        print(
            json.dumps(
                {
                    "passed": False,
                    "error": str(exc)
                    if isinstance(exc, ProbeError)
                    else "Local Codex probe failed; raw details withheld",
                }
            )
        )
        return 1
    serialized = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        with args.output.open("x") as output:
            output.write(serialized)
    print(serialized, end="")
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
