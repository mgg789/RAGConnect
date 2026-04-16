# RAGConnect

RAGConnect provides distributed memory for AI agents across many project-teams while keeping project memory isolated. Works with LightRAG knowledge bases.

---------

> Integration with Karpathy Method, AgentsMemory and KnowledgeMD in work

## Core model

- Create LightRAG knowledge-baase on your server
- Connect your AI-agents via MCP to RAGConnect gateway
- Create acces tokens 
- Append local LightRAG manualy
- Add to your in project AGENTS.md/CLAUDE.md project label
- Work with RAGConnect KB without fear of mixing up knowledge from different projects
- Make your local LightRAG confidential

## Quick start (server-memory, Docker)

1. Copy `.env.example` to `.env` and set required values:
   - `OPENAI_API_KEY` (you need LLM to work with LightRAG)
   - `RAGCONNECT_ADMIN_PASSWORD`
2. Start services:

```bash
docker compose up -d
```

3. Create user-token:

```bash
docker compose exec server-gateway ragconnect-server token create --role write --description "Alice"
```

## Quick start (client, no Docker)

1. Install package:

```bash
pip install -e .
```

2. Start web config UI:

```bash
ragconnect-web
```

3. Open `http://127.0.0.1:8090`, add destinations, set default project, configure remote-only mode if needed.

4. Start MCP server:

```bash
ragconnect-client
```

## Create local knowledge base (LightRAG)

This is an additional memory layer, not an alternative to project memory.
Recommended model:
- No `project_label` -> use local personal memory (It will your own AI memory).
- `project_label` / `memory-label` present -> use project remote memory. (It will be your team's shared memory)
- Local and project memory can be used side by side in one workflow. (This is the recomended method of use)

1. Start local LightRAG (example with Docker):

```bash
docker run --rm -p 9621:9621 -v lightrag_local_data:/data/lightrag \
  -e OPENAI_API_KEY=your_key_here \
  ghcr.io/hkuds/lightrag:latest
```

2. Start client UI:

```bash
ragconnect-web
```

3. Open `http://127.0.0.1:8090` and add local destination:
   - URL: `http://127.0.0.1:9621`
   - Do not set label/token for local destination.

4. Make sure `remote_only_mode` is disabled in client settings/config.

5. Start MCP server:

```bash
ragconnect-client
```

Now your agents can read/write personal memory via `memory_search` / `memory_write` without `project_label`,
and still use remote project memory when `project_label` is provided.

### Run local LightRAG without Docker

Install:

```bash
pip install "lightrag-hku[api]>=1.0.0"
```

Windows (PowerShell):

```powershell
$env:OPENAI_API_KEY="your_key_here"
# Optional:
# $env:OPENAI_API_BASE="https://your-openai-compatible-endpoint/v1"
# $env:LLM_MODEL="your-llm-model"
# $env:EMBEDDING_MODEL="your-embedding-model"
lightrag-server --host 127.0.0.1 --port 9621 --working-dir "$env:USERPROFILE\.lightrag\data"
```

Linux/macOS (bash/zsh):

```bash
export OPENAI_API_KEY="your_key_here"
# Optional:
# export OPENAI_API_BASE="https://your-openai-compatible-endpoint/v1"
# export LLM_MODEL="your-llm-model"
# export EMBEDDING_MODEL="your-embedding-model"
lightrag-server --host 127.0.0.1 --port 9621 --working-dir "$HOME/.lightrag/data"
```

### Autostart and background mode (local stack)

Local stack usually means 3 processes:
- `lightrag-server` (local memory backend)
- `ragconnect-web` (config UI)
- `ragconnect-client` (MCP server)

#### Windows autostart (Task Scheduler)

1. Create `scripts/start-local-ragconnect.ps1`:

```powershell
$ErrorActionPreference = "Stop"
$env:OPENAI_API_KEY = "your_key_here"

Start-Process -WindowStyle Hidden -FilePath "lightrag-server" -ArgumentList "--host 127.0.0.1 --port 9621 --working-dir `"$env:USERPROFILE\.lightrag\data`""
Start-Sleep -Seconds 2
Start-Process -WindowStyle Hidden -FilePath "ragconnect-web"
Start-Process -WindowStyle Hidden -FilePath "ragconnect-client"
```

2. Add startup task:

```powershell
schtasks /Create /TN "RAGConnect Local Stack" /SC ONLOGON /TR "powershell.exe -ExecutionPolicy Bypass -File `"%USERPROFILE%\path\to\start-local-ragconnect.ps1`"" /RL LIMITED /F
```

3. Manual start/stop:

```powershell
schtasks /Run /TN "RAGConnect Local Stack"
Get-Process lightrag-server,ragconnect-web,ragconnect-client -ErrorAction SilentlyContinue | Stop-Process -Force
```

#### Linux autostart (systemd user services)

Create user services:

`~/.config/systemd/user/lightrag.service`

```ini
[Unit]
Description=Local LightRAG

[Service]
Environment=OPENAI_API_KEY=your_key_here
ExecStart=%h/.local/bin/lightrag-server --host 127.0.0.1 --port 9621 --working-dir %h/.lightrag/data
Restart=always

[Install]
WantedBy=default.target
```

`~/.config/systemd/user/ragconnect-web.service`

```ini
[Unit]
Description=RAGConnect Web UI

[Service]
ExecStart=%h/.local/bin/ragconnect-web
Restart=always

[Install]
WantedBy=default.target
```

`~/.config/systemd/user/ragconnect-client.service`

```ini
[Unit]
Description=RAGConnect MCP Client

[Service]
ExecStart=%h/.local/bin/ragconnect-client
Restart=always

[Install]
WantedBy=default.target
```

Enable and run:

```bash
systemctl --user daemon-reload
systemctl --user enable --now lightrag.service ragconnect-web.service ragconnect-client.service
systemctl --user status lightrag.service ragconnect-web.service ragconnect-client.service
```

Stop:

```bash
systemctl --user stop lightrag.service ragconnect-web.service ragconnect-client.service
```

## Prompt configuration

MCP prompt text is loaded from `config/prompts/`:

- `global.md`
- `rules.md`

Set custom path with `RAGCONNECT_PROMPTS_DIR`.

## Project instructions

Copy and adapt:

- `config/AGENTS.md.example`
- `config/CLAUDE.md.example`

Set `memory-label = "your_project_label"` in repository instructions.

## Security notes

- LightRAG container is internal-only by default in compose.
- Admin endpoint `/admin/graph` uses HTTP Basic auth (`RAGCONNECT_ADMIN_USERNAME` / `RAGCONNECT_ADMIN_PASSWORD`).
- Runtime token store supports hashed tokens with expiration.
- Server has IP-based request rate limiting (`RAGCONNECT_RATE_LIMIT_*`).
- Admin auth has brute-force protection with temporary IP lockout (`RAGCONNECT_ADMIN_*` window/block settings).
