"""Provider factory: build any configured LLM backend behind one interface."""

from __future__ import annotations

from fastagent.providers.base import CompletionRequest, ModelProvider, ProviderResponse
from fastagent.providers.anthropic_provider import AnthropicProvider

__all__ = [
    "AnthropicProvider",
    "CompletionRequest",
    "ModelProvider",
    "ProviderResponse",
    "build_provider",
]


def build_provider(provider_config) -> ModelProvider:
    """Instantiate a provider from a fastagent.config.ProviderConfig."""
    if provider_config.type == "anthropic":
        return AnthropicProvider(timeout=provider_config.timeout_seconds)
    if provider_config.type == "openai_compatible":
        from fastagent.providers.openai_compat import OpenAICompatProvider

        if not provider_config.base_url:
            raise ValueError("openai_compatible providers require a base_url.")
        return OpenAICompatProvider(
            base_url=provider_config.base_url,
            api_key_env=provider_config.api_key_env,
            verify_tls=provider_config.verify_tls,
            timeout_seconds=provider_config.timeout_seconds,
            stream=provider_config.stream,
            extra_headers=provider_config.extra_headers,
            extra_body=provider_config.extra_body,
        )
    raise ValueError(f"Unknown provider type {provider_config.type!r}.")
