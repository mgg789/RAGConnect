"""Web UI for managing Client Gateway configuration.

Lets the user add/remove/toggle project destinations and update the
local-memory URL — all from a browser, without editing YAML by hand.

Start:
    ragconnect-web
    # or:
    python -m client_gateway.web_server

Environment variables:
    RAGCONNECT_CONFIG_PATH  — path to client_config.yaml
    RAGCONNECT_WEB_HOST     — bind host  (default: 127.0.0.1)
    RAGCONNECT_WEB_PORT     — bind port  (default: 8090)
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

import yaml
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from client_gateway.config import load_config

app = FastAPI(title="RAGConnect Client Web UI")


# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------

def _config_path() -> Path:
    env = os.environ.get("RAGCONNECT_CONFIG_PATH")
    if env:
        return Path(env)
    return Path.home() / ".ragconnect" / "client_config.yaml"


def _read() -> dict:
    path = _config_path()
    if not path.exists():
        return {}
    with open(path) as fh:
        return yaml.safe_load(fh) or {}


def _write(data: dict) -> None:
    path = _config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as fh:
        yaml.dump(data, fh, default_flow_style=False, allow_unicode=True)


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------

class ProjectIn(BaseModel):
    label: str
    url: str
    token: str
    enabled: bool = True


class LocalIn(BaseModel):
    url: str
    enabled: bool = True


# ---------------------------------------------------------------------------
# API routes
# ---------------------------------------------------------------------------

@app.get("/api/config")
async def get_config():
    return load_config(_config_path()).model_dump()


@app.post("/api/projects")
async def add_project(project: ProjectIn):
    data = _read()
    projects: list = data.get("projects", [])
    if any(p["label"] == project.label for p in projects):
        return {"status": "error", "error": f"Label '{project.label}' already exists."}
    projects.append(project.model_dump())
    data["projects"] = projects
    _write(data)
    return {"status": "ok"}


@app.delete("/api/projects/{label}")
async def delete_project(label: str):
    data = _read()
    data["projects"] = [p for p in data.get("projects", []) if p["label"] != label]
    _write(data)
    return {"status": "ok"}


@app.patch("/api/projects/{label}/toggle")
async def toggle_project(label: str):
    data = _read()
    for p in data.get("projects", []):
        if p["label"] == label:
            p["enabled"] = not p.get("enabled", True)
            break
    _write(data)
    return {"status": "ok"}


@app.put("/api/local")
async def update_local(local: LocalIn):
    data = _read()
    data["local_memory"] = local.model_dump()
    _write(data)
    return {"status": "ok"}


class DefaultProjectIn(BaseModel):
    label: Optional[str] = None  # None means "route to local"


@app.put("/api/default-project")
async def set_default_project(body: DefaultProjectIn):
    data = _read()
    if body.label:
        # Verify the label actually exists
        projects = data.get("projects", [])
        if not any(p["label"] == body.label for p in projects):
            return {"status": "error", "error": f"Label '{body.label}' not found."}
        data["default_project"] = body.label
    else:
        data.pop("default_project", None)
    _write(data)
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# Web UI — single-page HTML (no external dependencies)
# ---------------------------------------------------------------------------

_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>RAGConnect</title>
<style>
  :root {
    --bg:      #f5f6f8;
    --surface: #ffffff;
    --border:  #e0e3e8;
    --accent:  #3b6ff5;
    --accent2: #2d58d6;
    --danger:  #e0423a;
    --success: #1a8a55;
    --muted:   #6b7280;
    --text:    #1a1d23;
    --code-bg: #f0f2f5;
  }
  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    background: var(--bg); color: var(--text); font-size: 15px; line-height: 1.5;
  }
  a { color: var(--accent); text-decoration: none; }

  /* ---- layout ---- */
  header {
    background: var(--surface); border-bottom: 1px solid var(--border);
    padding: 0 2rem; height: 56px; display: flex; align-items: center; gap: 12px;
  }
  header .logo { font-weight: 700; font-size: 1.1rem; letter-spacing: -.3px; }
  header .sub  { color: var(--muted); font-size: 0.85rem; }
  main { max-width: 900px; margin: 2rem auto; padding: 0 1.25rem; }

  /* ---- cards ---- */
  .card {
    background: var(--surface); border: 1px solid var(--border);
    border-radius: 10px; margin-bottom: 1.5rem; overflow: hidden;
  }
  .card-head {
    padding: .875rem 1.5rem; border-bottom: 1px solid var(--border);
    font-weight: 600; font-size: .9375rem;
    display: flex; justify-content: space-between; align-items: center;
  }
  .card-body { padding: 1.5rem; }

  /* ---- table ---- */
  table { width: 100%; border-collapse: collapse; }
  th, td { padding: .5625rem .75rem; text-align: left; }
  th {
    font-size: .75rem; font-weight: 600; text-transform: uppercase;
    letter-spacing: .05em; color: var(--muted);
    border-bottom: 1px solid var(--border);
  }
  tr:not(:last-child) td { border-bottom: 1px solid var(--border); }
  td.label { font-weight: 600; }
  td.url   { font-size: .8125rem; color: var(--muted); max-width: 240px;
              overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  code {
    font-family: "SF Mono", ui-monospace, monospace;
    font-size: .8rem; background: var(--code-bg);
    padding: .1em .45em; border-radius: 4px;
  }

  /* ---- badges ---- */
  .badge {
    display: inline-block; padding: .175em .55em; border-radius: 5px;
    font-size: .72rem; font-weight: 600; letter-spacing: .02em;
  }
  .badge-on  { background: #d1fadf; color: var(--success); }
  .badge-off { background: #f0f2f5; color: var(--muted); }

  /* ---- buttons ---- */
  .btn {
    padding: .4rem .875rem; border: none; border-radius: 7px;
    font-size: .875rem; font-weight: 500; cursor: pointer; transition: .12s;
    white-space: nowrap;
  }
  .btn-primary { background: var(--accent); color: #fff; }
  .btn-primary:hover { background: var(--accent2); }
  .btn-ghost { background: transparent; border: 1px solid var(--border); color: var(--text); }
  .btn-ghost:hover { background: var(--bg); }
  .btn-danger-ghost { background: transparent; border: 1px solid #fca5a5; color: var(--danger); }
  .btn-danger-ghost:hover { background: var(--danger); color: #fff; border-color: var(--danger); }
  .btn-sm { padding: .25rem .6rem; font-size: .8125rem; }
  .actions { display: flex; gap: .4rem; }

  /* ---- form ---- */
  .divider { height: 1px; background: var(--border); margin: 1.25rem 0; }
  .form-row { display: flex; gap: .75rem; flex-wrap: wrap; align-items: flex-end; }
  .fg { display: flex; flex-direction: column; gap: .25rem; flex: 1; min-width: 130px; }
  .fg.lg { min-width: 220px; }
  .fg label { font-size: .78rem; font-weight: 500; color: var(--muted); }
  .fg input, .fg select {
    padding: .46rem .75rem; border: 1px solid var(--border); border-radius: 7px;
    font-size: .9375rem; width: 100%; background: var(--surface); color: var(--text);
    transition: border-color .15s;
  }
  .fg input:focus, .fg select:focus {
    outline: none; border-color: var(--accent);
    box-shadow: 0 0 0 3px rgba(59,111,245,.15);
  }

  /* ---- alert ---- */
  .alert {
    padding: .7rem 1rem; border-radius: 8px; margin-bottom: 1.25rem;
    font-size: .9rem; display: none; align-items: center; gap: .6rem;
  }
  .alert.show { display: flex; }
  .alert-ok  { background: #d1fadf; color: #065f35; border: 1px solid #a7f3c0; }
  .alert-err { background: #fee2e2; color: #7f1d1d; border: 1px solid #fca5a5; }

  .empty-row td { color: var(--muted); text-align: center; padding: 2rem; }
</style>
</head>
<body>

<header>
  <span class="logo">RAGConnect</span>
  <span class="sub">Client Gateway Configuration</span>
</header>

<main>

  <div id="alert" class="alert" role="alert"></div>

  <!-- -------- Project destinations -------- -->
  <div class="card">
    <div class="card-head">
      <span>Project Destinations</span>
      <span style="font-size:.8125rem;font-weight:400;color:var(--muted)" id="dest-count"></span>
    </div>
    <div class="card-body" style="padding:0">
      <table>
        <thead>
          <tr>
            <th>Label</th>
            <th>Server URL</th>
            <th>Token</th>
            <th>Status</th>
            <th style="width:140px"></th>
          </tr>
        </thead>
        <tbody id="projects-tbody">
          <tr class="empty-row"><td colspan="5">Loading…</td></tr>
        </tbody>
      </table>
    </div>

    <div style="padding:1.25rem 1.5rem;border-top:1px solid var(--border)">
      <p style="font-size:.8125rem;font-weight:600;color:var(--muted);margin-bottom:.75rem;text-transform:uppercase;letter-spacing:.05em">Add Destination</p>
      <form id="add-form">
        <div class="form-row">
          <div class="fg">
            <label>Label</label>
            <input name="label" placeholder="kettle" required autocomplete="off">
          </div>
          <div class="fg lg">
            <label>Server URL</label>
            <input name="url" placeholder="https://kettle-memory.example.com" required>
          </div>
          <div class="fg lg">
            <label>Access Token</label>
            <input name="token" placeholder="tok_…" type="password" required autocomplete="new-password">
          </div>
          <button type="submit" class="btn btn-primary">Add</button>
        </div>
      </form>
    </div>
  </div>

  <!-- -------- Default project -------- -->
  <div class="card">
    <div class="card-head">
      <span>Default Project</span>
      <span style="font-size:.8125rem;font-weight:400;color:var(--muted)">used when no project_label is specified</span>
    </div>
    <div class="card-body">
      <p style="font-size:.875rem;color:var(--muted);margin-bottom:1rem">
        The AI uses this project for memory operations when it does not pass an explicit
        <code>project_label</code>. Set it to the project you are currently working in.
        Leave as <em>None</em> to fall back to local memory by default.
      </p>
      <div class="form-row" style="align-items:center">
        <div class="fg" style="max-width:260px">
          <label>Default destination</label>
          <select id="default-project-select">
            <option value="">— None (use local memory) —</option>
          </select>
        </div>
        <button type="button" class="btn btn-primary" onclick="saveDefaultProject()">Save</button>
      </div>
    </div>
  </div>

  <!-- -------- Local memory -------- -->
  <div class="card">
    <div class="card-head">Local Memory (fallback)</div>
    <div class="card-body">
      <form id="local-form">
        <div class="form-row">
          <div class="fg lg">
            <label>LightRAG URL</label>
            <input id="local-url" name="url" placeholder="http://127.0.0.1:9621" required>
          </div>
          <div class="fg" style="min-width:120px;max-width:150px">
            <label>Enabled</label>
            <select id="local-enabled" name="enabled">
              <option value="true">Yes</option>
              <option value="false">No</option>
            </select>
          </div>
          <button type="submit" class="btn btn-primary">Save</button>
        </div>
      </form>
    </div>
  </div>

</main>

<script>
const $ = id => document.getElementById(id);

// ---- alert ----
function flash(msg, type) {
  const el = $('alert');
  el.textContent = msg;
  el.className = `alert show alert-${type === 'ok' ? 'ok' : 'err'}`;
  clearTimeout(el._t);
  el._t = setTimeout(() => { el.classList.remove('show'); }, 4000);
}

// ---- mask token ----
function mask(t) {
  return t.length > 10 ? t.slice(0, 6) + '\u2022\u2022\u2022\u2022' + t.slice(-4) : '\u2022'.repeat(8);
}

// ---- render table ----
function renderProjects(list) {
  const tbody = $('projects-tbody');
  $('dest-count').textContent = list.length ? `${list.length} configured` : '';
  if (!list.length) {
    tbody.innerHTML = '<tr class="empty-row"><td colspan="5">No destinations yet. Add one below.</td></tr>';
    return;
  }
  tbody.innerHTML = list.map(p => `
    <tr>
      <td class="label">${esc(p.label)}</td>
      <td class="url" title="${esc(p.url)}">${esc(p.url)}</td>
      <td><code>${esc(mask(p.token))}</code></td>
      <td><span class="badge ${p.enabled ? 'badge-on' : 'badge-off'}">${p.enabled ? 'enabled' : 'disabled'}</span></td>
      <td>
        <div class="actions">
          <button class="btn btn-sm btn-ghost"
            onclick="toggle(${JSON.stringify(p.label)})">${p.enabled ? 'Disable' : 'Enable'}</button>
          <button class="btn btn-sm btn-danger-ghost"
            onclick="remove(${JSON.stringify(p.label)})">Remove</button>
        </div>
      </td>
    </tr>`).join('');
}

function esc(s) {
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

// ---- load config ----
async function load() {
  try {
    const r = await fetch('/api/config');
    const cfg = await r.json();
    renderProjects(cfg.projects || []);
    $('local-url').value = cfg.local_memory?.url || 'http://127.0.0.1:9621';
    $('local-enabled').value = String(cfg.local_memory?.enabled !== false);
    // Populate default project dropdown
    const sel = $('default-project-select');
    const cur = cfg.default_project || '';
    sel.innerHTML = '<option value="">— None (use local memory) —</option>' +
      (cfg.projects || []).filter(p => p.enabled).map(p =>
        `<option value="${esc(p.label)}"${p.label === cur ? ' selected' : ''}>${esc(p.label)}</option>`
      ).join('');
  } catch(e) {
    flash('Could not load config: ' + e.message, 'err');
  }
}

// ---- default project ----
async function saveDefaultProject() {
  const label = $('default-project-select').value || null;
  const r = await fetch('/api/default-project', {
    method: 'PUT', headers: {'Content-Type':'application/json'},
    body: JSON.stringify({ label })
  });
  const d = await r.json();
  if (d.status === 'ok') flash(label ? `Default project set to "${label}".` : 'Default project cleared (local memory).', 'ok');
  else flash(d.error || 'Failed to save.', 'err');
}

// ---- add ----
$('add-form').addEventListener('submit', async e => {
  e.preventDefault();
  const fd = new FormData(e.target);
  const body = { label: fd.get('label'), url: fd.get('url'), token: fd.get('token'), enabled: true };
  const r = await fetch('/api/projects', {
    method: 'POST', headers: {'Content-Type':'application/json'},
    body: JSON.stringify(body)
  });
  const d = await r.json();
  if (d.status === 'ok') { e.target.reset(); load(); flash('Destination added.', 'ok'); }
  else flash(d.error || 'Failed to add.', 'err');
});

// ---- toggle ----
async function toggle(label) {
  const r = await fetch(`/api/projects/${encodeURIComponent(label)}/toggle`, { method: 'PATCH' });
  const d = await r.json();
  if (d.status === 'ok') load();
  else flash(d.error || 'Failed to toggle.', 'err');
}

// ---- remove ----
async function remove(label) {
  if (!confirm(`Remove destination "${label}"?`)) return;
  const r = await fetch(`/api/projects/${encodeURIComponent(label)}`, { method: 'DELETE' });
  const d = await r.json();
  if (d.status === 'ok') { load(); flash(`"${label}" removed.`, 'ok'); }
  else flash(d.error || 'Failed to remove.', 'err');
}

// ---- local memory ----
$('local-form').addEventListener('submit', async e => {
  e.preventDefault();
  const fd = new FormData(e.target);
  const r = await fetch('/api/local', {
    method: 'PUT', headers: {'Content-Type':'application/json'},
    body: JSON.stringify({ url: fd.get('url'), enabled: fd.get('enabled') === 'true' })
  });
  const d = await r.json();
  if (d.status === 'ok') flash('Local memory settings saved.', 'ok');
  else flash(d.error || 'Failed to save.', 'err');
});

load();
</script>
</body>
</html>"""


@app.get("/", response_class=HTMLResponse)
async def index():
    return _HTML


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main_sync() -> None:
    import uvicorn

    host = os.environ.get("RAGCONNECT_WEB_HOST", "127.0.0.1")
    port = int(os.environ.get("RAGCONNECT_WEB_PORT", "8090"))
    print(f"RAGConnect Web UI →  http://{host}:{port}")
    uvicorn.run(app, host=host, port=port, log_level="warning")


if __name__ == "__main__":
    main_sync()
