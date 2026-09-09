"""Browser consent -> PKCE -> official MCP client, strictly on local loopback.

Run after the local API and Web. Open the printed URL. Credentials stay in
process memory; callback immediately redirects to a URL without query secrets.
Stop with Ctrl-C. Revoke the test connection in Web settings when finished.
"""

import asyncio
import base64
import hashlib
import html
import secrets
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from app.config import settings

cfg = settings()
assert cfg.env == "local" and urlsplit(cfg.public_url).hostname in {"localhost", "127.0.0.1"}
API = cfg.public_url.rstrip("/")
REDIRECT = "http://127.0.0.1:18788/callback"
flow = {}


async def check_mcp(token):
    async with httpx.AsyncClient(headers={"Authorization": "Bearer " + token}) as client:
        async with streamable_http_client(API + "/mcp", http_client=client) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool("get_my_calendar")
                return not result.isError and bool(result.structuredContent)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def redirect(self, target):
        self.send_response(303)
        self.send_header("Location", target)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

    def page(self, message):
        body = (
            "<!doctype html><meta charset=utf-8><meta name=referrer content=no-referrer>"
            "<title>Anke Sports · 本地连接验收</title>"
            "<style>body{background:#0d201a;color:#edf2ea;font:18px system-ui;max-width:680px;margin:12vh auto;line-height:1.8}a{color:#c7eba0}</style>"
            "<h1>Anke Sports · 本地连接验收</h1><p>" + html.escape(message) + "</p>"
            "<p><a href='/verify-revocation'>检查撤销结果</a></p>"
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urlsplit(self.path).path
        try:
            if path == "/start":
                flow.clear()
                with httpx.Client() as client:
                    metadata = client.get(API + "/.well-known/oauth-authorization-server").json()
                    assert metadata["issuer"].rstrip("/") == API
                    info = client.post(
                        API + "/register",
                        json={
                            "client_name": "本地连接验收",
                            "redirect_uris": [REDIRECT],
                            "grant_types": ["authorization_code", "refresh_token"],
                            "response_types": ["code"],
                            "token_endpoint_auth_method": "none",
                            "scope": "calendar:read calendar:write feed:read",
                        },
                    )
                    info.raise_for_status()
                    flow.update(
                        client_id=info.json()["client_id"],
                        issuer=metadata["issuer"],
                        verifier=secrets.token_urlsafe(48),
                        state=secrets.token_urlsafe(32),
                    )
                challenge = (
                    base64.urlsafe_b64encode(hashlib.sha256(flow["verifier"].encode()).digest())
                    .decode()
                    .rstrip("=")
                )
                self.redirect(
                    API
                    + "/authorize?"
                    + urlencode(
                        {
                            "client_id": flow["client_id"],
                            "redirect_uri": REDIRECT,
                            "response_type": "code",
                            "scope": "calendar:read calendar:write feed:read",
                            "resource": API + "/mcp",
                            "state": flow["state"],
                            "code_challenge": challenge,
                            "code_challenge_method": "S256",
                        }
                    )
                )
            elif path == "/callback":
                params = parse_qs(urlsplit(self.path).query)
                assert params.get("state") == [flow["state"]] and params.get("iss") == [flow["issuer"]]
                if "code" not in params:
                    flow["result"] = "已取消连接，未兑换令牌。"
                else:
                    response = httpx.post(
                        API + "/token",
                        data={
                            "client_id": flow["client_id"],
                            "grant_type": "authorization_code",
                            "code": params["code"][0],
                            "code_verifier": flow["verifier"],
                            "redirect_uri": REDIRECT,
                            "resource": API + "/mcp",
                        },
                    )
                    response.raise_for_status()
                    tokens = response.json()
                    flow["access"] = tokens["access_token"]
                    assert "feed:read" not in tokens["scope"].split(), (
                        "Feed scope should remain unselected for this check"
                    )
                    assert asyncio.run(check_mcp(flow["access"]))
                    flow["result"] = (
                        "网页授权、PKCE 兑换、官方 SDK 的真实 HTTP 查询均通过。私人订阅权限未授予。请在设置中撤销「本地连接验收」。"
                    )
                self.redirect("/result")
            elif path == "/verify-revocation":
                response = httpx.post(
                    API + "/mcp", headers={"Authorization": "Bearer " + flow.get("access", "")}, json={}
                )
                self.page(
                    "撤销已生效：原访问令牌返回 401。" if response.status_code == 401 else "连接尚未撤销。"
                )
            else:
                self.page(flow.get("result", "请打开 /start 发起本地验收。"))
        except Exception:
            flow["result"] = "本地验收未通过，请检查服务状态后重新发起。没有输出凭据或回调参数。"
            if path == "/callback":
                self.redirect("/result")
            else:
                self.page(flow["result"])


if __name__ == "__main__":
    print("Local consent check: http://127.0.0.1:18788/start", flush=True)
    HTTPServer(("127.0.0.1", 18788), Handler).serve_forever()
