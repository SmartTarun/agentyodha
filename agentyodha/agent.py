"""The Agent: a provider-agnostic agentic loop.

The loop, guardrails, memory, and tool execution are identical regardless of
which LLM sits behind the ModelProvider (Anthropic, OpenAI, Ollama, vLLM, ...).

Features:
- Streaming by default
- Automatic tool-use loop with parallel tool execution results
- pause_turn / refusal / max_tokens stop-reason handling
- Input/output guardrails
- Optional human-in-the-loop tool approval hook
- Structured output extraction into Pydantic models
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable, Optional, Type, TypeVar

from pydantic import BaseModel

from agentyodha.agent_security import (
    AuditLog,
    BudgetExceeded,
    BudgetTracker,
    PermissionPolicy,
    validate_tool_input,
)
from agentyodha.config import AgentConfig
from agentyodha.guardrails import GuardResult, build_guards, run_guards
from agentyodha.memory import ConversationStore
from agentyodha.providers import AnthropicProvider, CompletionRequest, ModelProvider, ProviderResponse
from agentyodha.tools import Tool, registry

T = TypeVar("T", bound=BaseModel)

# Called before each tool executes: (tool_name, tool_input) -> allow?
ApprovalHook = Callable[[str, dict[str, Any]], bool]
# Called with each streamed text delta
TextHook = Callable[[str], None]


@dataclass
class AgentResult:
    """Outcome of one Agent.run() call."""

    text: str
    stop_reason: Optional[str]
    refused: bool = False
    blocked: bool = False  # a guardrail stopped the input or withheld the output
    iterations: int = 0
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    usage: dict[str, int] = field(default_factory=dict)
    guard_results: list[GuardResult] = field(default_factory=list)


class Agent:
    """A stateful agent driven by an AgentConfig."""

    def __init__(
        self,
        config: AgentConfig,
        tools: Optional[list[Tool]] = None,
        provider: Optional[ModelProvider] = None,
        session_id: str = "default",
        on_tool_call: Optional[ApprovalHook] = None,
        on_text: Optional[TextHook] = None,
    ):
        self.config = config
        self.provider = provider or AnthropicProvider()
        self.tools = tools if tools is not None else registry.resolve(config.tools)
        self.on_tool_call = on_tool_call
        self.on_text = on_text
        self.session_id = session_id

        rails = config.guardrails
        self.input_guards = build_guards(rails.input if rails else [])
        self.output_guards = build_guards(rails.output if rails else [])

        self.permissions = PermissionPolicy(
            default=config.permissions.default, tools=config.permissions.tools
        )
        self.budget = BudgetTracker(
            max_tool_calls_per_run=config.budget.max_tool_calls_per_run,
            max_tokens_per_session=config.budget.max_tokens_per_session,
            max_runs_per_minute=config.budget.max_runs_per_minute,
        )
        self.audit: Optional[AuditLog] = (
            AuditLog(config.audit_dir, session_id) if config.audit_dir else None
        )

        self.store: Optional[ConversationStore] = None
        self.messages: list[dict[str, Any]] = []
        if config.memory_dir:
            self.store = ConversationStore(config.memory_dir)
            self.messages = self.store.load(session_id)

    # ------------------------------------------------------------------ #
    # Request construction
    # ------------------------------------------------------------------ #

    def _request(self) -> CompletionRequest:
        return CompletionRequest(
            model=self.config.model,
            messages=self.messages,
            system=self.config.system,
            tools=[t.to_api() for t in self.tools],
            max_tokens=self.config.max_tokens,
            effort=self.config.effort,
            thinking=self.config.thinking,
            thinking_display=self.config.thinking_display,
            cache_system=self.config.cache_system_prompt,
            on_text=self.on_text,
        )

    # ------------------------------------------------------------------ #
    # Core loop
    # ------------------------------------------------------------------ #

    def run(self, user_input: str) -> AgentResult:
        """Send a user message and run the agentic loop until the model is done."""
        result = AgentResult(text="", stop_reason=None)

        # Budget: run-rate and session token limits
        try:
            self.budget.check_run_allowed()
        except BudgetExceeded as exc:
            result.blocked = True
            result.stop_reason = "budget_exceeded"
            result.text = f"[run blocked: {exc}]"
            self._audit("budget_blocked", {"reason": str(exc)})
            return result

        # Input guardrails: screen the user message before it reaches the model
        user_input, in_results, blocked = run_guards(self.input_guards, user_input, "input")
        result.guard_results.extend(in_results)
        self._audit("run_start", {
            "input": user_input,
            "guards": [g.guard for g in in_results if not g.passed],
        })
        if blocked:
            result.blocked = True
            result.stop_reason = "guardrail_blocked"
            result.text = "[input blocked by guardrail]"
            self._audit("run_end", {"stop_reason": result.stop_reason, "blocked": True})
            return result

        self.messages.append({"role": "user", "content": user_input})
        usage_totals: dict[str, int] = {}

        for iteration in range(1, self.config.max_iterations + 1):
            result.iterations = iteration

            response = self.provider.complete(self._request())

            for key, value in response.usage.items():
                usage_totals[key] = usage_totals.get(key, 0) + value
            self.budget.add_usage(response.usage)

            self.messages.append({"role": "assistant", "content": response.content})
            result.stop_reason = response.stop_reason

            if response.stop_reason == "refusal":
                result.refused = True
                break

            if response.stop_reason == "pause_turn":
                continue  # server-side tool paused; re-send to resume

            if response.stop_reason == "tool_use":
                tool_results = self._execute_tools(response, result)
                # All results for one assistant turn go back in a single user message
                self.messages.append({"role": "user", "content": tool_results})
                continue

            # end_turn / max_tokens / stop_sequence — we're done
            break

        result.text = self._last_text()
        result.usage = usage_totals

        # Output guardrails: redact/truncate/withhold before returning to the caller
        final_text, out_results, blocked = run_guards(self.output_guards, result.text, "output")
        result.guard_results.extend(out_results)
        if blocked:
            result.blocked = True
            result.text = "[response withheld by output guardrail]"
        else:
            result.text = final_text

        self._audit("run_end", {
            "stop_reason": result.stop_reason,
            "iterations": result.iterations,
            "tool_calls": [c["name"] for c in result.tool_calls],
            "usage": usage_totals,
            "blocked": result.blocked,
            "refused": result.refused,
        })
        self._persist()
        return result

    def _execute_tools(
        self, response: ProviderResponse, result: AgentResult
    ) -> list[dict[str, Any]]:
        tool_results: list[dict[str, Any]] = []
        for block in response.tool_uses():
            tool_id, tool_name = block["id"], block["name"]
            tool_input = block.get("input") or {}
            result.tool_calls.append({"name": tool_name, "input": tool_input})

            def refuse(reason: str, event: str = "tool_denied") -> None:
                self._audit(event, {"tool": tool_name, "input": tool_input, "reason": reason})
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": tool_id,
                    "content": reason,
                    "is_error": True,
                })

            # 1. Per-run tool budget (the denial goes back to the model so it wraps up)
            if not self.budget.tool_calls_allowed(len(result.tool_calls) - 1):
                refuse("Tool budget for this run is exhausted; finish with what you have.",
                       event="tool_budget_exhausted")
                continue

            # 2. Permission policy: allow / deny / ask
            decision = self.permissions.decision(tool_name)
            if decision == "deny":
                refuse(f"Tool {tool_name!r} is denied by this agent's permission policy.")
                continue
            needs_approval = decision == "ask"
            if needs_approval and self.on_tool_call is None:
                refuse(f"Tool {tool_name!r} requires approval but no approver is connected.")
                continue
            if self.on_tool_call and not self.on_tool_call(tool_name, tool_input):
                refuse("Tool call denied by the user.")
                continue

            # 3. Look up the tool and validate the (untrusted) input against its schema
            tool_obj = next((t for t in self.tools if t.name == tool_name), None)
            if tool_obj is None:
                refuse(f"Error: tool {tool_name!r} is not available.")
                continue
            schema_error = validate_tool_input(tool_obj.input_schema, tool_input)
            if schema_error:
                refuse(f"Invalid tool input: {schema_error}", event="tool_input_rejected")
                continue

            # 4. Execute (tool errors go back to the model, not up the stack)
            try:
                output = tool_obj(**tool_input)
                content = output if isinstance(output, str) else json.dumps(output)
                self._audit("tool_call", {"tool": tool_name, "input": tool_input, "ok": True})
                tool_results.append(
                    {"type": "tool_result", "tool_use_id": tool_id, "content": content}
                )
            except Exception as exc:
                self._audit("tool_call", {
                    "tool": tool_name, "input": tool_input, "ok": False, "error": str(exc),
                })
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": tool_id,
                    "content": f"Error: {exc}",
                    "is_error": True,
                })
        return tool_results

    def _audit(self, event: str, data: dict[str, Any]) -> None:
        if self.audit:
            self.audit.record(event, data)

    # ------------------------------------------------------------------ #
    # Structured output
    # ------------------------------------------------------------------ #

    def extract(self, user_input: str, schema: Type[T]) -> T:
        """One-shot extraction of a validated Pydantic model (no tools, no history)."""
        return self.provider.extract(
            model=self.config.model,
            prompt=user_input,
            schema=schema,
            system=self.config.system,
            max_tokens=self.config.max_tokens,
        )

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #

    def _last_text(self) -> str:
        for message in reversed(self.messages):
            if message["role"] != "assistant":
                continue
            content = message["content"]
            if isinstance(content, str):
                return content
            parts = [b.get("text", "") for b in content if b.get("type") == "text"]
            if parts:
                return "\n".join(parts)
        return ""

    def _persist(self) -> None:
        if self.store:
            self.store.save(self.session_id, self.messages)

    def reset(self) -> None:
        """Clear conversation history (and its persisted copy)."""
        self.messages = []
        if self.store:
            self.store.delete(self.session_id)
