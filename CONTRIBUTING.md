# Contributing to agentyodha

Thanks for your interest! agentyodha aims to stay **small, configuration-first,
and secure by default**. Contributions that add power without adding required
complexity are the ones most likely to land.

## Development setup

```bash
git clone <your-fork>
cd agentyodha
pip install -e .
python tests/test_smoke.py     # must pass offline, with no API key
```

The offline suite is the contract: everything that can be verified without a
live LLM call must be. If your change needs a live model to test manually,
say so in the PR, but still add offline coverage for the logic around it.

## Architecture at a glance

```
User input
  └─ Agent.run()                       agent.py
       ├─ BudgetTracker (rate/tokens)  agent_security.py
       ├─ input Guards                 guardrails.py
       ├─ ModelProvider.complete()     providers/  ← any LLM behind one interface
       ├─ tool loop
       │    ├─ PermissionPolicy (allow/deny/ask)
       │    ├─ schema validation of tool input
       │    └─ Tool.fn()               tools.py / http_tools.py
       ├─ output Guards
       └─ AuditLog (hash-chained)
```

The internal message format is Anthropic-style content blocks
(`text` / `tool_use` / `tool_result`); providers translate to their own wire
protocol and normalize responses back. That keeps the loop, guardrails,
testing, and playground identical across backends.

## Extension points (the supported ways to add things)

| To add… | Do this | Where |
|---|---|---|
| A tool | `@tool` on a typed, docstringed function | `tools.py` |
| A REST API as tools | `endpoints:` section in YAML — no code | `http_tools.py` |
| An LLM backend | Subclass `ModelProvider` (implement `complete` + `extract`), register in `build_provider` | `providers/` |
| A guard | Subclass `Guard` (implement `check`), add to `_GUARD_TYPES` | `guardrails.py` |
| A test assertion | Extend `Expectation` + `AgentTester.evaluate` | `testing.py` |

## Ground rules

- **Security is not optional.** Anything that opens a connection goes through
  `security.py` (URL/TLS validation, env-var-only credentials, timeouts,
  redaction). Anything that executes model-chosen actions goes through
  permissions + schema validation. PRs that bypass these will be asked to fix it.
- **No secrets anywhere in the repo** — not in code, tests, fixtures, or docs.
- **No copied code.** All contributions must be your original work (or clearly
  licensed compatible snippets with attribution proposed in the PR first).
  By contributing you agree your contribution is licensed under the MIT license.
- **Keep dependencies minimal.** New runtime dependencies need a strong reason;
  the playground intentionally uses only the standard library.
- Match the existing style: type hints, focused docstrings, small modules.

## Pull requests

1. Fork, branch from `main`.
2. Add or update offline tests in `tests/`.
3. Make sure `python tests/test_smoke.py` passes and CI is green.
4. Describe **what** changed and **why**; call out any security-relevant surface.

## Reporting bugs / requesting features

Use the issue templates. For anything security-sensitive, follow
[SECURITY.md](SECURITY.md) instead of opening a public issue.
