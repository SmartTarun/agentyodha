# Security Policy

## Reporting a vulnerability

Please **do not** open a public issue for security problems. Email
**tarun.vangari@gmail.com** with:

- a description of the issue and its impact,
- steps to reproduce (a minimal config/prompt is ideal),
- the version or commit you tested.

You should get an acknowledgement within a few days. Please allow a reasonable
window for a fix before public disclosure.

## Supported versions

| Version | Supported |
|---|---|
| latest `main` / most recent release | ✅ |
| older releases | ❌ |

## Security design (what the framework already enforces)

- **Credentials:** never read from config files — env-var references only;
  config loading refuses anything credential-shaped (`security.py`).
- **Transport:** HTTPS required for remote LLM/API endpoints; TLS verification
  cannot be disabled off-loopback; all requests have timeouts and size caps.
- **Agent boundary:** per-tool allow/deny/ask permissions, run/session budgets,
  schema validation of model-supplied tool inputs, input/output guardrails,
  and a hash-chained audit log (`agent_security.py`, `guardrails.py`).
- **Playground:** localhost bind + per-run session token by default.

## Honest limitations (please don't file these as vulnerabilities)

- The `prompt_injection` guard is a heuristic screen, not a guarantee. Treat
  model output as untrusted; use `deny`/`ask` permissions and budgets for
  anything destructive.
- The playground is a developer tool. Even with token auth, do not expose it
  to untrusted networks.
- Tools execute in-process with the agent. Sandboxing what a tool itself does
  (filesystem, subprocess) is the tool author's responsibility.
