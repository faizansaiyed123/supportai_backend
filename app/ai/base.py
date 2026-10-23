"""
Provider-abstraction contract (D007). Business logic calls these functions - it never imports
a specific provider SDK directly. Swapping local <-> hosted is a config change.
"""
from abc import ABC, abstractmethod


class GenerationProvider(ABC):
    @abstractmethod
    async def generate(self, *, system_prompt: str, user_prompt: str, max_tokens: int = 1024) -> str:
        raise NotImplementedError


class EmbeddingProvider(ABC):
    @abstractmethod
    async def embed(self, texts: list[str]) -> list[list[float]]:
        raise NotImplementedError


class GenerationError(Exception):
    """Raised when the configured generation provider fails or is unavailable."""


class EmbeddingError(Exception):
    """Raised when the configured embedding provider fails or is unavailable."""


class RerankProvider(ABC):
    @abstractmethod
    def score(self, *, query: str, documents: list[str]) -> list[float]:
        """Returns one relevance score per document, same order as `documents`. Higher =
        more relevant. Synchronous by design - cross-encoder inference is CPU-bound, not I/O-
        bound, so there's nothing to gain from async here (unlike the HTTP-based generation
        provider); callers run it via a thread if needed to avoid blocking an event loop."""
        raise NotImplementedError


class RerankError(Exception):
    """Raised when the configured rerank provider fails or is unavailable."""
