"""
URL fetching for URL-type knowledge sources (Sec. 13/18, closes a TODO.md gap). Real SSRF risk
here: fetching a server-controlled request to an arbitrary user-supplied URL is a classic vector
for reaching internal services (cloud metadata endpoints, internal admin panels, etc.) that
would otherwise be unreachable from outside the network. This module is the SSRF-mitigation
layer - every URL-type ingestion path MUST go through validate_url_is_safe() before any request
is made, and MUST re-validate on every redirect hop (redirects are not followed automatically
for this reason).

HONESTLY STATED RESIDUAL RISK (not hidden): this validates that a hostname's resolved IP is not
private/loopback/link-local/reserved AT VALIDATION TIME, then connects separately. Between
those two steps, DNS could theoretically be "rebound" to point the same hostname at a different
(private) IP - a DNS-rebinding attack. Fully closing that gap requires resolving once and
connecting to that exact pinned IP while still sending the correct Host header/SNI, which needs
a custom transport - not implemented here. This is flagged explicitly in docs/SECURITY.md as a
known limitation, not silently accepted. For an internal tool with trusted admin users (who are
the only ones permitted to add knowledge sources, via knowledge:manage), this is a reasonable
first-line defense, not a claim of complete SSRF immunity.
"""
import ipaddress
import socket
from urllib.parse import urlparse

import httpx

from app.core.config import get_settings

_ALLOWED_SCHEMES = {"http", "https"}
_MAX_REDIRECTS = 3


class URLFetchError(Exception):
    pass


class UnsafeURLError(URLFetchError):
    """Raised when a URL is rejected on safety grounds (disallowed scheme, resolves to a
    private/internal address, etc.) - deliberately a distinct exception from generic fetch
    failures so callers/logs can tell "we refused to even try" apart from "we tried and it
    failed"."""


def _is_safe_ip(ip_str: str) -> bool:
    ip = ipaddress.ip_address(ip_str)
    return not (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


def validate_url_is_safe(url: str) -> None:
    """Raises UnsafeURLError if the URL's scheme is disallowed, it has no hostname, the
    hostname doesn't resolve, or ANY of its resolved addresses is private/internal. Pure
    stdlib (ipaddress + socket) - no network mocking needed to unit test the classification
    logic itself, only the DNS resolution step needs a real/fake resolver."""
    parsed = urlparse(url)
    if parsed.scheme not in _ALLOWED_SCHEMES:
        raise UnsafeURLError(f"Unsupported URL scheme: {parsed.scheme!r} (only http/https allowed)")
    if not parsed.hostname:
        raise UnsafeURLError("URL has no hostname")

    try:
        addrinfo = socket.getaddrinfo(parsed.hostname, None)
    except socket.gaierror as exc:
        raise UnsafeURLError(f"Could not resolve hostname {parsed.hostname!r}: {exc}") from exc

    if not addrinfo:
        raise UnsafeURLError(f"Hostname {parsed.hostname!r} resolved to no addresses")

    for _family, _type, _proto, _canonname, sockaddr in addrinfo:
        ip_str = sockaddr[0]
        if not _is_safe_ip(ip_str):
            raise UnsafeURLError(
                f"URL {url!r} resolves to a disallowed address ({ip_str}) - "
                "private/internal addresses are not fetchable"
            )


async def fetch_url(url: str) -> tuple[bytes, str]:
    """Validates and fetches `url`, manually following redirects (up to _MAX_REDIRECTS) with
    re-validation at each hop - httpx's automatic redirect-following is deliberately NOT used,
    since it would fetch a redirect target before this module gets a chance to reject it.
    Returns (content_bytes, content_type). Enforces the same MAX_UPLOAD_SIZE_MB limit as file
    uploads, applied to the response body as it streams in (not just checked after the fact)
    so a malicious server can't exhaust memory by claiming a small Content-Length then
    streaming far more.
    """
    settings = get_settings()
    max_bytes = settings.max_upload_size_mb * 1024 * 1024

    current_url = url
    for _ in range(_MAX_REDIRECTS + 1):
        validate_url_is_safe(current_url)

        try:
            async with httpx.AsyncClient(follow_redirects=False, timeout=30.0) as client:
                async with client.stream("GET", current_url) as response:
                    if response.status_code in (301, 302, 303, 307, 308):
                        location = response.headers.get("location")
                        if not location:
                            raise URLFetchError(f"Redirect response with no Location header from {current_url}")
                        current_url = str(httpx.URL(current_url).join(location))
                        continue

                    if response.status_code >= 400:
                        raise URLFetchError(f"HTTP {response.status_code} fetching {current_url}")

                    content_type = response.headers.get("content-type", "text/html").split(";")[0].strip()
                    chunks = []
                    total = 0
                    async for chunk in response.aiter_bytes():
                        total += len(chunk)
                        if total > max_bytes:
                            raise URLFetchError(
                                f"Response from {current_url} exceeds {settings.max_upload_size_mb}MB limit"
                            )
                        chunks.append(chunk)
                    return b"".join(chunks), content_type
        except httpx.HTTPError as exc:
            raise URLFetchError(f"Failed to fetch {current_url}: {exc}") from exc

    raise URLFetchError(f"Too many redirects fetching {url} (max {_MAX_REDIRECTS})")
