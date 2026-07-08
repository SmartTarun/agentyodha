"""Declarative HTTP endpoint tools: connect an agent to YOUR API with zero code.

Most teams point agents at their own services. Instead of writing a Python
function per route, declare the endpoint once in agentyodha.yaml and each
operation becomes a registered tool an agent can use by name:

    endpoints:
      crm:
        base_url: https://api.mycompany.com
        auth:
          type: bearer                 # bearer | header | none
          api_key_env: CRM_API_KEY     # env-var NAME — never the key itself
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

Every endpoint inherits the framework's security policy: HTTPS required
off-loopback, TLS verification enforced, credentials from the environment
only, per-request timeouts, response-size caps, path-injection defense, and
secret redaction in errors.
"""

from __future__ import annotations

import json
import re
import threading
from typing import Any, Literal, Optional
from urllib.parse import quote

import httpx
from pydantic import BaseModel, Field

from agentyodha.security import (
    EndpointSecurityError,
    redact,
    resolve_api_key,
    validate_base_url,
    validate_tls,
)
from agentyodha.tools import Tool, registry

MAX_RESPONSE_CHARS_DEFAULT = 50_000
_ALLOWED_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE"}
_PLACEHOLDER = re.compile(r"\{(\w+)\}")

_JSON_TYPES = {"string", "integer", "number", "boolean"}


class EndpointAuth(BaseModel):
    type: Literal["bearer", "header", "none"] = "none"
    api_key_env: Optional[str] = None    # env-var NAME; never the key itself
    header: str = "X-API-Key"            # used when type == "header"


class EndpointParam(BaseModel):
    type: str = "string"
    description: str = ""
    location: Literal["path", "query", "body"] = Field(default="query", alias="in")
    required: bool = True
    enum: Optional[list[Any]] = None

    model_config = {"populate_by_name": True}


class EndpointToolSpec(BaseModel):
    name: str
    description: str
    method: str = "GET"
    path: str
    params: dict[str, EndpointParam] = Field(default_factory=dict)


class EndpointConfig(BaseModel):
    base_url: str
    auth: EndpointAuth = Field(default_factory=EndpointAuth)
    verify_tls: bool = True
    timeout_seconds: float = 30.0
    max_response_chars: int = MAX_RESPONSE_CHARS_DEFAULT
    extra_headers: dict[str, str] = Field(default_factory=dict)
    tools: list[EndpointToolSpec] = Field(default_factory=list)


def _validate_spec(endpoint_name: str, endpoint: EndpointConfig) -> None:
    validate_base_url(endpoint.base_url)
    validate_tls(endpoint.base_url, endpoint.verify_tls)
    for spec in endpoint.tools:
        method = spec.method.upper()
        if method not in _ALLOWED_METHODS:
            raise ValueError(
                f"endpoint {endpoint_name!r} tool {spec.name!r}: method {spec.method!r} "
                f"not allowed (use one of {sorted(_ALLOWED_METHODS)})."
            )
        placeholders = set(_PLACEHOLDER.findall(spec.path))
        path_params = {n for n, p in spec.params.items() if p.location == "path"}
        if placeholders != path_params:
            raise ValueError(
                f"endpoint {endpoint_name!r} tool {spec.name!r}: path placeholders "
                f"{sorted(placeholders)} must exactly match params declared with 'in: path' "
                f"({sorted(path_params)})."
            )
        for name, param in spec.params.items():
            if param.type not in _JSON_TYPES:
                raise ValueError(
                    f"endpoint {endpoint_name!r} tool {spec.name!r} param {name!r}: "
                    f"type {param.type!r} unsupported (use {sorted(_JSON_TYPES)})."
                )
            if param.location == "path" and not param.required:
                raise ValueError(
                    f"endpoint {endpoint_name!r} tool {spec.name!r} param {name!r}: "
                    "path parameters must be required."
                )


def _input_schema(spec: EndpointToolSpec) -> dict[str, Any]:
    properties: dict[str, Any] = {}
    required: list[str] = []
    for name, param in spec.params.items():
        prop: dict[str, Any] = {"type": param.type}
        if param.description:
            prop["description"] = param.description
        if param.enum:
            prop["enum"] = param.enum
        properties[name] = prop
        if param.required:
            required.append(name)
    return {"type": "object", "properties": properties, "required": required}


class _LazyClient:
    """Creates the httpx client (and reads the API key) on first use, not at load."""

    def __init__(self, endpoint: EndpointConfig):
        self.endpoint = endpoint
        self._client: Optional[httpx.Client] = None
        self._lock = threading.Lock()

    def get(self) -> httpx.Client:
        with self._lock:
            if self._client is None:
                endpoint = self.endpoint
                headers = dict(endpoint.extra_headers)
                api_key = resolve_api_key(endpoint.auth.api_key_env)
                if endpoint.auth.type == "bearer":
                    if not api_key:
                        raise EndpointSecurityError("bearer auth requires api_key_env.")
                    headers["Authorization"] = f"Bearer {api_key}"
                elif endpoint.auth.type == "header":
                    if not api_key:
                        raise EndpointSecurityError("header auth requires api_key_env.")
                    headers[endpoint.auth.header] = api_key
                self._client = httpx.Client(
                    base_url=endpoint.base_url.rstrip("/"),
                    headers=headers,
                    verify=endpoint.verify_tls,
                    timeout=httpx.Timeout(endpoint.timeout_seconds, connect=10.0),
                )
            return self._client


def _make_runner(lazy: _LazyClient, endpoint: EndpointConfig, spec: EndpointToolSpec):
    method = spec.method.upper()

    def run(**kwargs: Any) -> str:
        path = spec.path
        query: dict[str, Any] = {}
        body: dict[str, Any] = {}
        for name, param in spec.params.items():
            if name not in kwargs:
                continue
            value = kwargs[name]
            if param.location == "path":
                text = str(value)
                # Path values are untrusted model output: no separators/traversal.
                if "/" in text or "\\" in text or ".." in text:
                    raise ValueError(f"Illegal characters in path parameter {name!r}.")
                path = path.replace("{" + name + "}", quote(text, safe=""))
            elif param.location == "query":
                query[name] = value
            else:
                body[name] = value

        response = lazy.get().request(
            method, path, params=query or None, json=body or None
        )
        if response.status_code >= 400:
            raise RuntimeError(
                f"HTTP {response.status_code} from {spec.name}: "
                + redact(response.text[:500])
            )
        text = response.text
        if len(text) > endpoint.max_response_chars:
            text = text[: endpoint.max_response_chars] + "\n[response truncated]"
        return text

    return run


def build_endpoint_tools(endpoints_raw: dict[str, dict[str, Any]]) -> list[Tool]:
    """Parse the `endpoints:` config section into registered, callable Tools."""
    built: list[Tool] = []
    for endpoint_name, raw in (endpoints_raw or {}).items():
        endpoint = EndpointConfig(**(raw or {}))
        _validate_spec(endpoint_name, endpoint)
        lazy = _LazyClient(endpoint)
        for spec in endpoint.tools:
            tool = Tool(
                _make_runner(lazy, endpoint, spec),
                name=spec.name,
                description=spec.description,
                input_schema=_input_schema(spec),
            )
            registry.register(tool)
            built.append(tool)
    return built
