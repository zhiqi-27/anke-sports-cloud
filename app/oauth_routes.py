"""Official SDK OAuth handlers with explicit resource binding and issuer responses."""

import re
from urllib.parse import parse_qs, urlsplit

from mcp.server.auth.handlers.authorize import AuthorizationHandler
from mcp.server.auth.handlers.token import TokenHandler, TokenErrorResponse
from mcp.server.auth.middleware.client_auth import AuthenticationError, ClientAuthenticator
from mcp.server.auth.provider import construct_redirect_uri
from mcp.server.auth.routes import build_metadata, create_auth_routes, cors_middleware
from mcp.server.auth.settings import ClientRegistrationOptions, RevocationOptions
from mcp.server.transport_security import RequestBodyLimitMiddleware
from pydantic import AnyHttpUrl
from starlette.responses import JSONResponse, Response
from starlette.routing import Route, request_response

from app.oauth import SCOPES, TOKEN_RESOURCE, SportsOAuthProvider, issuer, resource

provider = SportsOAuthProvider()


class BoundAuthorizationHandler(AuthorizationHandler):
    async def handle(self, request):
        response = await super().handle(request)
        location = response.headers.get("location")
        if location and "error" in parse_qs(urlsplit(location).query):
            response.headers["location"] = construct_redirect_uri(location, iss=issuer())
        return response


class BoundTokenHandler(TokenHandler):
    async def handle(self, request):
        form = await request.form()
        audience = form.get("resource")
        if len(form.getlist("resource")) != 1 or audience not in {resource(), resource("extension")}:
            return self.response(
                TokenErrorResponse(error="invalid_request", error_description="An exact resource is required")
            )
        if form.get("grant_type") == "authorization_code" and not re.fullmatch(
            r"[A-Za-z0-9._~-]{43,128}", str(form.get("code_verifier", ""))
        ):
            return self.response(
                TokenErrorResponse(error="invalid_request", error_description="Invalid PKCE verifier")
            )
        context = TOKEN_RESOURCE.set(str(audience))
        try:
            return await super().handle(request)
        finally:
            TOKEN_RESOURCE.reset(context)


async def revoke(request):
    # SDK 1.30.0's RevocationRequest requires client_secret even for public
    # clients. Keep its authenticator and provider, but accept RFC 7009 forms.
    headers = {"Cache-Control": "no-store", "Pragma": "no-cache"}
    try:
        client = await ClientAuthenticator(provider).authenticate_request(request)
    except AuthenticationError:
        return JSONResponse({"error": "unauthorized_client"}, status_code=401, headers=headers)
    form = await request.form()
    raw = form.get("token")
    if len(form.getlist("token")) != 1 or not isinstance(raw, str) or not raw or len(raw) > 200:
        return JSONResponse({"error": "invalid_request"}, status_code=400, headers=headers)
    token = await provider.load_access_token(raw) or await provider.load_refresh_token(client, raw)
    if token and token.client_id == client.client_id:
        await provider.revoke_token(token)
    # Unknown tokens and tokens belonging to other clients have the same result.
    return Response(status_code=200, headers=headers)


def auth_routes():
    registration = ClientRegistrationOptions(
        enabled=True, valid_scopes=SCOPES, default_scopes=["calendar:read"]
    )
    revocation = RevocationOptions(enabled=True)
    routes = create_auth_routes(
        provider,
        AnyHttpUrl(issuer()),
        client_registration_options=registration,
        revocation_options=revocation,
    )
    metadata = build_metadata(AnyHttpUrl(issuer()), None, registration, revocation).model_dump(
        mode="json", exclude_none=True
    )
    metadata.update(
        token_endpoint_auth_methods_supported=["none", "client_secret_post", "client_secret_basic"],
        revocation_endpoint_auth_methods_supported=["none", "client_secret_post", "client_secret_basic"],
        authorization_response_iss_parameter_supported=True,
    )

    async def serve_metadata(request):
        return JSONResponse(metadata, headers={"Cache-Control": "public, max-age=300"})

    replacements = {
        "/.well-known/oauth-authorization-server": cors_middleware(serve_metadata, ["GET", "OPTIONS"]),
        "/authorize": RequestBodyLimitMiddleware(
            request_response(BoundAuthorizationHandler(provider).handle), 16384
        ),
        "/token": RequestBodyLimitMiddleware(
            cors_middleware(
                BoundTokenHandler(provider, ClientAuthenticator(provider)).handle, ["POST", "OPTIONS"]
            ),
            16384,
        ),
        "/revoke": RequestBodyLimitMiddleware(cors_middleware(revoke, ["POST", "OPTIONS"]), 16384),
    }
    return [
        Route(route.path, endpoint=replacements.get(route.path, route.app), methods=route.methods)
        for route in routes
    ]
