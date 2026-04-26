from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

import yaml
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from client_gateway.audit import read_recent_activity
from client_gateway.config import ClientConfig, DestinationConfig, ProjectContextConfig, load_config, save_config
from client_gateway.context import normalize_repo_root
from client_gateway.local_service import LocalServiceManager
from client_gateway.project_registry import register_project, remove_project_context, write_memory_snippet
from shared.dotenv import read_dotenv, update_dotenv
from shared.ops_log import mask_secret, tail_text
from shared.runtime import get_local_log_dir

app = FastAPI(title="RAGConnect Client Web UI")


def _config_path() -> Path:
    env = os.environ.get("RAGCONNECT_CONFIG_PATH")
    return Path(env) if env else Path.home() / ".ragconnect" / "client_config.yaml"


def _local_env_path() -> Path:
    env = os.environ.get("RAGCONNECT_ENV_PATH")
    return Path(env) if env else Path.home() / ".ragconnect" / ".env"


def _manager() -> LocalServiceManager:
    return LocalServiceManager(repo_root=os.environ.get("RAGCONNECT_REPO_ROOT"), rag_home=os.environ.get("RAGCONNECT_HOME"))


def _read() -> ClientConfig:
    return load_config(_config_path())


def _write(config: ClientConfig) -> None:
    save_config(config, _config_path())


class DestinationIn(BaseModel):
    url: str
    label: Optional[str] = None
    token: Optional[str] = None
    display_name: Optional[str] = None


class DefaultProjectIn(BaseModel):
    label: Optional[str] = None


class SettingsIn(BaseModel):
    remote_only_mode: bool = False
    strict_project_routing: bool = True


class LocalRuntimeIn(BaseModel):
    openai_api_base: Optional[str] = None
    openai_api_key: Optional[str] = None
    llm_model: Optional[str] = None
    local_embedding_mode: Optional[str] = None
    local_embedding_model: Optional[str] = None
    local_embedding_dim: Optional[str] = None
    embedding_model: Optional[str] = None
    embedding_dim: Optional[str] = None


class ProjectContextIn(BaseModel):
    repo_root: str
    project_label: str
    write_agents_md: bool = False
    write_claude_md: bool = False


@app.get("/api/config")
async def get_config():
    return _read().model_dump()


@app.get("/api/routing")
async def get_routing():
    config = _read()
    return {
        "status": "ok",
        "default_project": config.default_project,
        "remote_only_mode": config.remote_only_mode,
        "strict_project_routing": config.strict_project_routing,
    }


@app.put("/api/routing")
async def set_routing(body: SettingsIn):
    config = _read()
    config.remote_only_mode = body.remote_only_mode
    config.strict_project_routing = body.strict_project_routing
    _write(config)
    return {"status": "ok"}


@app.put("/api/default-project")
async def set_default_project(body: DefaultProjectIn):
    config = _read()
    if body.label and not any(d.label == body.label for d in config.destinations if not d.is_local):
        raise HTTPException(status_code=400, detail=f"Project '{body.label}' is not configured.")
    config.default_project = body.label
    _write(config)
    return {"status": "ok"}


@app.post("/api/destinations")
async def add_destination(dest: DestinationIn):
    config = _read()
    label = dest.label or None
    if label and not dest.token:
        raise HTTPException(status_code=400, detail="Token is required for project destinations.")
    if label is None:
        config.destinations = [d for d in config.destinations if not d.is_local]
        config.destinations.insert(0, DestinationConfig(url=dest.url, enabled=True, display_name=dest.display_name))
    else:
        if any(d.label == label for d in config.destinations):
            raise HTTPException(status_code=400, detail=f"Label '{label}' already exists.")
        config.destinations.append(
            DestinationConfig(url=dest.url, label=label, token=dest.token, enabled=True, display_name=dest.display_name)
        )
    _write(config)
    return {"status": "ok"}


@app.delete("/api/destinations/{identifier}")
async def delete_destination(identifier: str):
    config = _read()
    if identifier == "local":
        config.destinations = [d for d in config.destinations if not d.is_local]
    else:
        config.destinations = [d for d in config.destinations if d.label != identifier]
        if config.default_project == identifier:
            config.default_project = None
    _write(config)
    return {"status": "ok"}


@app.patch("/api/destinations/{identifier}/toggle")
async def toggle_destination(identifier: str):
    config = _read()
    for destination in config.destinations:
        if (identifier == "local" and destination.is_local) or destination.label == identifier:
            destination.enabled = not destination.enabled
            break
    _write(config)
    return {"status": "ok"}


@app.get("/api/project-contexts")
async def get_project_contexts():
    config = _read()
    return {"status": "ok", "project_contexts": [context.model_dump() for context in config.project_contexts]}


@app.post("/api/project-contexts")
async def upsert_project_context(body: ProjectContextIn):
    config = _read()
    result = register_project(
        config=config,
        repo_root=body.repo_root,
        project_label=body.project_label,
        config_path=_config_path(),
        write_agents=body.write_agents_md,
        write_claude=body.write_claude_md,
    )
    return {"status": "ok", **result}


@app.delete("/api/project-contexts")
async def delete_project_context(repo_root: str = Query(...)):
    config = _read()
    if not remove_project_context(config, repo_root):
        raise HTTPException(status_code=404, detail="Project context not found.")
    _write(config)
    return {"status": "ok"}


@app.post("/api/project-contexts/snippet")
async def write_project_snippet(body: ProjectContextIn):
    target_root = Path(body.repo_root)
    targets: list[str] = []
    if body.write_agents_md:
        write_memory_snippet(target_root / "AGENTS.md", body.project_label)
        targets.append("AGENTS.md")
    if body.write_claude_md:
        write_memory_snippet(target_root / "CLAUDE.md", body.project_label)
        targets.append("CLAUDE.md")
    return {"status": "ok", "targets": targets}


@app.get("/api/local-runtime")
async def get_local_runtime():
    env_values = read_dotenv(_local_env_path())
    return {
        "status": "ok",
        "env_path": str(_local_env_path()),
        "openai_api_base": env_values.get("OPENAI_API_BASE", ""),
        "has_openai_api_key": bool(env_values.get("OPENAI_API_KEY")),
        "masked_openai_api_key": mask_secret(env_values.get("OPENAI_API_KEY", "")),
        "llm_model": env_values.get("LLM_MODEL", ""),
        "local_embedding_mode": env_values.get("LOCAL_EMBEDDING_MODE", ""),
        "local_embedding_model": env_values.get("LOCAL_EMBEDDING_MODEL", ""),
        "local_embedding_dim": env_values.get("LOCAL_EMBEDDING_DIM", ""),
        "embedding_model": env_values.get("EMBEDDING_MODEL", ""),
        "embedding_dim": env_values.get("EMBEDDING_DIM", ""),
        "data_dir": str(_manager().data_dir),
    }


@app.put("/api/local-runtime")
async def set_local_runtime(body: LocalRuntimeIn):
    updates: dict[str, str | None] = {
        "OPENAI_API_BASE": (body.openai_api_base or "").strip() or None,
        "LLM_MODEL": (body.llm_model or "").strip() or None,
        "LOCAL_EMBEDDING_MODE": (body.local_embedding_mode or "").strip() or None,
        "LOCAL_EMBEDDING_MODEL": (body.local_embedding_model or "").strip() or None,
        "LOCAL_EMBEDDING_DIM": (body.local_embedding_dim or "").strip() or None,
        "EMBEDDING_MODEL": (body.embedding_model or "").strip() or None,
        "EMBEDDING_DIM": (body.embedding_dim or "").strip() or None,
    }
    incoming_key = (body.openai_api_key or "").strip()
    if incoming_key:
        updates["OPENAI_API_KEY"] = incoming_key
    update_dotenv(_local_env_path(), updates)
    return await get_local_runtime()


@app.get("/api/service/status")
async def service_status():
    manager = _manager()
    config = _read()
    state = manager.health_snapshot()
    web_component = state["components"].get("web")
    if web_component and web_component.get("running"):
        web_component["healthy"] = True
        if state["components"].get("lightrag", {}).get("healthy"):
            state["status"] = "ok"
    warnings: list[str] = []
    if config.default_project and not any(d.label == config.default_project and d.enabled for d in config.destinations):
        warnings.append("default_project points to a missing or disabled destination.")
    if state["components"]["lightrag"]["port_conflict"]:
        warnings.append("Port 9621 is occupied by a process not owned by the supervisor.")
    if state["components"]["proxy"]["port_conflict"]:
        warnings.append("Port 9622 is occupied by a process not owned by the supervisor.")
    if state["components"]["web"]["port_conflict"]:
        warnings.append("Port 8090 is occupied by a process not owned by the supervisor.")
    activity = read_recent_activity(limit=1)
    return {"status": "ok", "service": state, "warnings": warnings, "last_activity": activity[0] if activity else None}


@app.get("/api/doctor")
async def doctor():
    return {"status": "ok", "doctor": _manager().doctor()}


@app.get("/api/activity")
async def activity(limit: int = Query(default=50, ge=1, le=200)):
    return {"status": "ok", "items": read_recent_activity(limit=limit)}


@app.get("/api/logs")
async def logs(component: str = Query(default="lightrag"), lines: int = Query(default=100, ge=10, le=500)):
    path_map = {
        "proxy": get_local_log_dir() / "proxy.stdout.log",
        "proxy_err": get_local_log_dir() / "proxy.stderr.log",
        "lightrag": get_local_log_dir() / "lightrag.stdout.log",
        "lightrag_err": get_local_log_dir() / "lightrag.stderr.log",
        "web": get_local_log_dir() / "web.stdout.log",
        "web_err": get_local_log_dir() / "web.stderr.log",
        "audit": get_local_log_dir() / "client_audit.jsonl",
        "runtime": get_local_log_dir() / "client_runtime.jsonl",
        "health": get_local_log_dir() / "client_health.jsonl",
        "supervisor": get_local_log_dir() / "supervisor.stdout.log",
        "supervisor_err": get_local_log_dir() / "supervisor.stderr.log",
    }
    path = path_map.get(component)
    if not path:
        raise HTTPException(status_code=400, detail="Unknown log component.")
    return {"status": "ok", "component": component, "lines": tail_text(path, limit=lines)}


@app.get("/health")
async def health():
    return {"status": "ok"}


_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta http-equiv="Cache-Control" content="no-cache, no-store, must-revalidate">
<title>RAGConnect Local</title>
<style>
  :root { --bg:#0f1117; --surface:#181c26; --muted:#93a0b3; --text:#edf2f7; --border:#2a3344; --accent:#3b82f6; --ok:#10b981; --warn:#f59e0b; --err:#ef4444; }
  * { box-sizing:border-box; }
  body { margin:0; font:14px/1.45 "Segoe UI",system-ui,sans-serif; background:var(--bg); color:var(--text); }
  header { padding:16px 24px; border-bottom:1px solid var(--border); display:flex; justify-content:space-between; align-items:center; gap:16px; }
  main { max-width:1200px; margin:0 auto; padding:24px; display:grid; gap:16px; }
  .grid { display:grid; grid-template-columns:repeat(auto-fit, minmax(320px, 1fr)); gap:16px; }
  .card { background:var(--surface); border:1px solid var(--border); border-radius:14px; padding:16px; min-width:0; }
  h1,h2,h3,p { margin:0; }
  h1 { font-size:20px; }
  h2 { font-size:15px; margin-bottom:6px; }
  .muted { color:var(--muted); }
  .section-note { color:var(--muted); margin-bottom:14px; }
  .badge { display:inline-flex; align-items:center; max-width:100%; padding:2px 8px; border-radius:999px; font-size:12px; overflow-wrap:anywhere; word-break:break-word; }
  .ok { background:rgba(16,185,129,.15); color:#86efac; }
  .warn { background:rgba(245,158,11,.15); color:#fcd34d; }
  .err { background:rgba(239,68,68,.15); color:#fca5a5; }
  table { width:100%; border-collapse:collapse; table-layout:fixed; }
  th,td { padding:8px 6px; border-bottom:1px solid rgba(255,255,255,.05); text-align:left; vertical-align:top; overflow-wrap:anywhere; word-break:break-word; }
  input,select,textarea,button { width:100%; min-width:0; background:#0f172a; color:var(--text); border:1px solid var(--border); border-radius:10px; padding:10px 12px; }
  button { cursor:pointer; background:var(--accent); border-color:var(--accent); font-weight:600; }
  .row { display:grid; grid-template-columns:repeat(2, minmax(0, 1fr)); gap:10px; margin-bottom:10px; align-items:start; }
  .row-3 { display:grid; grid-template-columns:repeat(3, minmax(0, 1fr)); gap:10px; margin-bottom:10px; align-items:start; }
  .summary-list { display:grid; gap:10px; margin-bottom:14px; }
  .summary-item { display:grid; gap:6px; min-width:0; }
  .summary-label { color:var(--muted); font-size:12px; text-transform:uppercase; letter-spacing:.04em; }
  code { display:inline-flex; align-items:center; max-width:100%; background:#0b1220; border:1px solid rgba(255,255,255,.06); border-radius:10px; padding:8px; overflow-wrap:anywhere; word-break:break-word; white-space:pre-wrap; }
  pre { background:#0b1220; border:1px solid rgba(255,255,255,.06); border-radius:10px; padding:12px; overflow:auto; white-space:pre-wrap; }
  ul { margin:12px 0 0 18px; color:var(--muted); }
  .status-line { margin-top:10px; color:var(--muted); font-size:12px; }
  @media (max-width: 820px) {
    .row, .row-3 { grid-template-columns:1fr; }
  }
</style>
</head>
<body>
<header>
  <div><h1>RAGConnect Local</h1><p class="muted">Routing, project contexts, runtime, health and logs.</p></div>
  <button style="width:auto" onclick="loadAll()">Refresh</button>
</header>
<main>
  <div class="grid">
    <section class="card">
      <h2>Service Health</h2>
      <p class="section-note">Supervisor view of the local stack. Components may briefly show as starting while they warm up.</p>
      <div id="service-health" class="muted">Loading...</div>
    </section>
    <section class="card">
      <h2>Routing</h2>
      <p class="section-note">Controls where unlabeled memory requests go and how strict project routing should be.</p>
      <div id="routing-view" class="muted">Loading...</div>
      <div class="row">
        <input id="routing-default" placeholder="default_project label">
        <select id="routing-remote">
          <option value="false">Local fallback enabled</option>
          <option value="true">Remote only mode</option>
        </select>
      </div>
      <div class="row">
        <select id="routing-strict">
          <option value="true">Strict routing enabled</option>
          <option value="false">Fallback when project is unavailable</option>
        </select>
        <button onclick="saveRouting()">Save routing</button>
      </div>
    </section>
    <section class="card">
      <h2>Local Runtime</h2>
      <p class="section-note">Current local LLM and embedding runtime. Long values are shown in wrapped blocks below.</p>
      <div id="runtime-view" class="muted">Loading...</div>
      <div class="row">
        <input id="runtime-base" placeholder="OPENAI_API_BASE">
        <input id="runtime-model" placeholder="LLM_MODEL">
      </div>
      <div class="row">
        <input id="runtime-key" placeholder="OPENAI_API_KEY (optional)">
        <button onclick="saveRuntime()">Save runtime</button>
      </div>
    </section>
  </div>
  <div class="grid">
    <section class="card">
      <h2>Destinations</h2>
      <p class="section-note">Configured memory endpoints. Leave label empty to replace the local destination entry.</p>
      <div class="row-3">
        <input id="dest-label" placeholder="project label or empty">
        <input id="dest-url" placeholder="destination URL">
        <input id="dest-token" placeholder="token for project memory">
      </div>
      <button onclick="addDestination()">Add destination</button>
      <div id="destinations" style="margin-top:12px"></div>
    </section>
    <section class="card">
      <h2>Project Contexts</h2>
      <p class="section-note">Registers a repository path to a project label for automatic memory routing. Optional flags write the memory snippet into project docs.</p>
      <div class="row">
        <input id="ctx-root" placeholder="repo_root">
        <input id="ctx-label" placeholder="project_label">
      </div>
      <div class="row-3">
        <select id="ctx-agents">
          <option value="false">Write AGENTS.md: no</option>
          <option value="true">Write AGENTS.md: yes</option>
        </select>
        <select id="ctx-claude">
          <option value="false">Write CLAUDE.md: no</option>
          <option value="true">Write CLAUDE.md: yes</option>
        </select>
        <button onclick="saveProjectContext()">Register project</button>
      </div>
      <div id="project-contexts" style="margin-top:12px"></div>
    </section>
  </div>
  <section class="card">
    <h2>Recent Activity</h2>
    <p class="section-note">Last memory routing and audit events recorded by the local gateway.</p>
    <div id="activity" class="muted">Loading...</div>
  </section>
  <section class="card">
    <h2>Logs</h2>
    <p class="section-note">Inspect stdout or audit logs for the local components.</p>
    <div class="row">
      <select id="log-component">
        <option value="lightrag">lightrag</option>
        <option value="proxy">proxy</option>
        <option value="web">web</option>
        <option value="audit">audit</option>
        <option value="supervisor">supervisor</option>
      </select>
      <button onclick="loadLogs()">Load logs</button>
    </div>
    <pre id="logs">No logs loaded.</pre>
  </section>
</main>
<script>
async function j(url, options) { const r = await fetch(url, options); const d = await r.json(); if (!r.ok) throw new Error(d.detail || d.error || JSON.stringify(d)); return d; }
function esc(v) { return String(v ?? '').replace(/[&<>"]/g, s => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[s])); }
function badge(ok, label) { const cls = ok === 'ok' ? 'ok' : ok === 'warning' || ok === 'degraded' ? 'warn' : ok === 'disabled' ? 'warn' : 'err'; return `<span class="badge ${cls}">${esc(label || ok)}</span>`; }
function summaryItem(label, value) { return `<div class="summary-item"><div class="summary-label">${esc(label)}</div><code>${esc(value)}</code></div>`; }
function componentState(item) {
  if (item.healthy) return { kind:'ok', label:'healthy' };
  if (item.running) return { kind:'warning', label:'starting' };
  if (item.port_conflict) return { kind:'error', label:'conflict' };
  return { kind:'error', label:'down' };
}
async function loadAll() {
  await Promise.all([loadConfig(), loadRouting(), loadRuntime(), loadHealth(), loadContexts(), loadActivity()]);
  await loadLogs();
}
async function loadConfig() {
  const cfg = await j('/api/config');
  const rows = (cfg.destinations || []).map(d => `<tr><td>${esc(d.label || 'local')}</td><td>${esc(d.url)}</td><td>${badge(d.enabled ? 'ok':'error', d.enabled ? 'enabled':'disabled')}</td></tr>`).join('');
  document.getElementById('destinations').innerHTML = `<table><thead><tr><th>Label</th><th>URL</th><th>Status</th></tr></thead><tbody>${rows || '<tr><td colspan="3" class="muted">No destinations</td></tr>'}</tbody></table>`;
}
async function loadRouting() {
  const data = await j('/api/routing');
  document.getElementById('routing-default').value = data.default_project || '';
  document.getElementById('routing-remote').value = String(data.remote_only_mode);
  document.getElementById('routing-strict').value = String(data.strict_project_routing);
  document.getElementById('routing-view').innerHTML = `<div class="summary-list">${summaryItem('Default project', data.default_project || 'local fallback')}${summaryItem('Remote only mode', data.remote_only_mode ? 'enabled' : 'disabled')}${summaryItem('Strict project routing', data.strict_project_routing ? 'enabled' : 'disabled')}</div>`;
}
async function loadRuntime() {
  const data = await j('/api/local-runtime');
  document.getElementById('runtime-base').value = data.openai_api_base || '';
  document.getElementById('runtime-model').value = data.llm_model || '';
  document.getElementById('runtime-key').value = '';
  document.getElementById('runtime-view').innerHTML = `<div class="summary-list">${summaryItem('OpenAI API base', data.openai_api_base || 'not set')}${summaryItem('LLM model', data.llm_model || 'not set')}${summaryItem('Embedding model', data.local_embedding_model || data.embedding_model || 'n/a')}${summaryItem('Embedding dim', data.local_embedding_dim || data.embedding_dim || 'n/a')}${summaryItem('API key', data.masked_openai_api_key || 'not set')}</div>`;
}
async function loadHealth() {
  const data = await j('/api/service/status');
  const comps = Object.entries(data.service.components).map(([name, item]) => {
    const state = componentState(item);
    return `<tr><td>${esc(name)}</td><td>${badge(state.kind, state.label)}</td><td>${esc(item.pid || '-')}</td><td>${esc(item.port)}</td><td>${esc(item.restart_count || 0)}</td></tr>`;
  }).join('');
  const warns = (data.warnings || []).map(w => `<li>${esc(w)}</li>`).join('');
  document.getElementById('service-health').innerHTML = `<table><thead><tr><th>Component</th><th>Status</th><th>PID</th><th>Port</th><th>Restarts</th></tr></thead><tbody>${comps}</tbody></table>${warns ? `<ul>${warns}</ul>` : ''}<div class="status-line">Overall status: ${esc(data.service.status || 'unknown')}</div>`;
}
async function loadContexts() {
  const data = await j('/api/project-contexts');
  const rows = (data.project_contexts || []).map(item => `<tr><td>${esc(item.project_label)}</td><td>${esc(item.repo_root)}</td><td>${badge(item.enabled ? 'ok':'error', item.enabled ? 'enabled':'disabled')}</td></tr>`).join('');
  document.getElementById('project-contexts').innerHTML = `<table><thead><tr><th>Label</th><th>Repo Root</th><th>Status</th></tr></thead><tbody>${rows || '<tr><td colspan="3" class="muted">No project contexts registered yet</td></tr>'}</tbody></table>`;
}
async function loadActivity() {
  const data = await j('/api/activity?limit=10');
  document.getElementById('activity').innerHTML = (data.items || []).length ? `<pre>${esc(JSON.stringify(data.items || [], null, 2))}</pre>` : `<div class="muted">No memory activity has been recorded yet.</div>`;
}
async function loadLogs() {
  const component = document.getElementById('log-component').value;
  const data = await j('/api/logs?component=' + encodeURIComponent(component) + '&lines=100');
  document.getElementById('logs').textContent = (data.lines || []).join('\\n') || 'No log lines.';
}
async function saveRouting() {
  const defaultProject = document.getElementById('routing-default').value.trim() || null;
  await j('/api/routing', { method:'PUT', headers:{'Content-Type':'application/json'}, body:JSON.stringify({ remote_only_mode: document.getElementById('routing-remote').value === 'true', strict_project_routing: document.getElementById('routing-strict').value === 'true' }) });
  await j('/api/default-project', { method:'PUT', headers:{'Content-Type':'application/json'}, body:JSON.stringify({ label: defaultProject }) });
  await loadRouting();
  await loadHealth();
}
async function saveRuntime() {
  await j('/api/local-runtime', { method:'PUT', headers:{'Content-Type':'application/json'}, body:JSON.stringify({ openai_api_base: document.getElementById('runtime-base').value, llm_model: document.getElementById('runtime-model').value, openai_api_key: document.getElementById('runtime-key').value }) });
  await loadRuntime();
}
async function addDestination() {
  await j('/api/destinations', { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({ label: document.getElementById('dest-label').value || null, url: document.getElementById('dest-url').value, token: document.getElementById('dest-token').value || null }) });
  document.getElementById('dest-label').value=''; document.getElementById('dest-url').value=''; document.getElementById('dest-token').value='';
  await loadConfig();
}
async function saveProjectContext() {
  await j('/api/project-contexts', { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({ repo_root: document.getElementById('ctx-root').value, project_label: document.getElementById('ctx-label').value, write_agents_md: document.getElementById('ctx-agents').value === 'true', write_claude_md: document.getElementById('ctx-claude').value === 'true' }) });
  document.getElementById('ctx-root').value=''; document.getElementById('ctx-label').value='';
  await loadContexts();
}
setInterval(() => { loadHealth().catch(() => {}); loadActivity().catch(() => {}); }, 15000);
loadAll();
</script>
</body>
</html>"""


@app.get("/", response_class=HTMLResponse)
async def index():
    return _HTML


def main_sync() -> None:
    import uvicorn

    host = os.environ.get("RAGCONNECT_WEB_HOST", "127.0.0.1")
    port = int(os.environ.get("RAGCONNECT_WEB_PORT", "8090"))
    allow_remote = os.environ.get("RAGCONNECT_ALLOW_REMOTE_WEB", "false").lower() == "true"
    if host not in {"127.0.0.1", "localhost"} and not allow_remote:
        raise RuntimeError("Remote bind is disabled by default. Set RAGCONNECT_ALLOW_REMOTE_WEB=true to override.")
    uvicorn.run(app, host=host, port=port, log_level="warning")


if __name__ == "__main__":
    main_sync()
