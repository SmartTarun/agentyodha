"""Provider abstraction: one normalized interface, any LLM behind it.

The Agent speaks a single internal message format (Anthropic-style content
blocks: text / tool_use / tool_result). Each provider translates that format
to its own wire protocol and normalizes the response back, so the agent loop,
guardrails, memory, and test harness work identically across providers.

Normalized stop reasons: end_turn | tool_use | max_tokens | refusal | pause_turn
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable, Optional, Type, TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


@dataclass
class CompletionRequest:
    """A single model turn, in the framework's neutral format."""

    model: str
    messages: list[dict[str, Any]]           # Anthropic-style content blocks
    system: Optional[str] = None
    tools: list[dict[str, Any]] = field(default_factory=list)  # name/description/input_schema
    max_tokens: int = 4096
    effort: str = "high"
    thinking: str = "adaptive"                # "adaptive" | "disabled"
    thinking_display: str = "summarized"
    cache_system: bool = True
    on_text: Optional[Callable[[str], None]] = None


@dataclass
class ProviderResponse:
    """A normalized model response."""

    content: list[dict[str, Any]]             # Anthropic-style blocks (dicts)
    stop_reason: str
    usage: dict[str, int] = field(default_factory=dict)

    def tool_uses(self) -> list[dict[str, Any]]:
        return [b for b in self.content if b.get("type") == "tool_use"]

    def text(self) -> str:
        return "\n".join(b.get("text", "") for b in self.content if b.get("type") == "text")


class ModelProvider(ABC):
    """Interface every LLM backend implements."""

    name: str = "provider"

    @abstractmethod
    def complete(self, request: CompletionRequest) -> ProviderResponse:
        """Run one completion turn (streaming text deltas to request.on_text if set)."""

    @abstractmethod
    def extract(
        self,
        *,
        model: str,
        prompt: str,
        schema: Type[T],
        system: Optional[str] = None,
        max_tokens: int = 4096,
    ) -> T:
        """One-shot structured extraction into a validated Pydantic model."""
