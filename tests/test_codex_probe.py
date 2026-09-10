"""Probe isolation must fail before calling a user's unrelated MCP tools."""

from contextlib import contextmanager
import tomllib

import pytest

from scripts import check_codex_discovery as probe


def row(name, state=None, tools=None):
    return {"name": name, "runtimeStatus": state, "tools": tools or {}}


def rpc_for(rows, config=None):
    def rpc(method, params):
        if method == "config/read":
            return {"config": config or {"mcp_servers": {"another": {"enabled": False}}}}
        assert method == "mcpServerStatus/list"
        return {"data": rows, "nextCursor": None}
    return rpc


def test_plugin_table_quotes_are_toml_not_cli_keypath_quotes(monkeypatch):
    config = {"mcp_servers": {"another": {"enabled": True}},
              "plugins": {"some.plugin@publisher": {"enabled": True}}}

    @contextmanager
    def fake_client(binary, overrides):
        yield rpc_for([], config)

    monkeypatch.setattr(probe, "client", fake_client)
    overrides = probe.isolated_overrides("codex", "fixture", "http://localhost:8787/mcp")
    # Codex parses each -c value separately and merges; an inline TOML table
    # cannot be reopened by concatenating the flags into one TOML document.
    parsed = tomllib.loads("\n".join(overrides[:3]))
    assert parsed["plugins"] == {"some.plugin@publisher": {"enabled": False}}
    assert parsed["mcp_servers"]["another"]["enabled"] is False
    assert parsed["features"]["apps"] is False


@pytest.mark.parametrize("other", [
    row("another", "connected"), row("another", "starting"),
    row("another", "disabled", {"secret": {"name": "unrelated"}}),
    row("another"), row("unexpected-plugin"),
])
def test_scoped_inventory_requires_disabled_and_no_tools(other):
    with pytest.raises(probe.ProbeError, match="Unrelated MCP runtime"):
        probe.inventory(rpc_for([row("fixture", "connected"), other]), "fixture", "ephemeral")


def test_unscoped_null_status_requires_configured_disable():
    probe.inventory(rpc_for([row("fixture"), row("another")]), "fixture")
    with pytest.raises(probe.ProbeError):
        probe.inventory(rpc_for([row("fixture"), row("unexpected-plugin")]), "fixture")


def test_inventory_checks_all_pages():
    def rpc(method, params):
        if method == "config/read":
            return {"config": {"mcp_servers": {}}}
        if "cursor" not in params:
            return {"data": [row("fixture", "connected")], "nextCursor": "page-2"}
        return {"data": [row("plugin", "connected")], "nextCursor": None}
    with pytest.raises(probe.ProbeError):
        probe.inventory(rpc, "fixture", "ephemeral")


@pytest.mark.parametrize("extra", [
    {"plugins": {"plugin@publisher": {"enabled": True}}},
    {"features": {"apps": True}},
    {"mcp_servers": {"fixture": {"enabled": True}, "another": {"enabled": True}}},
])
def test_effective_config_refuses_inherited_integrations(extra):
    config = {"mcp_servers": {"fixture": {"enabled": True}}, "plugins": {},
              "features": {"apps": False}, **extra}
    with pytest.raises(probe.ProbeError):
        probe.verify_isolation(rpc_for([], config), "fixture")
