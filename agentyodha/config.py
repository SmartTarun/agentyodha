"""Configuration models and YAML loading.

An agent is defined declaratively in `agentyodha.yaml`:

    defaults:
      model: claude-opus-4-8
      effort: high

    agents:
      assistant:
        system: You are a helpful assistant.
        tools: [get_weather, calculate]
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal, Optional

import yaml
from pydantic import BaseModel, Field

DEFAULT_MODEL = "claude-opus-4-8"

Effort = Literal["low", "medium", "high", "xhigh", "max"]
ThinkingMode = Literal["adaptive", "disabled"]
ThinkingDisplay = Literal["summarized", "omitted"]


class ProviderConfig(BaseModel):
    """Connection settings for one LLM backend.

    Security policy: credentials are referenced by environment-variable NAME
    (`api_key_env`) and never stored in config; remote endpoints must be HTTPS;
    TLS verification can only be disabled for loopback addresses.
    """

    type: Literal["anthropic", "openai_compatible"] = "anthropic"
    base_url: Optional[str] = None
    api_key_env: Optional[str] = None
    verify_tls: bool = True
    timeout_seconds: float = 120.0
    stream: bool = True
    extra_headers: dict[str, str] = Field(default_factory=dict)
    extra_body: dict = Field(default_factory=dict)


class GuardrailsSpec(BaseModel):
    """Guard definitions for both directions of the agent boundary."""

    input: list[dict] = Field(default_factory=list)
    output: list[dict] = Field(default_factory=list)


Decision = Literal["allow", "deny", "ask"]


class PermissionsSpec(BaseModel):
    """Per-tool execution policy. 'ask' requires an approval hook (e.g. --approve)."""

    default: Decision = "allow"
    tools: dict[str, Decision] = Field(default_factory=dict)


class BudgetSpec(BaseModel):
    """Hard spend/abuse limits for an agent."""

    max_tool_calls_per_run: Optional[int] = 25
    max_tokens_per_session: Optional[int] = None
    max_runs_per_minute: Optional[int] = None


class AgentConfig(BaseModel):
    """Configuration for a single agent."""

    name: str = "agent"
    provider: str = "anthropic"  # key into the top-level `providers:` section
    model: str = DEFAULT_MODEL
    system: Optional[str] = None
    tools: list[str] = Field(default_factory=list)
    max_tokens: int = 64000
    effort: Effort = "high"
    thinking: ThinkingMode = "adaptive"
    thinking_display: ThinkingDisplay = "summarized"
    # Prompt caching for the system prompt (recommended for long/stable prompts)
    cache_system_prompt: bool = True
    # Persist conversation history to disk under this directory (None = in-memory only)
    memory_dir: Optional[str] = None
    # Safety valve for the agentic loop
    max_iterations: int = 25
    guardrails: Optional[GuardrailsSpec] = None
    permissions: PermissionsSpec = Field(default_factory=PermissionsSpec)
    budget: BudgetSpec = Field(default_factory=BudgetSpec)
    # Tamper-evident audit trail (hash-chained JSONL) written under this directory
    audit_dir: Optional[str] = None


class FrameworkConfig(BaseModel):
    """Top-level config: shared defaults plus named agents."""

    defaults: dict = Field(default_factory=dict)
    providers: dict[str, ProviderConfig] = Field(default_factory=dict)
    agents: dict[str, AgentConfig] = Field(default_factory=dict)
    # endpoints: name -> raw endpoint spec (parsed and registered by agentyodha.http_tools)
    endpoints: dict[str, dict] = Field(default_factory=dict)
    # tests: agent name -> list of raw test-case dicts (parsed by agentyodha.testing)
    tests: dict[str, list[dict]] = Field(default_factory=dict)

    def get_agent(self, name: str) -> AgentConfig:
        try:
            return self.agents[name]
        except KeyError:
            available = ", ".join(sorted(self.agents)) or "(none)"
            raise KeyError(f"Unknown agent {name!r}. Configured agents: {available}") from None

    def get_provider(self, name: str) -> ProviderConfig:
        if name in self.providers:
            return self.providers[name]
        if name == "anthropic":  # implicit default backend
            return ProviderConfig(type="anthropic")
        available = ", ".join(sorted(self.providers)) or "anthropic (implicit)"
        raise KeyError(f"Unknown provider {name!r}. Configured providers: {available}")

    def build_agent(self, name: str, **agent_kwargs):
        """Construct an Agent with its configured provider wired in."""
        from agentyodha.agent import Agent
        from agentyodha.providers import build_provider

        agent_config = self.get_agent(name)
        provider = build_provider(self.get_provider(agent_config.provider))
        return Agent(agent_config, provider=provider, **agent_kwargs)


def load_config(path: str | Path = "agentyodha.yaml") -> FrameworkConfig:
    """Load a FrameworkConfig from YAML, merging `defaults` into every agent."""
    path = Path(path)
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}

    # Security gate: refuse configs that embed credentials
    from agentyodha.security import assert_no_inline_secrets

    assert_no_inline_secrets(raw, path=str(path))

    defaults: dict = raw.get("defaults") or {}
    agents_raw: dict = raw.get("agents") or {}
    providers_raw: dict = raw.get("providers") or {}
    providers = {name: ProviderConfig(**(spec or {})) for name, spec in providers_raw.items()}

    agents: dict[str, AgentConfig] = {}
    for agent_name, overrides in agents_raw.items():
        merged = {**defaults, **(overrides or {}), "name": agent_name}
        agents[agent_name] = AgentConfig(**merged)

    endpoints_raw: dict = raw.get("endpoints") or {}
    if endpoints_raw:
        # Declarative HTTP tools: validate + register so agents can use them by name
        from agentyodha.http_tools import build_endpoint_tools

        build_endpoint_tools(endpoints_raw)

    return FrameworkConfig(
        defaults=defaults,
        providers=providers,
        agents=agents,
        endpoints=endpoints_raw,
        tests=raw.get("tests") or {},
    )
