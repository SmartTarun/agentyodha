"""Endpoint security: hardening the connection between agentyodha and any LLM API.

Enforced policies:
- **No secrets in config files.** Providers reference credentials by environment
  variable name (`api_key_env`), never by value. Config loading scans for
  anything that looks like a real key and refuses to start if it finds one.
- **HTTPS required for remote endpoints.** Plain `http://` is allowed only for
  loopback hosts (localhost / 127.0.0.1 / ::1) — e.g. a local Ollama or vLLM.
- **TLS verification on by default.** `verify_tls: false` is only honored for
  loopback endpoints; a remote endpoint with verification disabled is refused.
- **Bounded requests.** Every outbound call gets a timeout and a response-size
  cap so a misbehaving endpoint can't hang or flood the agent.
"""

from __future__ import annotations

import os
import re
from typing import Any, Optional
from urllib.parse import urlparse


class EndpointSecurityError(Exception):
    """Raised when a provider endpoint or credential violates security policy."""


_LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1", "[::1]", "0.0.0.0"}

# Common API-credential shapes (Anthropic, OpenAI, Google, AWS, generic JWT/hex)
_SECRET_PATTERNS = [
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}"),
    re.compile(r"\bsk-ant-[A-Za-z0-9_-]{10,}"),
    re.compile(r"\bAIza[0-9A-Za-z_-]{30,}"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{10,}"),  # JWT
    re.compile(r"\bgsk_[A-Za-z0-9]{20,}"),                        # Groq
]

_SECRETISH_KEYS = {"api_key", "apikey", "token", "secret", "password", "authorization"}


def is_loopback(host: Optional[str]) -> bool:
    return (host or "").lower() in _LOOPBACK_HOSTS


def validate_base_url(url: str) -> str:
    """Allow https:// anywhere; http:// only for loopback hosts."""
    parsed = urlparse(url)
    if parsed.scheme == "https":
        return url
    if parsed.scheme == "http":
        if is_loopback(parsed.hostname):
            return url
        raise EndpointSecurityError(
            f"Refusing plain-HTTP remote endpoint {url!r}. Use https://, or a "
            "loopback address for local model servers (e.g. http://127.0.0.1:11434/v1)."
        )
    raise EndpointSecurityError(f"Unsupported URL scheme in {url!r} (use https:// or local http://).")


def validate_tls(url: str, verify_tls: bool) -> None:
    """Disabling TLS verification is only acceptable for loopback endpoints."""
    if verify_tls:
        return
    if not is_loopback(urlparse(url).hostname):
        raise EndpointSecurityError(
            f"verify_tls: false is not allowed for remote endpoint {url!r}. "
            "TLS verification may only be disabled for loopback addresses."
        )


def looks_like_secret(value: str) -> bool:
    return any(p.search(value) for p in _SECRET_PATTERNS)


def assert_no_inline_secrets(node: Any, path: str = "config") -> None:
    """Recursively scan config data and refuse anything that embeds a credential."""
    if isinstance(node, dict):
        for key, value in node.items():
            key_path = f"{path}.{key}"
            if str(key).lower() in _SECRETISH_KEYS and isinstance(value, str) and value:
                raise EndpointSecurityError(
                    f"Inline credential at {key_path!r}. Never put secrets in config "
                    f"files — set an environment variable and reference it via "
                    f"'api_key_env: MY_PROVIDER_KEY'."
                )
            assert_no_inline_secrets(value, key_path)
    elif isinstance(node, list):
        for index, item in enumerate(node):
            assert_no_inline_secrets(item, f"{path}[{index}]")
    elif isinstance(node, str) and looks_like_secret(node):
        raise EndpointSecurityError(
            f"Value at {path!r} looks like an API credential. Never put secrets in "
            f"config files — use 'api_key_env' with an environment variable instead."
        )


def resolve_api_key(env_name: Optional[str]) -> Optional[str]:
    """Read a credential from the environment. Returns None when no env var is named
    (fine for local servers that need no auth)."""
    if not env_name:
        return None
    value = os.environ.get(env_name)
    if not value:
        raise EndpointSecurityError(
            f"Environment variable {env_name!r} is not set. Export it before "
            "starting the agent; agentyodha never reads keys from config files."
        )
    return value


def redact(text: str) -> str:
    """Mask anything secret-shaped before it reaches logs or error messages."""
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub("[REDACTED_KEY]", text)
    return text
