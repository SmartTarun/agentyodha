"""Anthropic provider — the first-class backend, using the official SDK.

Uses current API best practices: streaming (avoids HTTP timeouts at large
max_tokens), adaptive thinking with effort control, and prompt caching on the
system prompt.
"""

from __future__ import annotations

from typing import Any, Optional, Type, TypeVar

import anthropic
from pydantic import BaseModel

from fastagent.providers.base import CompletionRequest, ModelProvider, ProviderResponse

T = TypeVar("T", bound=BaseModel)


class AnthropicProvider(ModelProvider):
    name = "anthropic"

    def __init__(self, client: Optional[anthropic.Anthropic] = None, timeout: float = 600.0):
        # The SDK resolves credentials itself (ANTHROPIC_API_KEY, auth token, or
        # an `ant auth login` profile) and always verifies TLS against the
        # official endpoint — nothing credential-shaped touches our config.
        self.client = client or anthropic.Anthropic(timeout=timeout)

    def _params(self, request: CompletionRequest) -> dict[str, Any]:
        params: dict[str, Any] = {
            "model": request.model,
            "max_tokens": request.max_tokens,
            "output_config": {"effort": request.effort},
        }
        if request.thinking == "adaptive":
            params["thinking"] = {"type": "adaptive", "display": request.thinking_display}
        else:
            params["thinking"] = {"type": "disabled"}

        if request.system:
            if request.cache_system:
                params["system"] = [{
                    "type": "text",
                    "text": request.system,
                    "cache_control": {"type": "ephemeral"},
                }]
            else:
                params["system"] = request.system

        if request.tools:
            tools = [
                {"name": t["name"], "description": t["description"], "input_schema": t["input_schema"]}
                for t in request.tools
            ]
            tools.sort(key=lambda t: t["name"])  # deterministic order = cache-friendly
            params["tools"] = tools
        return params

    def complete(self, request: CompletionRequest) -> ProviderResponse:
        with self.client.messages.stream(
            **self._params(request), messages=request.messages
        ) as stream:
            if request.on_text:
                for delta in stream.text_stream:
                    request.on_text(delta)
            response = stream.get_final_message()

        usage = {
            k: v for k, v in response.usage.model_dump().items() if isinstance(v, int)
        }
        content = [block.model_dump(exclude_none=True) for block in response.content]
        return ProviderResponse(
            content=content,
            stop_reason=response.stop_reason or "end_turn",
            usage=usage,
        )

    def extract(
        self,
        *,
        model: str,
        prompt: str,
        schema: Type[T],
        system: Optional[str] = None,
        max_tokens: int = 4096,
    ) -> T:
        params: dict[str, Any] = {"model": model, "max_tokens": max_tokens}
        if system:
            params["system"] = system
        response = self.client.messages.parse(
            **params,
            messages=[{"role": "user", "content": prompt}],
            output_format=schema,
        )
        return response.parsed_output
