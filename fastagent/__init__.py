"""fastagent — a lightweight, configuration-first framework for Claude-powered agents."""

from fastagent.agent import Agent, AgentResult
from fastagent.agent_security import AuditLog, BudgetTracker, PermissionPolicy
from fastagent.config import (
    AgentConfig,
    BudgetSpec,
    FrameworkConfig,
    GuardrailsSpec,
    PermissionsSpec,
    ProviderConfig,
    load_config,
)
from fastagent.guardrails import Guard, GuardResult, build_guards
from fastagent.memory import ConversationStore
from fastagent.providers import AnthropicProvider, ModelProvider, build_provider
from fastagent.security import EndpointSecurityError
from fastagent.testing import AgentTester, Expectation, TestCase
from fastagent.tools import Tool, registry, tool

__version__ = "0.1.0"
__author__ = "Tarun Vangari"
__license__ = "MIT"

__all__ = [
    "Agent",
    "AgentConfig",
    "AgentResult",
    "AgentTester",
    "AnthropicProvider",
    "AuditLog",
    "BudgetSpec",
    "BudgetTracker",
    "PermissionPolicy",
    "PermissionsSpec",
    "ConversationStore",
    "EndpointSecurityError",
    "Expectation",
    "FrameworkConfig",
    "Guard",
    "GuardResult",
    "GuardrailsSpec",
    "ModelProvider",
    "ProviderConfig",
    "TestCase",
    "Tool",
    "build_guards",
    "build_provider",
    "load_config",
    "registry",
    "tool",
]
