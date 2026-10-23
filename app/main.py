from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import auth, conversations, customers, health, knowledge, memberships, tickets
from app.core.config import get_settings
from app.core.logging import configure_logging, get_logger
from app.core.security_headers import SecurityHeadersMiddleware
from app.workers.app import procrastinate_app

configure_logging()
logger = get_logger(__name__)

settings = get_settings()

# Must stay in sync with backend/.env.example. Refuses to start in production if still set.
_PLACEHOLDER_JWT_SECRET = "change_me_to_a_long_random_value_in_real_envs"


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    logger.info("app_startup", environment=settings.environment)

    if settings.is_production and settings.jwt_secret == _PLACEHOLDER_JWT_SECRET:
        raise RuntimeError(
            "ENVIRONMENT=production but JWT_SECRET is still the placeholder from .env.example. "
            "Set a real, long random secret before running in production."
        )

    await procrastinate_app.open_async()
    yield
    await procrastinate_app.close_async()


app = FastAPI(
    title="SupportAI API",
    version="0.1.0",
    lifespan=lifespan,
    # /docs and the OpenAPI schema leak the API surface and endpoint signatures; disable them
    # in production. They remain available in dev for local testing.
    docs_url=None if settings.is_production else "/docs",
    redoc_url=None if settings.is_production else "/redoc",
    openapi_url=None if settings.is_production else "/api/openapi.json",
)

app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router, prefix="/api", tags=["health"])
app.include_router(auth.router, prefix="/api", tags=["auth"])
app.include_router(knowledge.router, prefix="/api", tags=["knowledge"])
app.include_router(customers.router, prefix="/api", tags=["customers"])
app.include_router(memberships.router, prefix="/api", tags=["memberships"])
app.include_router(conversations.router, prefix="/api", tags=["conversations"])
app.include_router(tickets.router, prefix="/api", tags=["tickets"])
