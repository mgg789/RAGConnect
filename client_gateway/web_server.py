"""Web UI for managing Client Gateway configuration.

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

from client_gateway.config import ClientConfig, DestinationConfig, load_config

app = FastAPI(title="RAGConnect Client Web UI")


# ---------------------------------------------------------------------------
# Config I/O
# ---------------------------------------------------------------------------

def _config_path() -> Path:
    env = os.environ.get("RAGCONNECT_CONFIG_PATH")
    return Path(env) if env else Path.home() / ".ragconnect" / "client_config.yaml"


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

class DestinationIn(BaseModel):
    url: str
    label: Optional[str] = None   # absent → local LightRAG
    token: Optional[str] = None   # absent → native API
    display_name: Optional[str] = None
    prefer_for_search: bool = False
    allow_local_search_augmentation: bool = False


class DefaultProjectIn(BaseModel):
    label: Optional[str] = None   # None → clear default


class SettingsIn(BaseModel):
    remote_only_mode: bool = False
    strict_project_routing: bool = True


# ---------------------------------------------------------------------------
# API routes
# ---------------------------------------------------------------------------

@app.get("/api/config")
async def get_config():
    return load_config(_config_path()).model_dump()


@app.post("/api/destinations")
async def add_destination(dest: DestinationIn):
    label = dest.label or None  # normalise empty string to None

    # Validate: project destinations need a token
    if label and not dest.token:
        return {"status": "error", "error": "Token is required for project destinations."}

    data = _read()
    destinations: list = data.get("destinations", [])

    if label is None:
        # Local LightRAG — only one allowed; replace if it exists
        destinations = [d for d in destinations if d.get("label")]  # drop existing local
        destinations.insert(0, {"url": dest.url, "enabled": True})
    else:
        if any(d.get("label") == label for d in destinations):
            return {"status": "error", "error": f"Label '{label}' already exists."}
        destinations.append({
            "url": dest.url,
            "label": label,
            "token": dest.token,
            "enabled": True,
            "display_name": dest.display_name,
            "prefer_for_search": dest.prefer_for_search,
            "allow_local_search_augmentation": dest.allow_local_search_augmentation,
        })

    data["destinations"] = destinations
    _write(data)
    return {"status": "ok"}


@app.delete("/api/destinations/{identifier}")
async def delete_destination(identifier: str):
    """identifier = 'local' for the local LightRAG, or a project label."""
    data = _read()
    if identifier == "local":
        data["destinations"] = [
            d for d in data.get("destinations", []) if d.get("label")
        ]
        # Clear default_project if it pointed to local (not applicable but safe)
    else:
        data["destinations"] = [
            d for d in data.get("destinations", [])
            if d.get("label") != identifier
        ]
        # Clear default_project if it was pointing to the removed label
        if data.get("default_project") == identifier:
            data.pop("default_project", None)

    _write(data)
    return {"status": "ok"}


@app.patch("/api/destinations/{identifier}/toggle")
async def toggle_destination(identifier: str):
    data = _read()
    for d in data.get("destinations", []):
        is_target = (identifier == "local" and not d.get("label")) or \
                    (d.get("label") == identifier)
        if is_target:
            d["enabled"] = not d.get("enabled", True)
            break
    _write(data)
    return {"status": "ok"}


@app.put("/api/default-project")
async def set_default_project(body: DefaultProjectIn):
    data = _read()
    if body.label:
        destinations = data.get("destinations", [])
        if not any(d.get("label") == body.label for d in destinations):
            return {"status": "error", "error": f"Label '{body.label}' not found."}
        data["default_project"] = body.label
    else:
        data.pop("default_project", None)
    _write(data)
    return {"status": "ok"}


@app.put("/api/settings")
async def set_settings(body: SettingsIn):
    data = _read()
    data["remote_only_mode"] = bool(body.remote_only_mode)
    data["strict_project_routing"] = bool(body.strict_project_routing)
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
    --local:   #0891b2;
    --muted:   #6b7280;
    --text:    #1a1d23;
    --code-bg: #f0f2f5;
  }
  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    background: var(--bg); color: var(--text); font-size: 15px; line-height: 1.5;
  }

  header {
    background: var(--surface); border-bottom: 1px solid var(--border);
    padding: 0 2rem; height: 56px; display: flex; align-items: center; gap: 12px;
  }
  header .logo { font-weight: 700; font-size: 1.1rem; letter-spacing: -.3px; }
  header .sub  { color: var(--muted); font-size: 0.85rem; }
  main { max-width: 960px; margin: 2rem auto; padding: 0 1.25rem; }

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

  table { width: 100%; border-collapse: collapse; }
  th, td { padding: .5625rem .75rem; text-align: left; }
  th {
    font-size: .75rem; font-weight: 600; text-transform: uppercase;
    letter-spacing: .05em; color: var(--muted); border-bottom: 1px solid var(--border);
  }
  tr:not(:last-child) td { border-bottom: 1px solid var(--border); }
  td.url { font-size: .8125rem; color: var(--muted); max-width: 220px;
           overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  code {
    font-family: "SF Mono", ui-monospace, monospace; font-size: .8rem;
    background: var(--code-bg); padding: .1em .45em; border-radius: 4px;
  }

  .badge {
    display: inline-block; padding: .175em .55em; border-radius: 5px;
    font-size: .72rem; font-weight: 600; letter-spacing: .02em;
  }
  .badge-local   { background: #cffafe; color: var(--local); }
  .badge-project { background: #ede9fe; color: #6d28d9; }
  .badge-on      { background: #d1fadf; color: var(--success); }
  .badge-off     { background: #f0f2f5; color: var(--muted); }
  .badge-default { background: #fef3c7; color: #92400e; }

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

  .divider { height: 1px; background: var(--border); margin: 1.25rem 0; }
  .form-section { margin-bottom: 1.25rem; }
  .form-section-title {
    font-size: .78rem; font-weight: 600; color: var(--muted);
    text-transform: uppercase; letter-spacing: .05em; margin-bottom: .625rem;
  }
  .form-row { display: flex; gap: .75rem; flex-wrap: wrap; align-items: flex-end; }
  .fg { display: flex; flex-direction: column; gap: .25rem; flex: 1; min-width: 120px; }
  .fg.lg { min-width: 200px; }
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
  .fg input.optional { border-style: dashed; }

  .alert {
    padding: .7rem 1rem; border-radius: 8px; margin-bottom: 1.25rem;
    font-size: .9rem; display: none; align-items: center; gap: .6rem;
  }
  .alert.show { display: flex; }
  .alert-ok  { background: #d1fadf; color: #065f35; border: 1px solid #a7f3c0; }
  .alert-err { background: #fee2e2; color: #7f1d1d; border: 1px solid #fca5a5; }

  .hint { font-size: .8125rem; color: var(--muted); margin-top: .375rem; }
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

  <!-- ==================== Destinations ==================== -->
  <div class="card">
    <div class="card-head">
      <span>Memory Destinations</span>
      <span id="dest-count" style="font-size:.8125rem;font-weight:400;color:var(--muted)"></span>
    </div>

    <!-- table -->
    <div style="padding:0">
      <table>
        <thead>
          <tr>
            <th>Type</th>
            <th>Label</th>
            <th>URL</th>
            <th>Token</th>
            <th>Status</th>
            <th style="width:160px"></th>
          </tr>
        </thead>
        <tbody id="dest-tbody">
          <tr class="empty-row"><td colspan="6">Loading…</td></tr>
        </tbody>
      </table>
    </div>

    <!-- add form -->
    <div style="padding:1.25rem 1.5rem;border-top:1px solid var(--border)">
      <div class="form-section">
        <div class="form-section-title">Add Local LightRAG</div>
        <p class="hint" style="margin-bottom:.75rem">
          Requests go directly to LightRAG's native API — no label, no token needed.
          This becomes the default destination when no <code>project_label</code> is given.
        </p>
        <form id="add-local-form">
          <div class="form-row">
            <div class="fg lg">
              <label>LightRAG URL</label>
              <input name="url" placeholder="http://127.0.0.1:9621" required>
            </div>
            <button type="submit" class="btn btn-primary">Set Local LightRAG</button>
          </div>
        </form>
      </div>

      <div class="divider"></div>

      <div class="form-section">
        <div class="form-section-title">Add Project Destination</div>
        <p class="hint" style="margin-bottom:.75rem">
          Requests are proxied through the project's Server Gateway with Bearer-token auth.
        </p>
        <form id="add-project-form">
          <div class="form-row">
            <div class="fg">
              <label>Label</label>
              <input name="label" placeholder="kettle" required autocomplete="off">
            </div>
            <div class="fg lg">
              <label>Server Gateway URL</label>
              <input name="url" placeholder="https://kettle-memory.example.com" required>
            </div>
            <div class="fg lg">
              <label>Access Token</label>
              <input name="token" placeholder="tok_…" type="password" required autocomplete="new-password">
            </div>
            <button type="submit" class="btn btn-primary">Add Project</button>
          </div>
        </form>
      </div>
    </div>
  </div>

  <!-- ==================== Default Project ==================== -->
  <div class="card">
    <div class="card-head">
      <span>Default Project</span>
      <span style="font-size:.8125rem;font-weight:400;color:var(--muted)">when no project_label is specified</span>
    </div>
    <div class="card-body">
      <p class="hint" style="margin-bottom:1rem">
        When the AI calls a memory tool without an explicit <code>project_label</code>,
        it routes here. Set this to the project you are currently working in.
        Leave as <em>None</em> to fall back to local LightRAG by default.
      </p>
      <div class="form-row" style="align-items:center">
        <div class="fg" style="max-width:280px">
          <label>Default destination</label>
          <select id="default-project-select">
            <option value="">— None (use local LightRAG) —</option>
          </select>
        </div>
        <button type="button" class="btn btn-primary" onclick="saveDefaultProject()">Save</button>
      </div>
    </div>
  </div>

</main>

<script>
const $ = id => document.getElementById(id);

function flash(msg, type) {
  const el = $('alert');
  el.textContent = msg;
  el.className = `alert show alert-${type === 'ok' ? 'ok' : 'err'}`;
  clearTimeout(el._t);
  el._t = setTimeout(() => el.classList.remove('show'), 4500);
}

function esc(s) {
  return String(s)
    .replace(/&/g,'&amp;').replace(/</g,'&lt;')
    .replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

function mask(t) {
  return t && t.length > 10
    ? t.slice(0, 6) + '\u2022\u2022\u2022\u2022' + t.slice(-4)
    : '\u2022'.repeat(8);
}

// ---- render destinations table ----
function renderDestinations(dests, defaultProject) {
  const tbody = $('dest-tbody');
  const count = dests.length;
  $('dest-count').textContent = count ? `${count} configured` : '';

  if (!count) {
    tbody.innerHTML = '<tr class="empty-row"><td colspan="6">No destinations yet. Add one below.</td></tr>';
    return;
  }

  tbody.innerHTML = dests.map(d => {
    const isLocal   = !d.label;
    const id        = isLocal ? 'local' : esc(d.label);
    const typeBadge = isLocal
      ? '<span class="badge badge-local">local</span>'
      : '<span class="badge badge-project">project</span>';
    const labelCell = isLocal ? '<span style="color:var(--muted)">—</span>' : `<strong>${esc(d.label)}</strong>`;
    const tokenCell = isLocal
      ? '<span style="color:var(--muted);font-size:.8125rem">native API</span>'
      : `<code>${esc(mask(d.token || ''))}</code>`;
    const statusBadge = d.enabled
      ? '<span class="badge badge-on">enabled</span>'
      : '<span class="badge badge-off">disabled</span>';
    const defBadge = (!isLocal && d.label === defaultProject)
      ? ' <span class="badge badge-default">default</span>' : '';

    return `<tr>
      <td>${typeBadge}</td>
      <td>${labelCell}${defBadge}</td>
      <td class="url" title="${esc(d.url)}">${esc(d.url)}</td>
      <td>${tokenCell}</td>
      <td>${statusBadge}</td>
      <td>
        <div class="actions">
          <button class="btn btn-sm btn-ghost" onclick="toggle('${id}')">
            ${d.enabled ? 'Disable' : 'Enable'}
          </button>
          <button class="btn btn-sm btn-danger-ghost" onclick="remove('${id}')">Remove</button>
        </div>
      </td>
    </tr>`;
  }).join('');
}

// ---- load config ----
async function load() {
  try {
    const r   = await fetch('/api/config');
    const cfg = await r.json();
    const dests = cfg.destinations || [];
    const def   = cfg.default_project || '';

    renderDestinations(dests, def);

    // Populate default-project dropdown (project destinations only)
    const sel = $('default-project-select');
    const projects = dests.filter(d => d.label);
    sel.innerHTML = '<option value="">— None (use local LightRAG) —</option>' +
      projects.filter(p => p.enabled).map(p =>
        `<option value="${esc(p.label)}"${p.label === def ? ' selected' : ''}>${esc(p.label)}</option>`
      ).join('');
  } catch(e) {
    flash('Could not load config: ' + e.message, 'err');
  }
}

// ---- add local ----
$('add-local-form').addEventListener('submit', async e => {
  e.preventDefault();
  const fd = new FormData(e.target);
  const r = await fetch('/api/destinations', {
    method: 'POST', headers: {'Content-Type':'application/json'},
    body: JSON.stringify({ url: fd.get('url') })  // no label, no token
  });
  const d = await r.json();
  if (d.status === 'ok') { e.target.reset(); load(); flash('Local LightRAG configured.', 'ok'); }
  else flash(d.error || 'Failed.', 'err');
});

// ---- add project ----
$('add-project-form').addEventListener('submit', async e => {
  e.preventDefault();
  const fd = new FormData(e.target);
  const r = await fetch('/api/destinations', {
    method: 'POST', headers: {'Content-Type':'application/json'},
    body: JSON.stringify({ url: fd.get('url'), label: fd.get('label'), token: fd.get('token') })
  });
  const d = await r.json();
  if (d.status === 'ok') { e.target.reset(); load(); flash('Project destination added.', 'ok'); }
  else flash(d.error || 'Failed.', 'err');
});

// ---- toggle ----
async function toggle(id) {
  const r = await fetch(`/api/destinations/${encodeURIComponent(id)}/toggle`, { method: 'PATCH' });
  const d = await r.json();
  if (d.status === 'ok') load();
  else flash(d.error || 'Failed to toggle.', 'err');
}

// ---- remove ----
async function remove(id) {
  const name = id === 'local' ? 'local LightRAG' : `"${id}"`;
  if (!confirm(`Remove ${name}?`)) return;
  const r = await fetch(`/api/destinations/${encodeURIComponent(id)}`, { method: 'DELETE' });
  const d = await r.json();
  if (d.status === 'ok') { load(); flash(`${name} removed.`, 'ok'); }
  else flash(d.error || 'Failed to remove.', 'err');
}

// ---- default project ----
async function saveDefaultProject() {
  const label = $('default-project-select').value || null;
  const r = await fetch('/api/default-project', {
    method: 'PUT', headers: {'Content-Type':'application/json'},
    body: JSON.stringify({ label })
  });
  const d = await r.json();
  if (d.status === 'ok') {
    load();
    flash(label ? `Default project set to "${label}".` : 'Default cleared — using local LightRAG.', 'ok');
  } else flash(d.error || 'Failed.', 'err');
}

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
    allow_remote = os.environ.get("RAGCONNECT_ALLOW_REMOTE_WEB", "false").lower() == "true"
    if host not in {"127.0.0.1", "localhost"} and not allow_remote:
        raise RuntimeError(
            "Remote bind is disabled by default. Set RAGCONNECT_ALLOW_REMOTE_WEB=true to override."
        )
    print(f"RAGConnect Web UI →  http://{host}:{port}")
    uvicorn.run(app, host=host, port=port, log_level="warning")


if __name__ == "__main__":
    main_sync()
