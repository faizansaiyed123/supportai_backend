"""Generation provider implementations. See app/ai/base.py for the contract."""
import httpx

from app.ai.base import GenerationError, GenerationProvider
from app.core.config import get_settings


class OllamaGenerationProvider(GenerationProvider):
    """Local, no paid API key required (D007)."""

    async def generate(self, *, system_prompt: str, user_prompt: str, max_tokens: int = 1024) -> str:
        settings = get_settings()
        payload = {
            "model": settings.ollama_model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "stream": False,
            "options": {"num_predict": max_tokens},
            # Ollama's default keep_alive is 5 minutes, after which it unloads the weights. A
            # multi-GB model then has to be re-read from disk on the next request, which measured
            # ~295s here versus ~12s warm. Holding it longer keeps interactive latency sane under
            # bursty/traffic-light usage instead of paying a cold load between questions.
            "keep_alive": settings.ollama_keep_alive,
        }
        try:
            async with httpx.AsyncClient(timeout=settings.ai_generation_timeout_seconds) as client:
                resp = await client.post(f"{settings.ollama_base_url}/api/chat", json=payload)
                resp.raise_for_status()
                data = resp.json()
                return data["message"]["content"]
        except (httpx.HTTPError, KeyError) as exc:
            # httpx timeout exceptions stringify to an empty string, which would otherwise produce
            # a bare "Ollama generation failed: " with no diagnostic. Name the exception type so a
            # timeout is distinguishable from a connection error in the logs.
            raise GenerationError(
                f"Ollama generation failed ({type(exc).__name__}): {exc or 'no detail'}"
            ) from exc


class HostedOpenAICompatibleProvider(GenerationProvider):
    """Optional hosted-provider adapter. Disabled unless explicitly configured (D007)."""

    async def generate(self, *, system_prompt: str, user_prompt: str, max_tokens: int = 1024) -> str:
        settings = get_settings()
        if not settings.hosted_ai_api_key or not settings.hosted_ai_base_url:
            raise GenerationError(
                "Hosted AI provider selected but HOSTED_AI_API_KEY/HOSTED_AI_BASE_URL not set"
            )
        payload = {
            "model": settings.ollama_model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "max_tokens": max_tokens,
        }
        headers = {"Authorization": f"Bearer {settings.hosted_ai_api_key}"}
        try:
            async with httpx.AsyncClient(timeout=settings.ai_generation_timeout_seconds) as client:
                resp = await client.post(
                    f"{settings.hosted_ai_base_url}/chat/completions", json=payload, headers=headers
                )
                resp.raise_for_status()
                data = resp.json()
                return data["choices"][0]["message"]["content"]
        except (httpx.HTTPError, KeyError, IndexError) as exc:
            raise GenerationError(f"Hosted generation failed: {exc}") from exc
