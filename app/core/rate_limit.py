"""
Minimal in-process rate limiting (Sec. 10 hardening item, TODO.md). Deliberately simple: a
fixed-window counter per bucket key held in memory. This is NOT sufficient for a
multi-process/multi-instance production deployment (each process has its own counters) - a
real deployment needs a shared store (e.g. rate limiting backed by Postgres or Redis) once
horizontal scaling is in play. Documented as a known limitation rather than a hidden gap; see
docs/SECURITY.md.

Two keying strategies, chosen per endpoint:
- By client IP: for UNAUTHENTICATED endpoints (login, register, password-reset-request) where
  there's no actor yet to key on.
- By actor (user_id): for AUTHENTICATED endpoints (document upload, ask-AI) - keying by IP
  alone would let multiple legitimate users behind the same NAT/office IP starve each other,
  and wouldn't stop a single malicious authenticated user from rotating IPs. Keying by the
  authenticated user_id is the more correct choice once an actor exists.
"""
import time
from collections import defaultdict
from functools import lru_cache

from fastapi import Depends, HTTPException, Request, status

from app.api.deps import Actor, get_current_actor
from app.core.config import get_settings


class _FixedWindowLimiter:
    def __init__(self, *, max_requests: int, window_seconds: int) -> None:
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._buckets: dict[str, list[float]] = defaultdict(list)

    def check(self, key: str) -> None:
        now = time.monotonic()
        window_start = now - self.window_seconds
        timestamps = self._buckets[key]
        while timestamps and timestamps[0] < window_start:
            timestamps.pop(0)
        if len(timestamps) >= self.max_requests:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many requests, please try again later.",
            )
        timestamps.append(now)


def _client_key(request: Request) -> str:
    return request.client.host if request.client else "unknown"


# --- Unauthenticated endpoints: keyed by client IP ---
# Limits come from settings (defaults are the abuse-resistant values) and the limiters are
# built lazily so tests can override the settings before the first request rather than being
# locked to whatever was configured at import time.
@lru_cache
def _limiter(setting_name: str, *, window_seconds: int) -> _FixedWindowLimiter:
    return _FixedWindowLimiter(
        max_requests=getattr(get_settings(), setting_name), window_seconds=window_seconds
    )


def _register_limiter() -> _FixedWindowLimiter:
    return _limiter("rate_limit_register_per_minute", window_seconds=60)


def _login_limiter() -> _FixedWindowLimiter:
    return _limiter("rate_limit_login_per_minute", window_seconds=60)


def _password_reset_limiter() -> _FixedWindowLimiter:
    return _limiter("rate_limit_password_reset_per_hour", window_seconds=3600)


def _upload_limiter() -> _FixedWindowLimiter:
    return _limiter("rate_limit_upload_per_minute", window_seconds=60)


def _ask_question_limiter() -> _FixedWindowLimiter:
    return _limiter("rate_limit_ask_question_per_minute", window_seconds=60)


def _ticket_write_limiter() -> _FixedWindowLimiter:
    return _limiter("rate_limit_ticket_write_per_minute", window_seconds=60)


async def rate_limit_login(request: Request) -> None:
    _login_limiter().check(_client_key(request))


async def rate_limit_register(request: Request) -> None:
    _register_limiter().check(_client_key(request))


async def rate_limit_password_reset(request: Request) -> None:
    # Deliberately IP-keyed even though the request carries an email - keying by the
    # submitted email would let an attacker trivially bypass the limiter by rotating emails,
    # and would do nothing to stop enumeration-by-volume against one IP.
    _password_reset_limiter().check(_client_key(request))


# --- Authenticated endpoints: keyed by actor (user_id) ---
async def rate_limit_document_upload(actor: Actor = Depends(get_current_actor)) -> None:
    _upload_limiter().check(str(actor.user_id))


async def rate_limit_ask_question(actor: Actor = Depends(get_current_actor)) -> None:
    _ask_question_limiter().check(str(actor.user_id))


async def rate_limit_ticket_write(actor: Actor = Depends(get_current_actor)) -> None:
    _ticket_write_limiter().check(str(actor.user_id))
