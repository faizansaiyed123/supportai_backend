"""
Central application configuration, loaded from environment variables (.env in dev).
Everything security- or environment-sensitive lives here, not scattered through the codebase.
"""
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    environment: str = "development"
    log_level: str = "INFO"

    database_url: str
    database_url_sync: str
    db_pool_size: int = 10
    db_max_overflow: int = 10
    db_pool_timeout: float = 30.0

    jwt_secret: str
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 15
    refresh_token_expire_days: int = 30

    allowed_origins: str = "http://localhost:3000"

    storage_backend: str = "local"
    local_storage_path: str = "/app/data/uploads"
    max_upload_size_mb: int = 25

    ai_generation_provider: str = "ollama"
    ai_embedding_provider: str = "local_sentence_transformers"
    ollama_base_url: str = "http://ollama:11434"
    ollama_model: str = "llama3.1:8b"
    # Local CPU inference is slow and highly variable: a warm llama3.1:8b answers in ~12s, but the
    # first call after an Ollama restart has to load ~4.9GB of weights and measured ~295s. This is a
    # ceiling on the request budget, not an expected latency - it only bites on a cold start. Hosted
    # providers use the same budget and never come close.
    ai_generation_timeout_seconds: float = 600.0
    # How long Ollama keeps the model resident after a request. Its own default (5m) is short
    # enough that a bursty workload pays a full ~5GB reload between questions.
    ollama_keep_alive: str = "30m"
    embedding_model_name: str = "BAAI/bge-small-en-v1.5"
    embedding_dimensions: int = 384

    rerank_enabled: bool = True
    ai_rerank_provider: str = "cross_encoder_local"
    rerank_model_name: str = "cross-encoder/ms-marco-MiniLM-L-6-v2"
    rerank_candidate_pool_size: int = 20
    rerank_top_n: int = 5

    hosted_ai_api_key: str | None = None
    hosted_ai_base_url: str | None = None

    # Rate limits (see app/core/rate_limit.py). The defaults are the production-safe values;
    # they're settings rather than hardcoded constants only so a test/CI environment can raise
    # them - the E2E suite creates a fresh org per test from a single client IP, which trips an
    # abuse-oriented registration limit that exists for real attackers, not for a test runner.
    rate_limit_register_per_minute: int = 5
    rate_limit_login_per_minute: int = 10
    rate_limit_password_reset_per_hour: int = 5
    rate_limit_upload_per_minute: int = 20
    rate_limit_ask_question_per_minute: int = 30
    rate_limit_ticket_write_per_minute: int = 60

    # Procrastinate worker: parallel in-process jobs per worker replica. Scale by running
    # multiple `worker` container replicas AND raising this, not by one giant worker.
    worker_concurrency: int = 4

    @property
    def allowed_origins_list(self) -> list[str]:
        return [o.strip() for o in self.allowed_origins.split(",") if o.strip()]

    @property
    def is_production(self) -> bool:
        return self.environment == "production"


@lru_cache
def get_settings() -> Settings:
    """Cached settings accessor. Use this (not a module-level Settings() instance) everywhere,
    so tests can override via environment variables cleanly and settings are only parsed once."""
    # pydantic-settings populates database_url/jwt_secret from the environment, which mypy
    # cannot see - this is the single construction site, so the ignore is scoped to it.
    return Settings()  # type: ignore[call-arg]
