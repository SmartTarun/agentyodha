"""Agent Swagger: a local web playground to explore, invoke, and test agents.

Like Swagger UI for HTTP APIs — every configured agent is listed with its
model, tools, and guardrails; you can send prompts, inspect tool calls,
token usage, and guardrail verdicts, and run the agent's test suite, all
from the browser.

    agentyodha serve --tools-module examples.tools
    # -> http://127.0.0.1:8420

Binds to 127.0.0.1 by default; this is a developer tool, not a production
gateway — do not expose it to the internet.
"""

from __future__ import annotations

import dataclasses
import hmac
import json
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Optional
from urllib.parse import parse_qs, urlparse

from agentyodha.agent import Agent
from agentyodha.config import FrameworkConfig
from agentyodha.testing import AgentTester, TestCase

MAX_BODY_BYTES = 1 * 1024 * 1024  # 1 MB request cap


class PlaygroundState:
    """Holds config plus live Agent instances keyed by (agent, session)."""

    def __init__(self, config: FrameworkConfig):
        self.config = config
        self._agents: dict[tuple[str, str], Agent] = {}
        self._lock = threading.Lock()

    def agent(self, name: str, session: str) -> Agent:
        key = (name, session)
        with self._lock:
            if key not in self._agents:
                self._agents[key] = self.config.build_agent(name, session_id=session)
            return self._agents[key]

    def reset(self, name: str, session: str) -> None:
        with self._lock:
            agent = self._agents.pop((name, session), None)
        if agent:
            agent.reset()

    def describe_agents(self) -> list[dict[str, Any]]:
        out = []
        for name, cfg in self.config.agents.items():
            rails = cfg.guardrails
            out.append({
                "name": name,
                "provider": cfg.provider,
                "model": cfg.model,
                "effort": cfg.effort,
                "thinking": cfg.thinking,
                "max_tokens": cfg.max_tokens,
                "system": cfg.system or "",
                "tools": cfg.tools,
                "guardrails": {
                    "input": [g.get("type") for g in (rails.input if rails else [])],
                    "output": [g.get("type") for g in (rails.output if rails else [])],
                },
                "tests": len(self.config.tests.get(name, [])),
            })
        return out


def _json_default(obj: Any) -> Any:
    if dataclasses.is_dataclass(obj):
        return dataclasses.asdict(obj)
    return str(obj)


class PlaygroundHandler(BaseHTTPRequestHandler):
    state: PlaygroundState        # injected by serve()
    auth_token: Optional[str] = None  # None = auth disabled

    # -- plumbing -------------------------------------------------------- #

    def log_message(self, fmt: str, *args: Any) -> None:  # quieter default logging
        pass

    def _authorized(self) -> bool:
        """API endpoints require the session token (header or ?token= query)."""
        if self.auth_token is None:
            return True
        header = self.headers.get("Authorization", "")
        if header.startswith("Bearer ") and hmac.compare_digest(header[7:], self.auth_token):
            return True
        query_token = parse_qs(urlparse(self.path).query).get("token", [""])[0]
        return hmac.compare_digest(query_token, self.auth_token)

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, payload: Any, status: int = 200) -> None:
        body = json.dumps(payload, default=_json_default).encode("utf-8")
        self._send(status, body, "application/json; charset=utf-8")

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", 0))
        if length > MAX_BODY_BYTES:
            raise ValueError("Request body exceeds the 1 MB limit.")
        return json.loads(self.rfile.read(length) or b"{}")

    # -- routes ---------------------------------------------------------- #

    def do_GET(self) -> None:
        route = urlparse(self.path).path
        if route in ("/", "/index.html"):
            self._send(200, PAGE.encode("utf-8"), "text/html; charset=utf-8")
        elif route == "/api/agents":
            if not self._authorized():
                self._send_json({"error": "unauthorized — missing or invalid token"}, 401)
                return
            self._send_json(self.state.describe_agents())
        else:
            self._send_json({"error": "not found"}, 404)

    def do_POST(self) -> None:
        if not self._authorized():
            self._send_json({"error": "unauthorized — missing or invalid token"}, 401)
            return
        try:
            payload = self._read_json()
            route = urlparse(self.path).path
            if route == "/api/run":
                self._send_json(self._run(payload))
            elif route == "/api/test":
                self._send_json(self._test(payload))
            elif route == "/api/reset":
                self.state.reset(payload["agent"], payload.get("session", "playground"))
                self._send_json({"ok": True})
            else:
                self._send_json({"error": "not found"}, 404)
        except Exception as exc:
            self._send_json({"error": f"{type(exc).__name__}: {exc}"}, 500)

    def _run(self, payload: dict[str, Any]) -> dict[str, Any]:
        agent = self.state.agent(payload["agent"], payload.get("session", "playground"))
        result = agent.run(payload["message"])
        return {
            "text": result.text,
            "stop_reason": result.stop_reason,
            "refused": result.refused,
            "blocked": result.blocked,
            "iterations": result.iterations,
            "tool_calls": result.tool_calls,
            "usage": result.usage,
            "guards": [dataclasses.asdict(g) for g in result.guard_results],
        }

    def _test(self, payload: dict[str, Any]) -> dict[str, Any]:
        name = payload["agent"]
        raw_cases = self.state.config.tests.get(name, [])
        if not raw_cases:
            return {"agent": name, "cases": [], "summary": "No tests configured for this agent."}
        cases = [TestCase(**c) for c in raw_cases]
        tester = AgentTester(lambda: self.state.config.build_agent(name, session_id="__test__"))
        report = tester.run_suite(cases)
        return {
            "agent": name,
            "passed": report.passed,
            "summary": report.summary(),
            "cases": [
                {
                    "name": c.name,
                    "passed": c.passed,
                    "confidence": c.confidence,
                    "runs": c.runs,
                    "passes": c.passes,
                    "error": c.error,
                }
                for c in report.cases
            ],
        }


def serve(
    config: FrameworkConfig,
    host: str = "127.0.0.1",
    port: int = 8420,
    auth_token: Optional[str] = None,
    require_auth: bool = True,
) -> None:
    """Start the playground server (blocking).

    A per-run session token is generated (unless supplied) and required on every
    API call — so nothing else on the machine can drive your agents or burn
    your LLM quota through this port.
    """
    PlaygroundHandler.state = PlaygroundState(config)
    PlaygroundHandler.auth_token = (auth_token or secrets.token_urlsafe(24)) if require_auth else None

    server = ThreadingHTTPServer((host, port), PlaygroundHandler)
    if PlaygroundHandler.auth_token:
        print(f"Agent Swagger running at http://{host}:{port}/?token={PlaygroundHandler.auth_token}")
        print("API calls require this token (Bearer header or ?token=). Ctrl+C to stop.")
    else:
        print(f"Agent Swagger running at http://{host}:{port}  — AUTH DISABLED (--no-auth)")
    if host not in ("127.0.0.1", "localhost", "::1"):
        print("WARNING: binding beyond localhost — this is a dev tool, do not expose it publicly.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        server.server_close()


PAGE = """<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>agentyodha — Agent Swagger</title>
<style>
  :root { --bg:#0f1117; --panel:#181b24; --border:#2a2f3d; --text:#e6e9f0; --dim:#8b93a7;
          --accent:#6ea8fe; --ok:#4ade80; --bad:#f87171; --warn:#fbbf24; }
  * { box-sizing:border-box; }
  body { margin:0; font:14px/1.5 ui-sans-serif,system-ui,sans-serif; background:var(--bg); color:var(--text); }
  header { padding:14px 22px; border-bottom:1px solid var(--border); display:flex; align-items:baseline; gap:12px; }
  header h1 { font-size:17px; margin:0; }
  header span { color:var(--dim); font-size:12px; }
  main { display:grid; grid-template-columns:320px 1fr; gap:0; height:calc(100vh - 51px); }
  #sidebar { border-right:1px solid var(--border); overflow-y:auto; padding:14px; }
  .agent-card { background:var(--panel); border:1px solid var(--border); border-radius:8px;
                padding:12px; margin-bottom:10px; cursor:pointer; }
  .agent-card.active { border-color:var(--accent); }
  .agent-card h3 { margin:0 0 6px; font-size:14px; }
  .agent-card .meta { color:var(--dim); font-size:12px; }
  .chip { display:inline-block; background:#232838; border-radius:10px; padding:1px 8px;
          font-size:11px; margin:2px 3px 0 0; color:var(--accent); }
  #panel { display:flex; flex-direction:column; overflow:hidden; }
  #chat { flex:1; overflow-y:auto; padding:18px 22px; }
  .msg { max-width:78%; padding:10px 14px; border-radius:10px; margin-bottom:10px; white-space:pre-wrap; }
  .msg.user { background:#20304d; margin-left:auto; }
  .msg.agent { background:var(--panel); border:1px solid var(--border); }
  .detail { font-size:12px; color:var(--dim); margin:-4px 0 12px 4px; }
  .detail b.ok { color:var(--ok); } .detail b.bad { color:var(--bad); } .detail b.warn { color:var(--warn); }
  #composer { display:flex; gap:8px; padding:14px 22px; border-top:1px solid var(--border); }
  #composer input { flex:1; background:var(--panel); border:1px solid var(--border); color:var(--text);
                    border-radius:8px; padding:10px 14px; font-size:14px; }
  button { background:var(--accent); color:#0b1020; border:0; border-radius:8px; padding:10px 16px;
           font-weight:600; cursor:pointer; }
  button.ghost { background:transparent; color:var(--dim); border:1px solid var(--border); }
  #testreport { padding:0 22px 14px; }
  pre { background:var(--panel); border:1px solid var(--border); border-radius:8px; padding:12px;
        overflow-x:auto; font-size:12px; }
  .spin { color:var(--dim); font-style:italic; }
</style>
</head>
<body>
<header><h1>agentyodha</h1><span>Agent Swagger — explore, invoke, and test your agents</span></header>
<main>
  <div id="sidebar"></div>
  <div id="panel">
    <div id="chat"></div>
    <div id="testreport"></div>
    <div id="composer">
      <input id="prompt" placeholder="Send a message to the selected agent…"
             onkeydown="if(event.key==='Enter')send()">
      <button onclick="send()">Send</button>
      <button class="ghost" onclick="runTests()">Run tests</button>
      <button class="ghost" onclick="resetSession()">Reset</button>
    </div>
  </div>
</main>
<script>
let agents = [], current = null;
const $ = id => document.getElementById(id);

// Session token: taken from the URL printed at server start, kept for the tab.
const TOKEN = new URLSearchParams(location.search).get('token')
  || sessionStorage.getItem('agentyodha_token') || '';
if (TOKEN) sessionStorage.setItem('agentyodha_token', TOKEN);
const authHeaders = TOKEN ? {'Authorization': 'Bearer ' + TOKEN} : {};

async function api(path, options = {}) {
  options.headers = Object.assign({}, options.headers || {}, authHeaders);
  const response = await fetch(path, options);
  if (response.status === 401) {
    $('sidebar').innerHTML = '<div class="agent-card">Unauthorized. Open the playground ' +
      'using the tokened URL printed in the terminal.</div>';
    throw new Error('unauthorized');
  }
  return response.json();
}

async function load() {
  agents = await api('/api/agents');
  const sb = $('sidebar'); sb.innerHTML = '';
  agents.forEach(a => {
    const el = document.createElement('div');
    el.className = 'agent-card' + (current === a.name ? ' active' : '');
    el.onclick = () => { current = a.name; $('chat').innerHTML=''; $('testreport').innerHTML=''; load(); };
    const rails = [...a.guardrails.input.map(g=>'in:'+g), ...a.guardrails.output.map(g=>'out:'+g)];
    el.innerHTML = `<h3>${a.name}</h3>
      <div class="meta">${a.provider} · ${a.model} · effort=${a.effort} · ${a.tests} test(s)</div>
      <div>${a.tools.map(t=>`<span class="chip">🔧 ${t}</span>`).join('')}
           ${rails.map(g=>`<span class="chip">🛡 ${g}</span>`).join('')}</div>`;
    sb.appendChild(el);
  });
  if (!current && agents.length) { current = agents[0].name; load(); }
}

function bubble(cls, text) {
  const el = document.createElement('div');
  el.className = 'msg ' + cls; el.textContent = text;
  $('chat').appendChild(el); $('chat').scrollTop = 1e9;
  return el;
}

async function send() {
  const input = $('prompt'); const text = input.value.trim();
  if (!text || !current) return;
  input.value = ''; bubble('user', text);
  const pending = bubble('agent', '…thinking'); pending.classList.add('spin');
  try {
    const r = await api('/api/run', {method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({agent: current, message: text})});
    pending.classList.remove('spin');
    if (r.error) { pending.textContent = 'Error: ' + r.error; return; }
    pending.textContent = r.text || '(empty response)';
    const d = document.createElement('div'); d.className = 'detail';
    const guards = (r.guards||[]).filter(g=>!g.passed)
      .map(g=>`<b class="warn">🛡 ${g.guard}:${g.action}</b>`).join(' ');
    const tools = (r.tool_calls||[]).map(t=>`🔧 ${t.name}`).join(' ');
    const usage = r.usage ? `in=${r.usage.input_tokens||0} out=${r.usage.output_tokens||0} cached=${r.usage.cache_read_input_tokens||0}` : '';
    d.innerHTML = `stop=<b class="${r.refused||r.blocked?'bad':'ok'}">${r.stop_reason}</b>` +
      ` · loops=${r.iterations} ${tools?'· '+tools:''} ${guards?'· '+guards:''} · ${usage}`;
    $('chat').appendChild(d); $('chat').scrollTop = 1e9;
  } catch (e) { pending.textContent = 'Request failed: ' + e; }
}

async function runTests() {
  if (!current) return;
  $('testreport').innerHTML = '<pre class="spin">Running test suite…</pre>';
  const r = await api('/api/test', {method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({agent: current})});
  $('testreport').innerHTML = '<pre>' + (r.summary || r.error || 'no output') + '</pre>';
}

async function resetSession() {
  if (!current) return;
  await api('/api/reset', {method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({agent: current})});
  $('chat').innerHTML = ''; $('testreport').innerHTML = '';
}

load();
</script>
</body>
</html>
"""
