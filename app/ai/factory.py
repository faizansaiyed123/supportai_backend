"""Selects the configured provider implementation from settings."""
from app.ai.base import EmbeddingProvider, GenerationProvider, RerankProvider
from app.ai.embeddings import LocalSentenceTransformersProvider
from app.ai.generation import HostedOpenAICompatibleProvider, OllamaGenerationProvider
from app.ai.reranking import LocalCrossEncoderRerankProvider, NoOpRerankProvider
from app.core.config import get_settings


def get_generation_provider() -> GenerationProvider:
    settings = get_settings()
    if settings.ai_generation_provider == "ollama":
        return OllamaGenerationProvider()
    if settings.ai_generation_provider == "hosted_openai_compatible":
        return HostedOpenAICompatibleProvider()
    raise ValueError(f"Unknown AI_GENERATION_PROVIDER: {settings.ai_generation_provider}")


def get_embedding_provider() -> EmbeddingProvider:
    settings = get_settings()
    if settings.ai_embedding_provider == "local_sentence_transformers":
        return LocalSentenceTransformersProvider()
    raise ValueError(f"Unknown AI_EMBEDDING_PROVIDER: {settings.ai_embedding_provider}")


def get_rerank_provider() -> RerankProvider:
    settings = get_settings()
    if not settings.rerank_enabled:
        return NoOpRerankProvider()
    if settings.ai_rerank_provider == "cross_encoder_local":
        return LocalCrossEncoderRerankProvider()
    raise ValueError(f"Unknown AI_RERANK_PROVIDER: {settings.ai_rerank_provider}")
