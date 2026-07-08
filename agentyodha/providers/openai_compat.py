"""OpenAI-compatible provider: one backend for "any LLM".

Nearly every model server speaks the OpenAI Chat Completions protocol —
OpenAI itself, Ollama, vLLM, LM Studio, llama.cpp, Groq, Together, Mistral,
DeepSeek, and most gateways. Point `base_url` at any of them:

    providers:
      local:
        type: openai_compatible
        base_url: http://127.0.0.1:11434/v1     # Ollama
      openai:
        type: openai_compatible
        base_url: https://api.openai.com/v1
        api_key_env: OPENAI_API_KEY             # env var NAME, never the key

Endpoint security is enforced at construction: HTTPS required for remote
hosts, TLS verification cannot be disabled off-loopback, credentials come
only from the environment, and every request has a timeout + size cap.
"""

from __future__ import annotations

import json
from typing import Any, Optional, Type, TypeVar

import httpx
from pydantic import BaseModel

from agentyodha.providers.base import CompletionRequest, ModelProvider, ProviderResponse
from agentyodha.security import redact, resolve_api_key, validate_base_url, validate_tls

T = TypeVar("T", bound=BaseModel)

MAX_RESPONSE_BYTES = 20 * 1024 * 1024  # 20 MB cap on any endpoint response

_STOP_REASON_MAP = {
    "stop": "end_turn",
    "tool_calls": "tool_use",
    "function_call": "tool_use",
    "length": "max_tokens",
    "content_filter": "refusal",
}


def to_openai_messages(
    system: Optional[str], messages: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Translate the framework's Anthropic-style history to OpenAI chat format."""
    out: list[dict[str, Any]] = []
    if system:
        out.append({"role": "system", "content": system})

    for message in messages:
        role, content = message["role"], message["content"]
        if isinstance(content, str):
            out.append({"role": role, "content": content})
            continue

        if role == "user":
            texts: list[str] = []
            for block in content:
                if block.get("type") == "tool_result":
                    body = block.get("content", "")
                    if not isinstance(body, str):
                        body = json.dumps(body)
                    if block.get("is_error"):
                        body = f"[tool error] {body}"
                    out.append({
                        "role": "tool",
                        "tool_call_id": block["tool_use_id"],
                        "content": body,
                    })
                elif block.get("type") == "text":
                    texts.append(block.get("text", ""))
            if texts:
                out.append({"role": "user", "content": "\n".join(texts)})
        elif role == "assistant":
            text_parts: list[str] = []
            tool_calls: list[dict[str, Any]] = []
            for block in content:
                block_type = block.get("type")
                if block_type == "text":
                    text_parts.append(block.get("text", ""))
                elif block_type == "tool_use":
                    tool_calls.append({
                        "id": block["id"],
                        "type": "function",
                        "function": {
                            "name": block["name"],
                            "arguments": json.dumps(block.get("input") or {}),
                        },
                    })
                # thinking blocks are provider-internal; skip them
            entry: dict[str, Any] = {"role": "assistant", "content": "\n".join(text_parts) or None}
            if tool_calls:
                entry["tool_calls"] = tool_calls
            out.append(entry)
    return out


def to_openai_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t["description"],
                "parameters": t["input_schema"],
            },
        }
        for t in sorted(tools, key=lambda t: t["name"])
    ]


def from_openai_message(message: dict[str, Any], finish_reason: str) -> ProviderResponse:
    """Normalize an OpenAI chat message back into Anthropic-style blocks."""
    content: list[dict[str, Any]] = []
    if message.get("content"):
        content.append({"type": "text", "text": message["content"]})
    for call in message.get("tool_calls") or []:
        try:
            arguments = json.loads(call["function"].get("arguments") or "{}")
        except json.JSONDecodeError:
            arguments = {"_raw": call["function"].get("arguments")}
        content.append({
            "type": "tool_use",
            "id": call.get("id") or f"call_{len(content)}",
            "name": call["function"]["name"],
            "input": arguments,
        })
    return ProviderResponse(
        content=content,
        stop_reason=_STOP_REASON_MAP.get(finish_reason, "end_turn"),
    )


class OpenAICompatProvider(ModelProvider):
    name = "openai_compatible"

    def __init__(
        self,
        base_url: str,
        api_key_env: Optional[str] = None,
        verify_tls: bool = True,
        timeout_seconds: float = 120.0,
        stream: bool = True,
        extra_headers: Optional[dict[str, str]] = None,
        extra_body: Optional[dict[str, Any]] = None,
    ):
        base_url = validate_base_url(base_url.rstrip("/"))
        validate_tls(base_url, verify_tls)
        self.base_url = base_url
        self.stream = stream
        self.extra_body = extra_body or {}

        headers = {"Content-Type": "application/json", **(extra_headers or {})}
        api_key = resolve_api_key(api_key_env)
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        self._http = httpx.Client(
            base_url=base_url,
            headers=headers,
            verify=verify_tls,
            timeout=httpx.Timeout(timeout_seconds, connect=10.0),
        )

    # ------------------------------------------------------------------ #

    def _post(self, payload: dict[str, Any]) -> dict[str, Any]:
        response = self._http.post("/chat/completions", json=payload)
        self._raise_for_status(response)
        if len(response.content) > MAX_RESPONSE_BYTES:
            raise RuntimeError("Endpoint response exceeded the size cap.")
        return response.json()

    @staticmethod
    def _raise_for_status(response: httpx.Response) -> None:
        if response.status_code >= 400:
            body = redact(response.text[:500])
            raise RuntimeError(f"LLM endpoint returned HTTP {response.status_code}: {body}")

    def _payload(self, request: CompletionRequest) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": request.model,
            "max_tokens": request.max_tokens,
            "messages": to_openai_messages(request.system, request.messages),
            **self.extra_body,
        }
        if request.tools:
            payload["tools"] = to_openai_tools(request.tools)
        return payload

    def complete(self, request: CompletionRequest) -> ProviderResponse:
        payload = self._payload(request)
        if self.stream:
            return self._complete_streaming(payload, request)
        data = self._post(payload)
        choice = data["choices"][0]
        result = from_openai_message(choice["message"], choice.get("finish_reason") or "stop")
        result.usage = self._usage(data.get("usage"))
        if request.on_text:
            request.on_text(result.text())
        return result

    def _complete_streaming(
        self, payload: dict[str, Any], request: CompletionRequest
    ) -> ProviderResponse:
        payload = {**payload, "stream": True}
        text_parts: list[str] = []
        tool_calls: dict[int, dict[str, Any]] = {}
        finish_reason = "stop"
        usage: dict[str, int] = {}
        received = 0

        with self._http.stream("POST", "/chat/completions", json=payload) as response:
            self._raise_for_status(response)
            for line in response.iter_lines():
                received += len(line)
                if received > MAX_RESPONSE_BYTES:
                    raise RuntimeError("Streaming response exceeded the size cap.")
                if not line.startswith("data:"):
                    continue
                data_str = line[5:].strip()
                if data_str == "[DONE]":
                    break
                chunk = json.loads(data_str)
                if chunk.get("usage"):
                    usage = self._usage(chunk["usage"])
                choices = chunk.get("choices") or []
                if not choices:
                    continue
                choice = choices[0]
                if choice.get("finish_reason"):
                    finish_reason = choice["finish_reason"]
                delta = choice.get("delta") or {}
                if delta.get("content"):
                    text_parts.append(delta["content"])
                    if request.on_text:
                        request.on_text(delta["content"])
                for tc in delta.get("tool_calls") or []:
                    slot = tool_calls.setdefault(
                        tc.get("index", 0), {"id": "", "name": "", "arguments": ""}
                    )
                    if tc.get("id"):
                        slot["id"] = tc["id"]
                    fn = tc.get("function") or {}
                    if fn.get("name"):
                        slot["name"] += fn["name"]
                    if fn.get("arguments"):
                        slot["arguments"] += fn["arguments"]

        message: dict[str, Any] = {"content": "".join(text_parts)}
        if tool_calls:
            message["tool_calls"] = [
                {
                    "id": slot["id"] or f"call_{index}",
                    "type": "function",
                    "function": {"name": slot["name"], "arguments": slot["arguments"]},
                }
                for index, slot in sorted(tool_calls.items())
            ]
        result = from_openai_message(message, finish_reason)
        result.usage = usage
        return result

    @staticmethod
    def _usage(usage: Optional[dict[str, Any]]) -> dict[str, int]:
        if not usage:
            return {}
        return {
            "input_tokens": usage.get("prompt_tokens", 0) or 0,
            "output_tokens": usage.get("completion_tokens", 0) or 0,
        }

    # ------------------------------------------------------------------ #

    def extract(
        self,
        *,
        model: str,
        prompt: str,
        schema: Type[T],
        system: Optional[str] = None,
        max_tokens: int = 4096,
    ) -> T:
        json_schema = schema.model_json_schema()
        messages: list[dict[str, Any]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        payload: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "messages": messages,
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": schema.__name__, "schema": json_schema, "strict": True},
            },
            **self.extra_body,
        }
        try:
            data = self._post(payload)
            text = data["choices"][0]["message"]["content"] or ""
        except RuntimeError:
            # Endpoint doesn't support response_format — fall back to instruction + parse
            payload.pop("response_format")
            payload["messages"] = messages + [{
                "role": "system",
                "content": "Respond ONLY with a JSON object matching this schema, no prose:\n"
                + json.dumps(json_schema),
            }]
            data = self._post(payload)
            text = data["choices"][0]["message"]["content"] or ""
        return schema.model_validate_json(_strip_fences(text))


def _strip_fences(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else text
        if text.endswith("```"):
            text = text[: -3]
    return text.strip()
