# Runbook: Build, Secure, Test, and Operate an Agent with agentyodha

**Owner:** Tarun Vangari (framework maintainer) | **Frequency:** As needed (per new agent)
**Last Updated:** 2026-07-08 | **Last Run:** 2026-07-08 (framework verification on Windows 11 / Python 3.12)

### Purpose

Take a teammate from a clean machine to a **working, secured, tested agent**
built on [agentyodha](https://github.com/SmartTarun/agentyodha) — including
connecting it to an internal API, locking it down (permissions, budgets,
guardrails, audit), and proving it behaves with the test harness and the
Agent Swagger playground. Use it when creating any new agent, or as the
checklist when reviewing someone else's.

### Prerequisites

- [ ] Python **3.10+** and `pip` on PATH (`python --version`)
- [ ] An LLM credential — one of:
  - `ANTHROPIC_API_KEY` exported (or `ant auth login` completed), **or**
  - a local OpenAI-compatible server running (e.g. Ollama at `http://127.0.0.1:11434/v1`)
- [ ] If the agent will call an internal API: its base URL (must be **https** unless loopback) and an API key exported as an environment variable
- [ ] Git, if the agent config will be version-controlled (it should be)

> **Never place API keys in YAML, code, or this runbook.** agentyodha refuses
> to start if it finds anything credential-shaped in config.

### Procedure

#### Step 1: Install the framework

```bash
pip install agentyodha
# Until the first PyPI release is published, install from GitHub instead:
pip install git+https://github.com/SmartTarun/agentyodha.git
```

**Expected result:** `agentyodha --help` prints the six commands: `init, list, chat, test, serve, audit`.
**If it fails:** On Windows, an `OSError ... Long Path` during install means Windows long-path support is off — enable it (see https://pip.pypa.io/warnings/enable-long-paths) or install into a venv at a short path like `C:\venvs\ay`.

#### Step 2: Scaffold the project

```bash
agentyodha init my-agent
cd my-agent
```

**Expected result:** Four files created: `agentyodha.yaml`, `tools.py`, `.env.example`, `.gitignore`, plus printed next steps. Re-running is safe (existing files are skipped, never overwritten).
**If it fails:** Check directory write permissions; run from a folder you own.

#### Step 3: Set the LLM credential and pick the provider

```bash
# Anthropic (default — nothing to change in YAML):
set ANTHROPIC_API_KEY=<your key>          # Windows cmd; use $env: in PowerShell, export in bash

# OR a local/other model: uncomment the provider block in agentyodha.yaml, e.g.
#   providers:
#     local:
#       type: openai_compatible
#       base_url: http://127.0.0.1:11434/v1
# and set `provider: local` + a model name (e.g. llama3.1) on the agent.
```

**Expected result:** `agentyodha list` prints the `assistant` agent with its provider and model.
**If it fails:** `Environment variable 'X' is not set` → export it. `Refusing plain-HTTP remote endpoint` → the base_url must be https unless it's 127.0.0.1/localhost.

#### Step 4: Define the agent's behavior

Edit `agentyodha.yaml`: set the `system` prompt (who the agent is, what it must/must not do), `model`, and `effort` (`low` for cheap/fast routes, `high` default, `xhigh` for the hardest work).

**Expected result:** `agentyodha list` still succeeds (it re-validates the whole config, including the secret scan).
**If it fails:** Pydantic will name the exact bad field and value — fix the YAML at that path.

#### Step 5: Add tools

Python tools — add typed, docstringed functions to `tools.py`:

```python
from agentyodha import tool

@tool
def lookup_order(order_id: str) -> str:
    """Fetch an order's status from the order system.

    Args:
        order_id: The internal order identifier.
    """
    ...
```

Your REST API as tools — zero code, in `agentyodha.yaml`:

```yaml
endpoints:
  orders_api:
    base_url: https://api.mycompany.com
    auth: {type: bearer, api_key_env: ORDERS_API_KEY}
    tools:
      - name: get_order
        description: Fetch an order by id.
        method: GET
        path: /v1/orders/{order_id}
        params:
          order_id: {type: string, in: path, description: Order id}
```

Then list every tool name under the agent's `tools:` key.

**Expected result:** `agentyodha list` shows the tool names next to the agent.
**If it fails:** `Unknown tool 'x'` at chat-time → Python tools need `--tools-module tools` on every command; endpoint tools register from YAML automatically. Path/param mismatch errors name the exact endpoint, tool, and parameter to fix.

#### Step 6: Secure the agent (do not skip)

In `agentyodha.yaml`, on the agent:

```yaml
    guardrails:
      input:
        - {type: prompt_injection, action: block}
      output:
        - {type: pii_redact}
    permissions:
      default: allow
      tools:
        <any write/destructive tool>: ask     # or deny
    budget:
      max_tool_calls_per_run: 10
      max_tokens_per_session: 500000
      max_runs_per_minute: 20
    audit_dir: .agentyodha/audit
```

Rules of thumb: anything that **writes, sends, or deletes** gets `ask` (or `deny` until proven needed); every agent gets a budget; every agent that touches real systems gets `audit_dir`.

**Expected result:** `agentyodha list` passes; guardrail/permission chips appear in the playground later.
**If it fails:** `Unknown guard type` lists valid types (`prompt_injection`, `blocklist`, `pii_redact`, `max_length`).

#### Step 7: First live run

```bash
agentyodha chat assistant --tools-module tools --verbose --approve
```

Ask something that forces a tool call. `--approve` prompts before each tool executes — keep it on until you trust the agent.

**Expected result:** Streamed answer, then a stats line like `[stop=end_turn iterations=2 tools=1 usage={...}]`.
**If it fails:** See Troubleshooting. `stop=guardrail_blocked` or `budget_exceeded` means your own policies fired — that's the system working; tune the config if it's a false positive.

#### Step 8: Write and run the test suite

Add cases under `tests:` in `agentyodha.yaml` (the scaffold includes one). Cover: the happy path with `uses_tool`, one injection attempt, and one output-shape check. Use `repeat: 3` on flaky-prone cases — the report's **confidence** is passes/runs.

```bash
agentyodha test assistant --tools-module tools
```

**Expected result:** `[PASS] <case> confidence=100% (n/n)` per case; exit code 0. Non-zero exit = at least one failure, with the failing check and the actual response printed.
**If it fails:** A failing `judge` check shows the grader's reason — fix the agent's system prompt, or the criterion if it was ambiguous.

#### Step 9: Review in the Agent Swagger playground

```bash
agentyodha serve --tools-module tools
```

Open the **tokened URL printed in the terminal** (e.g. `http://127.0.0.1:8420/?token=...`). Inspect: tool chips, guardrail chips, per-message stop reason / loop count / token usage; click **Run tests**.

**Expected result:** Agent cards render; prompts round-trip; unauthorized requests (no token) get 401.
**If it fails:** "Unauthorized" in the UI → you opened the bare URL; use the tokened one. Port in use → `--port 8500`.

#### Step 10: Verify the audit trail

```bash
agentyodha audit .agentyodha/audit
```

**Expected result:** `[OK] ... (N entries verified)` for each session file — every run, tool decision, and guard verdict is in a SHA-256 hash chain.
**If it fails:** `[TAMPERED]` with a line number means the log was edited after the fact — treat as an incident; identify who/what modified the file.

#### Step 11: Ship it

```bash
git init && git add -A && git commit -m "New agent: assistant"
```

Embed in an application with the same config:

```python
from agentyodha import load_config
import tools  # registers @tool functions

config = load_config("agentyodha.yaml")
agent = config.build_agent("assistant", session_id=user_id)
result = agent.run(user_message)
```

**Expected result:** Config (no secrets in it) is version-controlled; the app uses `build_agent`, which wires the provider + all security layers automatically.
**If it fails:** `ModuleNotFoundError: tools` in the app → the tools module must be importable from the app's working directory or package.

### Verification

- [ ] `agentyodha list` shows the agent with expected provider, model, and tools
- [ ] `agentyodha test <agent>` exits 0 with confidence ≥ the bar you set (100% for deterministic checks)
- [ ] A prompt-injection attempt in chat is blocked (`stop=guardrail_blocked`) or refused
- [ ] A destructive tool call prompts for approval (or is denied) — it never auto-executes
- [ ] `agentyodha audit .agentyodha/audit` reports `[OK]`
- [ ] `git grep -iE "sk-|api_key:"` over the project returns nothing sensitive

### Troubleshooting

| Symptom | Likely Cause | Fix |
|---------|-------------|-----|
| `Environment variable 'X' is not set` | Key not exported in this shell | Export it (see `.env.example`); never paste it into YAML |
| `Inline credential at ...` / `looks like an API credential` | A key was pasted into config | Remove it, rotate that key, use `api_key_env` |
| `Refusing plain-HTTP remote endpoint` | `base_url` is `http://` on a non-loopback host | Use `https://`, or a `127.0.0.1` address for local servers |
| `Unknown tool 'x'` | `--tools-module` missing, or name mismatch with YAML | Pass `--tools-module tools`; match `tools:` entries to registered names |
| Tool result: "requires approval but no approver is connected" | Tool has `ask` policy; no `--approve` / callback wired | Run chat with `--approve`, or wire `on_tool_call` in your app |
| `stop=budget_exceeded` immediately | Session token or run-rate budget exhausted | Raise the budget in YAML, or start a new session (`--session new-name`) |
| HTTP 404 on model name | Wrong model id for the provider | Anthropic: `claude-opus-4-8`; local: the exact name your server reports |
| Playground shows Unauthorized | Opened URL without the session token | Copy the full tokened URL from the terminal |
| `pip install` OSError about long paths (Windows) | 260-char MAX_PATH limit | Enable Windows long paths, or venv at a short path |
| Judge test errors with provider message | Judge uses the agent's provider; endpoint lacks structured output | Set a `judge_model` the provider supports, or use non-judge checks |

### Rollback

- **Bad config change:** config is in git — `git checkout -- agentyodha.yaml` (or revert the commit).
- **Polluted conversation:** `agentyodha chat` with a fresh `--session`, or delete the session file under `.agentyodha/sessions/`.
- **Misbehaving tool in production:** set that tool to `deny` in `permissions.tools` and redeploy the YAML — no code change needed.
- **Full removal:** `pip uninstall agentyodha`; the project folder and its `.agentyodha/` state are self-contained — delete them.
- Audit logs are append-only evidence — **do not delete them during an incident.**

### Escalation

| Situation | Contact | Method |
|-----------|---------|--------|
| Framework bug (reproducible) | Maintainer | GitHub issue with the bug template: https://github.com/SmartTarun/agentyodha/issues |
| Security vulnerability in the framework | Maintainer (privately) | Per [SECURITY.md](../SECURITY.md) — email, no public issue |
| `[TAMPERED]` audit log | Your security owner | Preserve the file, note timestamps, investigate file access |
| Suspected leaked API key | Key owner / provider console | Rotate the key immediately; agentyodha never stores it, but shells and CI logs might |

### History

| Date | Run By | Notes |
|------|--------|-------|
| 2026-07-08 | Tarun Vangari + Claude | Runbook created and steps verified against agentyodha 0.1.0 (9/9 offline tests; init/list/serve/audit exercised on Windows 11, Python 3.12). Live model steps (7–9) verified structurally; first execution with a real key pending. |
