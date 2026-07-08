# Changelog

All notable changes to agentyodha are documented here.
Format: [Keep a Changelog](https://keepachangelog.com/) style; versions follow [SemVer](https://semver.org/).

## [0.1.0] — 2026-07-07

Initial public release. (Developed under the working name "fastagent";
released as **agentyodha** — *yodha* is Sanskrit for "warrior" — since the
fastagent name is taken on PyPI.)

### Added
- **Core**: provider-agnostic agentic loop — streaming, tool use with parallel
  results, `pause_turn`/`refusal`/`max_tokens` handling, conversation memory,
  structured output extraction into Pydantic models.
- **Providers**: Anthropic (official SDK: adaptive thinking, effort, prompt
  caching) and OpenAI-compatible (OpenAI, Ollama, vLLM, LM Studio, Groq,
  Together, Mistral, ... via one `base_url`).
- **Tools**: `@tool` decorator (schema from type hints + docstrings) and
  zero-code declarative REST endpoint tools (`endpoints:` in YAML).
- **Configuration**: YAML + Pydantic; shared defaults, per-agent overrides.
- **Testing**: declarative test suites with assertions, repeat-runs for
  confidence scores, and LLM-as-judge criteria.
- **Agent Swagger playground**: stdlib web UI to explore, invoke, and test
  agents (token-authenticated, localhost by default).
- **Endpoint security**: env-var-only credentials with config secret-scanning,
  HTTPS/TLS enforcement, timeouts, size caps, secret redaction.
- **Agent security**: per-tool allow/deny/ask permissions, run/session/rate
  budgets, tool-input schema validation, hash-chained tamper-evident audit log.
- **CLI**: `init`, `list`, `chat`, `test`, `serve`, `audit`.
