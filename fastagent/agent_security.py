"""Agent-layer security: permissions, budgets, and a tamper-evident audit trail.

Where fastagent.security hardens the *connection to the LLM endpoint*, this
module hardens the *agent itself*:

- **PermissionPolicy** — per-tool allow / deny / ask, declared in YAML. A
  denied tool never executes; "ask" requires an approval hook (CLI --approve,
  or your own callback) and denies when none is wired.
- **BudgetTracker** — hard limits on tool calls per run, tokens per session,
  and runs per minute, so a runaway loop or a hostile prompt can't burn your
  quota.
- **AuditLog** — an append-only JSONL trail of every run, tool decision, and
  guard verdict. Records are SHA-256 hash-chained (each entry commits to the
  previous one), so after-the-fact tampering is detectable with `verify()`.
"""

from __future__ import annotations

import hashlib
import json
import time
from collections import deque
from pathlib import Path
from typing import Any, Literal, Optional

Decision = Literal["allow", "deny", "ask"]


class PermissionPolicy:
    """Per-tool execution policy with a default."""

    def __init__(self, default: Decision = "allow", tools: Optional[dict[str, Decision]] = None):
        self.default = default
        self.tools = tools or {}

    def decision(self, tool_name: str) -> Decision:
        return self.tools.get(tool_name, self.default)


class BudgetExceeded(Exception):
    """Raised when a session or run exceeds its configured budget."""


class BudgetTracker:
    """Enforces run-rate, per-run tool-call, and per-session token budgets."""

    def __init__(
        self,
        max_tool_calls_per_run: Optional[int] = None,
        max_tokens_per_session: Optional[int] = None,
        max_runs_per_minute: Optional[int] = None,
    ):
        self.max_tool_calls_per_run = max_tool_calls_per_run
        self.max_tokens_per_session = max_tokens_per_session
        self.max_runs_per_minute = max_runs_per_minute
        self.session_tokens = 0
        self._run_times: deque[float] = deque()

    def check_run_allowed(self) -> None:
        """Call at the start of each run; raises BudgetExceeded if over budget."""
        if self.max_runs_per_minute is not None:
            now = time.monotonic()
            while self._run_times and now - self._run_times[0] > 60.0:
                self._run_times.popleft()
            if len(self._run_times) >= self.max_runs_per_minute:
                raise BudgetExceeded(
                    f"Run rate limit reached ({self.max_runs_per_minute}/minute)."
                )
            self._run_times.append(now)
        if (
            self.max_tokens_per_session is not None
            and self.session_tokens >= self.max_tokens_per_session
        ):
            raise BudgetExceeded(
                f"Session token budget exhausted "
                f"({self.session_tokens}/{self.max_tokens_per_session})."
            )

    def tool_calls_allowed(self, calls_so_far: int) -> bool:
        if self.max_tool_calls_per_run is None:
            return True
        return calls_so_far < self.max_tool_calls_per_run

    def add_usage(self, usage: dict[str, int]) -> None:
        self.session_tokens += usage.get("input_tokens", 0) + usage.get("output_tokens", 0)


def validate_tool_input(input_schema: dict[str, Any], tool_input: dict[str, Any]) -> Optional[str]:
    """Validate a model-supplied tool input against the declared schema.

    Returns an error message, or None if valid. Defense-in-depth: the model
    usually respects schemas, but tool inputs are untrusted output and get
    checked before anything executes.
    """
    if not isinstance(tool_input, dict):
        return f"Tool input must be an object, got {type(tool_input).__name__}."
    properties = input_schema.get("properties", {})
    required = input_schema.get("required", [])
    missing = [key for key in required if key not in tool_input]
    if missing:
        return f"Missing required parameter(s): {', '.join(missing)}."
    unknown = [key for key in tool_input if key not in properties]
    if unknown:
        return f"Unknown parameter(s): {', '.join(unknown)}."
    return None


class AuditLog:
    """Append-only, hash-chained JSONL audit trail (one file per session)."""

    GENESIS = "0" * 64

    def __init__(self, directory: str | Path, session_id: str):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in session_id)
        self.path = self.directory / f"{safe}.audit.jsonl"
        self._prev_hash = self._load_last_hash()

    def _load_last_hash(self) -> str:
        if not self.path.exists():
            return self.GENESIS
        last_line = ""
        with self.path.open(encoding="utf-8") as handle:
            for line in handle:
                if line.strip():
                    last_line = line
        if not last_line:
            return self.GENESIS
        return json.loads(last_line).get("hash", self.GENESIS)

    @staticmethod
    def _entry_hash(prev_hash: str, payload: dict[str, Any]) -> str:
        canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256((prev_hash + canonical).encode("utf-8")).hexdigest()

    def record(self, event: str, data: dict[str, Any]) -> None:
        payload = {"ts": time.time(), "event": event, "data": data, "prev": self._prev_hash}
        entry = {**payload, "hash": self._entry_hash(self._prev_hash, payload)}
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
        self._prev_hash = entry["hash"]

    @classmethod
    def verify(cls, path: str | Path) -> tuple[bool, int, Optional[str]]:
        """Re-walk the hash chain. Returns (ok, entries_checked, error)."""
        path = Path(path)
        prev = cls.GENESIS
        count = 0
        with path.open(encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, 1):
                if not line.strip():
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    return False, count, f"line {line_number}: not valid JSON"
                stored_hash = entry.pop("hash", None)
                if entry.get("prev") != prev:
                    return False, count, f"line {line_number}: chain break (prev mismatch)"
                if cls._entry_hash(prev, entry) != stored_hash:
                    return False, count, f"line {line_number}: hash mismatch (record altered)"
                prev = stored_hash
                count += 1
        return True, count, None
