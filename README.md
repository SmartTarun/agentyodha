# agentyodha

[![CI](https://github.com/SmartTarun/agentyodha/actions/workflows/ci.yml/badge.svg)](https://github.com/SmartTarun/agentyodha/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](pyproject.toml)

A lightweight, **configuration-first** Python framework for building, testing, and securing LLM-powered agents — with **any LLM backend**.

Define agents in YAML. Write tools as plain Python functions — or declare your own REST API as tools with zero code. Test agent behavior like you'd test an API, with assertions, repeat-runs for confidence scores, and an interactive **Agent Swagger** web playground.

Anthropic Claude is the first-class backend (official SDK, adaptive thinking, effort control, streaming, prompt caching, correct `pause_turn`/`refusal` handling). Any OpenAI-compatible endpoint works too — OpenAI, Ollama, vLLM, LM Studio, Groq, Together, Mistral — behind one provider interface, so the agent loop, tools, guardrails, and tests are identical across backends.

## Install & start a project in one command

```bash
pip install agentyodha      # from PyPI (imports as `agentyodha`)
# or from a clone: pip install -e .
# auth: set ANTHROPIC_API_KEY, or run `ant auth login`

agentyodha init my-agent-project     # scaffolds agentyodha.yaml, tools.py, .env.example
cd my-agent-project
agentyodha chat assistant --tools-module tools
```

## 1. Define providers and agents in YAML

```yaml
# agentyodha.yaml
providers:                                 # any LLM behind one interface
  anthropic:
    type: anthropic                        # official SDK; exists implicitly too
  local:
    type: openai_compatible
    base_url: http://127.0.0.1:11434/v1    # Ollama (also vLLM, LM Studio, ...)
  openai:
    type: openai_compatible
    base_url: https://api.openai.com/v1
    api_key_env: OPENAI_API_KEY            # env-var NAME — never the key itself

defaults:
  model: claude-opus-4-8
  effort: high
  thinking: adaptive

agents:
  assistant:
    provider: anthropic                    # or local / openai / any key above
    system: You are a precise, helpful assistant.
    tools: [calculate, get_weather]
    memory_dir: .agentyodha/sessions        # persist conversations
    guardrails:
      input:
        - type: prompt_injection           # block injection attempts
          action: block
      output:
        - type: pii_redact                 # redact emails/phones/cards/SSNs
        - type: max_length
          limit: 6000
```

## 2. Write tools as plain functions

Type hints + docstring become the JSON schema automatically:

```python
from agentyodha import tool
from typing import Literal

@tool
def get_weather(location: str, unit: Literal["celsius", "fahrenheit"] = "celsius") -> str:
    """Get the current weather for a location.

    Args:
        location: City name, e.g. "Hyderabad".
        unit: Temperature unit to report.
    """
    ...
```

## 3. Connect agents to YOUR API — zero code

Most agents exist to call your own services. Declare the endpoint once; every
operation becomes a tool your agents use by name — no Python required:

```yaml
endpoints:
  crm:
    base_url: https://api.mycompany.com
    auth: {type: bearer, api_key_env: CRM_API_KEY}   # or type: header / none
    timeout_seconds: 30
    tools:
      - name: get_customer
        description: Fetch a customer record by id.
        method: GET
        path: /v1/customers/{customer_id}
        params:
          customer_id: {type: string, in: path, description: Customer id}
          include:     {type: string, in: query, required: false}
      - name: create_ticket
        description: Open a support ticket.
        method: POST
        path: /v1/tickets
        params:
          subject:  {type: string, in: body}
          priority: {type: string, in: body, enum: [low, normal, high], required: false}

agents:
  support:
    tools: [get_customer, create_ticket]
```

Endpoint tools inherit the full security policy automatically: HTTPS enforced,
TLS verification, env-var-only credentials, timeouts, response-size caps,
path-injection defense (path params reject `/`, `\`, `..`), and secret
redaction in error messages. They're also covered by permissions, budgets,
guardrails, and the audit trail like any other tool.

## 4. Run

```bash
agentyodha list                                        # show configured agents
agentyodha chat assistant --tools-module examples.tools --verbose
agentyodha chat assistant --approve                    # human-in-the-loop tool approval
```

Or from Python:

```python
from agentyodha import Agent, load_config
import examples.tools

config = load_config("agentyodha.yaml")
agent = config.build_agent("assistant", on_text=print)
result = agent.run("What's 1847 * 392?")
print(result.text, result.tool_calls, result.usage)
```

## 5. Test — assertions + confidence scores

LLM output is non-deterministic, so single pass/fail is weak evidence. Declare cases in YAML; `repeat: N` reruns each case and reports **confidence = pass rate**:

```yaml
tests:
  assistant:
    - name: does_arithmetic_with_tool
      prompt: "What is 1847 * 392? Use your calculator."
      repeat: 3
      expect:
        uses_tool: calculate
        contains: ["724024"]
        max_iterations: 3
    - name: stays_concise
      prompt: "In one sentence, what is Python?"
      expect:
        max_chars: 400
        judge: "The response is a single correct sentence about Python."   # LLM-as-judge
```

```bash
agentyodha test assistant --tools-module examples.tools
```

Available checks: `contains`, `not_contains`, `regex`, `min_chars` / `max_chars`, `uses_tool`, `max_iterations`, `not_refused`, and `judge` (natural-language criterion graded by an LLM).

## 6. Agent Swagger — the interactive playground

Like Swagger UI, but for agents. Explore every configured agent (model, tools, guardrails), send prompts, and inspect the full result — streamed text, tool calls, loop iterations, guardrail verdicts, token usage — then run the test suite with one click.

```bash
agentyodha serve --tools-module examples.tools
# -> http://127.0.0.1:8420/?token=...
```

Zero extra dependencies (stdlib HTTP server). Binds to localhost by default — it's a dev tool, don't expose it publicly.

## 7. Security: the LLM endpoint

The connection between agentyodha and any model endpoint is policy-enforced (`agentyodha/security.py`):

| Policy | Enforcement |
|---|---|
| No secrets in config | Config loading scans for anything credential-shaped (`sk-…`, JWTs, AWS keys, `api_key:` fields) and **refuses to start**. Credentials are referenced by env-var name only (`api_key_env`). |
| HTTPS required | Remote `base_url` must be `https://`. Plain `http://` is allowed only for loopback hosts (local Ollama/vLLM). |
| TLS verification | On by default; `verify_tls: false` is honored **only** for loopback endpoints. |
| Bounded requests | Every outbound call has a connect/read timeout and a response-size cap; error messages are secret-redacted before logging. |
| Playground auth | `agentyodha serve` generates a per-run session token; every API call requires it (Bearer header or `?token=`), so nothing else on the machine can drive your agents or spend your quota. Binds to 127.0.0.1. `--no-auth` to opt out. |

## 8. Security: the agents themselves

Beyond the endpoint, each agent is individually sandboxed (`agentyodha/agent_security.py`), all from YAML:

```yaml
agents:
  assistant:
    permissions:                     # per-tool policy
      default: allow                 # allow | deny | ask
      tools:
        send_email: ask              # requires --approve (denied if no approver)
        delete_records: deny         # never executes, period
    budget:                          # hard limits against runaway loops / abuse
      max_tool_calls_per_run: 10
      max_tokens_per_session: 500000
      max_runs_per_minute: 20
    audit_dir: .agentyodha/audit      # tamper-evident audit trail
```

| Layer | What it does |
|---|---|
| **Permissions** | `allow` / `deny` / `ask` per tool. `deny` never executes; `ask` requires a connected approver (CLI `--approve` or your callback) and is **denied, not silently allowed**, when none exists. |
| **Budgets** | Caps tool calls per run, tokens per session, and runs per minute. A run over budget is blocked with `stop_reason: budget_exceeded`; a loop over its tool budget gets error results telling the model to wrap up. |
| **Input validation** | Tool inputs are untrusted model output — every call is validated against the tool's declared schema (required/unknown parameters) before anything executes. |
| **Audit trail** | Every run, guard verdict, tool decision, and outcome is appended to a JSONL log where each record is SHA-256 hash-chained to the previous one. `agentyodha audit .agentyodha/audit` re-walks the chain and reports any altered, inserted, or reordered record. |

## 9. Security: guardrails

Guards run on input (before the model sees it) and output (before the caller sees it):

| Guard | What it does |
|---|---|
| `prompt_injection` | Heuristic screen for "ignore previous instructions"-style attacks (block/flag) |
| `blocklist` | Your own regex patterns (block/flag) |
| `pii_redact` | Redacts emails, phone numbers, card numbers, SSNs |
| `max_length` | Truncates over-long output |

Guards are a first line of defense — combine with least-privilege tools, `deny`/`ask` permissions, and budgets for anything destructive. Custom guards: subclass `agentyodha.guardrails.Guard`.

## Structured output

```python
from pydantic import BaseModel

class Contact(BaseModel):
    name: str
    email: str

contact = agent.extract("Reach Jane Doe at jane@example.com", Contact)  # validated Contact
```

## Project layout

```
agentyodha/
├── agent.py                     # provider-agnostic agentic loop
├── agent_security.py            # permissions, budgets, hash-chained audit log
├── providers/
│   ├── base.py                  # ModelProvider interface + normalized request/response
│   ├── anthropic_provider.py    # official Anthropic SDK backend
│   └── openai_compat.py         # OpenAI/Ollama/vLLM/Groq/... backend (httpx + SSE)
├── http_tools.py                # declarative REST endpoint tools (your API, zero code)
├── security.py                  # endpoint policy: URLs, TLS, secrets, redaction
├── config.py                    # pydantic models + YAML loading (secret-scanned)
├── tools.py                     # @tool decorator, schema generation, registry
├── guardrails.py                # input/output guards
├── testing.py                   # test harness, assertions, LLM-as-judge, confidence
├── playground.py                # Agent Swagger web UI (token-authenticated)
├── memory.py                    # conversation persistence
└── cli.py                       # init / list / chat / test / serve / audit
```

## Development & contributing

```bash
python tests/test_smoke.py   # offline tests, no API key needed
```

Building your first agent? Follow the step-by-step
**[operational runbook](docs/RUNBOOK.md)** — install → scaffold → secure →
test → playground → ship, with expected results, troubleshooting, and rollback
for every step.

Contributions welcome — see [CONTRIBUTING.md](CONTRIBUTING.md) for the
architecture map and the supported extension points (custom providers, guards,
tools, and test assertions). Security reports: [SECURITY.md](SECURITY.md).
Community standards: [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md).
Release history: [CHANGELOG.md](CHANGELOG.md).

## License & provenance

MIT — see [LICENSE](LICENSE). All source in this repository is original work for
this project; nothing is copied from any other project or organization. The
handful of runtime dependencies (anthropic, pydantic, PyYAML, httpx) are all
MIT/BSD-licensed, are installed by pip, and are not vendored here — see
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

The name **agentyodha** combines *agent* with *yodha* (योद्धा, Sanskrit for
"warrior"). PyPI distribution and import name are both `agentyodha`. This
project was previously developed under the working name "fastagent"; it was
renamed because unrelated packages named `fastagent`/`fast-agent-mcp` already
exist on PyPI, and it is not affiliated with them.

## Releasing

Releases publish to PyPI automatically via
[publish.yml](.github/workflows/publish.yml) using PyPI **Trusted Publishing**
(OIDC — no API tokens stored anywhere):

1. One-time: on pypi.org → *Publishing*, add a pending publisher for project
   `agentyodha` pointing at this repo and `publish.yml`, environment `pypi`.
2. Bump `version` in `pyproject.toml` and update `CHANGELOG.md`.
3. Create a GitHub Release with tag `vX.Y.Z` — CI builds, validates
   (`twine check` + offline tests), and publishes.
