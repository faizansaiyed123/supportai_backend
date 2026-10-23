"""
Security response headers (Sec. 10 hardening item, TODO.md). A small hand-written ASGI
middleware rather than a dependency, since the set of headers needed is short and stable.
"""
from starlette.types import ASGIApp, Receive, Scope, Send


class SecurityHeadersMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message):
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                # Strip the server banner (uvicorn/etc.) so version/tip data isn't leaked, and
                # drop any existing CSP so our conservative value below is authoritative.
                headers = [
                    (k, v) for k, v in headers
                    if k.lower() not in (b"server", b"content-security-policy")
                ]
                headers.extend(
                    [
                        (b"x-content-type-options", b"nosniff"),
                        (b"x-frame-options", b"DENY"),
                        (b"referrer-policy", b"no-referrer"),
                        # HSTS only makes sense once actually served over HTTPS - safe to
                        # send in dev too (browsers ignore it over plain HTTP), and a real
                        # deployment behind TLS gets it "for free" via this middleware.
                        (b"strict-transport-security", b"max-age=63072000; includeSubDomains"),
                        # A conservative default CSP for a JSON API (no inline scripts, no
                        # framing) - the future frontend, once built, will likely be served
                        # from a different origin/app and define its own CSP; this one is
                        # scoped to what this API itself returns (mostly JSON, plus /docs).
                        (b"content-security-policy", b"default-src 'none'; frame-ancestors 'none'"),
                    ]
                )
                message["headers"] = headers
            await send(message)

        await self.app(scope, receive, send_with_headers)
