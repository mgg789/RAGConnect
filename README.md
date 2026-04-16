# RAGConnect

RAGConnect gives your AI agents one shared memory layer so you can work more efficiently. It works especially well for teams: you can create multiple project memory spaces, connect them to your local memory, keep personal and project memory separate, and still let your agents collaborate through shared project context.

## What you get

- local memory by default for personal long-term context, available to all AI agents on the same machine
- project memory selected by `project_label`, shared across everyone working on that project and separated from local memory
- MCP support for Codex, Claude Desktop, and other MCP-compatible apps
- optional server deployment for shared team memory
- optional auto-start for local memory on Windows or macOS for a smoother workflow

> For now, we support only LightRAG because we consider it the strongest option today.  
> For your convenience, we are already working on integrations with Karpathy-style memory approaches, AgentsMemory, and KnowledgeMD.

## UltraQuickStart

Clone the repository and, inside the created directory, tell your AI agent:

`Create my own memory here`

Then the agent will:

1. ask you the required questions
2. set up local memory if needed
3. connect MCP for Codex and Claude Desktop if needed
4. enable local memory auto-start if needed
5. deploy server memory if needed
6. test `health`, `write`, and `search`
7. confirm routing: without a label -> local memory, with a label -> project memory

This is the simplest and most convenient setup path. Recommended.

## Scripts for local installation

### Windows

- `scripts/windows/install-local-stack.ps1` — main bootstrap
- `scripts/windows/install-codex-mcp.ps1` — MCP for Codex
- `scripts/windows/install-claude-mcp.ps1` — MCP for Claude Desktop
- `scripts/windows/install-autostart.ps1` — auto-start via the Startup folder
- `scripts/windows/uninstall-autostart.ps1`
- `scripts/windows/start-local-stack.ps1`
- `scripts/windows/stop-local-stack.ps1`

What `install-local-stack.ps1` does:

- creates `~/.ragconnect` and `.venv`
- installs the project, LightRAG API, and embedding runtime
- writes `~/.ragconnect/.env` and `client_config.yaml`
- optionally installs MCP for Codex or Claude Desktop
- optionally enables auto-start

```powershell
powershell -File scripts/windows/install-local-stack.ps1 `
  -RepoRoot "C:\path\to\RAGConnect" `
  -PythonPath "C:\Path\To\python.exe" `
  -InstallCodexMcp `
  -InstallClaudeMcp `
  -EnableAutostart
```

### macOS

- `scripts/macos/install-local-stack.sh` — main bootstrap
- `scripts/macos/install-codex-mcp.sh` — MCP for Codex
- `scripts/macos/install-claude-mcp.sh` — MCP for Claude Desktop
- `scripts/macos/install-autostart.sh` — auto-start via LaunchAgent
- `scripts/macos/uninstall-autostart.sh`
- `scripts/macos/start-local-stack.sh`
- `scripts/macos/stop-local-stack.sh`

What `install-local-stack.sh` does:

- creates `~/.ragconnect` and `.venv`
- installs the project, LightRAG API, and embedding runtime
- writes `~/.ragconnect/.env` (`chmod 600`) and `client_config.yaml`
- optionally installs MCP for Codex or Claude Desktop
- optionally enables auto-start via `~/Library/LaunchAgents/com.ragconnect.local-stack.plist`

```bash
bash scripts/macos/install-local-stack.sh \
  --repo-root /path/to/RAGConnect \
  --api-key sk-... \
  --install-claude-mcp \
  --enable-autostart
```

## MCP setup for Codex

The recommended approach is to use the direct Python module entrypoint.

Block for `~/.codex/config.toml`:

```toml
[mcp_servers.ragconnect]
command = "/Users/<you>/.ragconnect/.venv/bin/python3"
args = ["-m", "client_gateway.mcp_server"]
cwd = "/path/to/RAGConnect"
enabled = true

[mcp_servers.ragconnect.env]
PYTHONPATH = "/path/to/RAGConnect"
RAGCONNECT_CONFIG_PATH = "/Users/<you>/.ragconnect/client_config.yaml"
RAGCONNECT_PROMPTS_DIR = "/path/to/RAGConnect/config/prompts"
```

The scripts `install-codex-mcp.ps1` and `install-codex-mcp.sh` write this block automatically.

## MCP setup for Claude Desktop

Claude Desktop uses the same module entrypoint: `python -m client_gateway.mcp_server`.  
The scripts `install-claude-mcp.ps1` and `install-claude-mcp.sh` update `claude_desktop_config.json` automatically.

## Auto-start for local memory

If the user answers “yes” to the auto-start question, the agent enables it automatically:

- **Windows**: places a `.cmd` file into the `Startup` folder (`%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup`)
- **macOS**: creates a LaunchAgent plist in `~/Library/LaunchAgents/` and loads it immediately

Neither option requires administrator privileges.

## Snippet for project `AGENTS.md` or `CLAUDE.md`

For a project that should use project memory, copy one of these files and replace `LABEL_HERE`:

- `config/AGENTS.md.example`
- `config/CLAUDE.md.example`

This snippet tells the agent:

- which `project_label` to use
- that memory should be treated as working memory, not as an optional extra tool
- when to search memory before replying
- when results must be written back
- when to use local memory without a label

## Docker deployment for shared server memory

### Quick path

1. Copy `.env.example` to `.env`.
2. Fill in `OPENAI_API_KEY` and `RAGCONNECT_ADMIN_PASSWORD`.
3. Run `docker compose up -d`.
4. Create a write token:

```bash
docker compose exec server-gateway ragconnect-server token create --role write --description "Initial user"
```

### What the current Docker stack includes

- LightRAG with OpenAI-compatible binding
- a local embedding proxy inside the LightRAG container
- the default embedding model `intfloat/multilingual-e5-small`
- a project gateway with token auth

So the Docker configuration mirrors the working setup already validated in a real environment.

## Memory model

- without `project_label` -> local personal memory
- with `project_label="some-project"` -> shared project memory for that project
- personal notes should stay in local memory
- project knowledge should stay in project memory

## MCP prompts

The prompts that define memory behavior for the agent are stored here:

- `config/prompts/global.md`
- `config/prompts/rules.md`

They are intentionally written so the agent treats memory as an external long-term memory layer and uses it proactively.

## Technical notes

- default local embeddings: `intfloat/multilingual-e5-small`, dimension `384`
- direct Codex MCP entrypoint: `python -m client_gateway.mcp_server`
- `pyproject.toml` uses `setuptools.build_meta`, so editable install works normally
- current Windows auto-start uses the `Startup` folder

## How it works

RAGConnect keeps the architecture simple:

- one local Client Gateway per user
- one local LightRAG for personal memory
- one isolated server stack per project
- one MCP entrypoint for all local AI tools

The Client Gateway receives requests from Codex, Claude Desktop, and other MCP-compatible apps, then decides where to send them:

- no label -> local memory
- known `project_label` -> project server
- if project search fails -> local fallback with a warning

Each project server has its own Server Gateway in front of LightRAG. Its job is simple: validate the token and allow or deny access.

Each project token has one of two roles:

- `readonly`
- `write`

This keeps project memory isolated, avoids complex multi-tenant logic, and makes deployment easier: if you want two projects, you run two servers or two Docker containers.

> Created by Droidje tech developers for developers around the world  
> We work for the progress of AI
