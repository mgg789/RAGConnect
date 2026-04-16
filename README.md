# RAGConnect

RAGConnect provides distributed memory for AI agents across many projects while keeping project memory isolated.

## Core model

- `1 server gateway + 1 LightRAG = 1 project`
- Client routes requests by `memory-label` / `project_label`
- Server gateway validates token and proxies to its own LightRAG
- Local user memory is optional

## Quick start (server, Docker)

1. Copy `.env.example` to `.env` and set required values:
   - `OPENAI_API_KEY`
   - `RAGCONNECT_ADMIN_PASSWORD`
2. Start services:

```bash
docker compose up -d
```

3. Create token:

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
