"""Command-line interface: `agentyodha chat <agent>` and `agentyodha list`."""

from __future__ import annotations

import argparse
import importlib
import sys

from agentyodha.config import load_config


def _import_tool_modules(modules: list[str]) -> None:
    """Import user modules so their @tool decorators register with the registry."""
    sys.path.insert(0, ".")
    for module in modules:
        importlib.import_module(module)


def cmd_list(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    if not config.agents:
        print("No agents configured.")
        return 0
    for name, agent_config in config.agents.items():
        tool_list = ", ".join(agent_config.tools) or "no tools"
        print(
            f"{name:<20} provider={agent_config.provider}  model={agent_config.model}  "
            f"effort={agent_config.effort}  [{tool_list}]"
        )
    return 0


def cmd_chat(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    agent_config = config.get_agent(args.agent)
    if args.tools_module:
        _import_tool_modules(args.tools_module)

    def approve(name: str, tool_input: dict) -> bool:
        answer = input(f"\n[tool] {name}({tool_input}) — allow? [y/N] ").strip().lower()
        return answer in ("y", "yes")

    # Only wire the approver when asked for — tools with an "ask" permission
    # policy are denied (not silently allowed) when no approver is connected.
    agent = config.build_agent(
        args.agent,
        session_id=args.session,
        on_tool_call=approve if args.approve else None,
        on_text=lambda delta: print(delta, end="", flush=True),
    )

    print(f"Chatting with {args.agent!r} (model={agent_config.model}). Ctrl+C or 'exit' to quit.\n")
    while True:
        try:
            user_input = input("you> ").strip()
        except (KeyboardInterrupt, EOFError):
            print()
            break
        if not user_input:
            continue
        if user_input.lower() in ("exit", "quit"):
            break
        print("agent> ", end="", flush=True)
        result = agent.run(user_input)
        print()  # newline after streamed text
        if result.refused:
            print("[the model declined this request]")
        if args.verbose:
            print(
                f"[stop={result.stop_reason} iterations={result.iterations} "
                f"tools={len(result.tool_calls)} usage={result.usage}]"
            )
    return 0


def cmd_test(args: argparse.Namespace) -> int:
    from agentyodha.testing import AgentTester, TestCase

    config = load_config(args.config)
    if args.tools_module:
        _import_tool_modules(args.tools_module)

    targets = [args.agent] if args.agent else sorted(config.tests)
    if not targets:
        print("No tests configured (add a `tests:` section to your config).")
        return 0

    all_passed = True
    for name in targets:
        raw_cases = config.tests.get(name, [])
        if not raw_cases:
            print(f"No tests for agent {name!r}.")
            continue
        cases = [TestCase(**c) for c in raw_cases]
        tester = AgentTester(lambda n=name: config.build_agent(n, session_id="__test__"))
        report = tester.run_suite(cases)
        print(report.summary())
        print()
        all_passed = all_passed and report.passed
    return 0 if all_passed else 1


def cmd_serve(args: argparse.Namespace) -> int:
    from agentyodha.playground import serve

    config = load_config(args.config)
    if args.tools_module:
        _import_tool_modules(args.tools_module)
    serve(config, host=args.host, port=args.port, require_auth=not args.no_auth)
    return 0


_INIT_CONFIG = """\
# agentyodha project — see README at https://your-repo for full reference.
providers:
  anthropic:
    type: anthropic            # uses ANTHROPIC_API_KEY / `ant auth login`
  # local:
  #   type: openai_compatible
  #   base_url: http://127.0.0.1:11434/v1   # Ollama / vLLM / LM Studio

defaults:
  model: claude-opus-4-8
  effort: high
  thinking: adaptive

# Connect agents to YOUR API with zero code — each entry becomes a tool.
# endpoints:
#   my_api:
#     base_url: https://api.mycompany.com
#     auth: {type: bearer, api_key_env: MY_API_KEY}
#     tools:
#       - name: get_item
#         description: Fetch an item by id.
#         method: GET
#         path: /v1/items/{item_id}
#         params:
#           item_id: {type: string, in: path, description: Item id}

agents:
  assistant:
    system: You are a precise, helpful assistant.
    tools: [greet]             # from tools.py; add endpoint tool names here too
    memory_dir: .agentyodha/sessions
    audit_dir: .agentyodha/audit
    guardrails:
      input:
        - {type: prompt_injection, action: block}
      output:
        - {type: pii_redact}
    budget:
      max_tool_calls_per_run: 10
      max_runs_per_minute: 20

tests:
  assistant:
    - name: greets_politely
      prompt: Say hello to Sam.
      expect:
        uses_tool: greet
        contains: [Sam]
"""

_INIT_TOOLS = '''\
"""Project tools — plain functions; type hints + docstrings become the schema."""

from agentyodha import tool


@tool
def greet(name: str) -> str:
    """Produce a friendly greeting for a person.

    Args:
        name: The person to greet.
    """
    return f"Hello, {name}! Welcome aboard."
'''

_INIT_ENV = """\
# Copy to your shell / secrets manager — agentyodha never reads keys from YAML.
ANTHROPIC_API_KEY=
# MY_API_KEY=
# OPENAI_API_KEY=
"""

_INIT_GITIGNORE = """\
__pycache__/
*.pyc
.venv/
.agentyodha/
.env
"""


def cmd_init(args: argparse.Namespace) -> int:
    from pathlib import Path

    target = Path(args.directory)
    target.mkdir(parents=True, exist_ok=True)
    files = {
        "agentyodha.yaml": _INIT_CONFIG,
        "tools.py": _INIT_TOOLS,
        ".env.example": _INIT_ENV,
        ".gitignore": _INIT_GITIGNORE,
    }
    for name, content in files.items():
        path = target / name
        if path.exists():
            print(f"skip   {path} (already exists)")
        else:
            path.write_text(content, encoding="utf-8")
            print(f"create {path}")
    print(
        "\nNext steps:\n"
        f"  cd {target}\n"
        "  set ANTHROPIC_API_KEY (see .env.example)\n"
        "  agentyodha chat assistant --tools-module tools\n"
        "  agentyodha test assistant --tools-module tools\n"
        "  agentyodha serve --tools-module tools     # Agent Swagger playground"
    )
    return 0


def cmd_audit(args: argparse.Namespace) -> int:
    from pathlib import Path

    from agentyodha.agent_security import AuditLog

    target = Path(args.path)
    files = sorted(target.glob("*.audit.jsonl")) if target.is_dir() else [target]
    if not files:
        print(f"No audit files found under {target}.")
        return 1

    all_ok = True
    for file in files:
        ok, count, error = AuditLog.verify(file)
        status = "OK" if ok else "TAMPERED"
        print(f"[{status}] {file}  ({count} entries verified)")
        if error:
            print(f"           {error}")
        all_ok = all_ok and ok
    return 0 if all_ok else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="agentyodha", description="Run configurable Claude agents.")
    parser.add_argument("--config", default="agentyodha.yaml", help="Path to the YAML config file")
    subparsers = parser.add_subparsers(dest="command", required=True)

    p_init = subparsers.add_parser("init", help="Scaffold a new agentyodha project")
    p_init.add_argument("directory", nargs="?", default=".", help="Target directory (default: .)")
    p_init.set_defaults(func=cmd_init)

    p_list = subparsers.add_parser("list", help="List configured agents")
    p_list.set_defaults(func=cmd_list)

    p_chat = subparsers.add_parser("chat", help="Interactive chat with an agent")
    p_chat.add_argument("agent", help="Agent name from the config file")
    p_chat.add_argument("--session", default="default", help="Session id for persisted memory")
    p_chat.add_argument(
        "--tools-module",
        action="append",
        default=[],
        help="Python module to import for @tool registrations (repeatable)",
    )
    p_chat.add_argument("--approve", action="store_true", help="Ask before every tool call")
    p_chat.add_argument("--verbose", action="store_true", help="Print loop/usage stats after each turn")
    p_chat.set_defaults(func=cmd_chat)

    p_test = subparsers.add_parser("test", help="Run declarative test suites against agents")
    p_test.add_argument("agent", nargs="?", help="Agent to test (default: all agents with tests)")
    p_test.add_argument(
        "--tools-module", action="append", default=[],
        help="Python module to import for @tool registrations (repeatable)",
    )
    p_test.set_defaults(func=cmd_test)

    p_serve = subparsers.add_parser("serve", help="Start the Agent Swagger web playground")
    p_serve.add_argument("--host", default="127.0.0.1")
    p_serve.add_argument("--port", type=int, default=8420)
    p_serve.add_argument(
        "--tools-module", action="append", default=[],
        help="Python module to import for @tool registrations (repeatable)",
    )
    p_serve.add_argument(
        "--no-auth", action="store_true",
        help="Disable the playground session token (localhost convenience only)",
    )
    p_serve.set_defaults(func=cmd_serve)

    p_audit = subparsers.add_parser("audit", help="Verify the integrity of hash-chained audit logs")
    p_audit.add_argument("path", help="An .audit.jsonl file, or a directory of them")
    p_audit.set_defaults(func=cmd_audit)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
