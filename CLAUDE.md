# RAGConnect Memory Bootstrap Rules

These instructions are intentionally identical for `AGENTS.md` and `CLAUDE.md`.

Their goal is to collect configuration from the user in plain language, set up memory without requiring terminal usage, and then use that memory as a working long-term memory for the agent.

## Goal

Use RAGConnect as a real working memory, not as an optional tool.

- Local memory is the default personal memory.
- Project memory is selected via `project_label`.
- For a specific project, the label is set as:

memory-label = "LABEL_HERE"

## Required Question Flow

Before modifying memory settings, installing MCP, enabling autostart, or deploying a server, ask the user questions in this exact order and in the user’s language.  
Do not skip questions. Do not silently assume values.

0. Provide a short intro:  
   "Welcome to RAGConnect. This service gives your AI agents shared memory and separates it into local (your personal) and project (shared with your team). Each project has its own memory. Your AI agent will now help you set everything up. Please answer a few questions.  
   Thank you for your trust! MIT License (c) 2026 Mike Gumenyuk (Droidje tech)"

1. Do you need only local memory (for your agents + connecting to existing projects), or also project memory on your own server (for your team)?
2. If a server is needed, provide SSH parameters: host, port, username, and authentication method.
3. Is Docker installed and currently available on this machine (or do you want to run without Docker)?
4. If a server is needed, provide the sudo password or confirm passwordless sudo.
5. Do you already have a domain for the server? If yes, which one?
6. If a domain exists, are DNS A/AAAA records already pointing to the server IP (if not, set them)?
7. Which Git URL should be used to clone the repository on the server (default: our repository)?
8. Which branch or tag should be deployed?
9. Where is `OPENAI_API_KEY` stored: environment, `.env`, or nowhere (if nowhere, provide it now)?
10. Are you using the default OpenAI endpoint or an OpenAI-compatible endpoint (required for LightRAG, any LLM API is acceptable)?
11. If using a compatible endpoint, what is `OPENAI_API_BASE` (endpoint URL)?
12. Do you need custom `LLM_MODEL` (default: gpt-5.4-mini) and `EMBEDDING_MODEL` (default: local)? If yes, request both values.
13. Should we configure local memory, remote project memory, or both?
14. If project memory is needed, what `memory-label` should be used for the repository and what is the repository path?
15. If using project memory, what is the Server Gateway URL? If setting up a new server, suggest using the generated URL.
16. If using project memory, what is the RAGConnect access token (`tok_...`)? If setting up a new server, suggest creating one during deployment and linking it to this machine.
17. Should requests without a label go to local memory?
18. Is `remote_only_mode=true` required?
19. Should strict routing be enabled without fallback if the label is invalid or project memory is unavailable (fallback would use local memory)?
20. Should MCP be automatically configured for Codex, Claude Desktop, or both (you can configure later)?
21. Should local memory start automatically on system startup?
22. What login and password should be set for admin access (token and graph management on the remote server)? (Only if configuring a project server)

If the user is unsure about Docker, you may check:
- `docker --version`
- `docker compose version`

## Perform Setup Without User Terminal

If the task can be performed by the agent, do not ask the user to open a terminal, copy commands, or edit configs manually.  
The agent must handle setup automatically.

Then inform the user.

---

## Local Setup on Windows

Use repository scripts:

- `scripts/windows/install-local-stack.ps1`
- `scripts/windows/install-codex-mcp.ps1`
- `scripts/windows/install-claude-mcp.ps1`
- `scripts/windows/install-autostart.ps1`
- `scripts/windows/uninstall-autostart.ps1`
- `scripts/windows/start-local-stack.ps1`
- `scripts/windows/stop-local-stack.ps1`

If the user enabled autostart, configure it automatically.  
If MCP for Codex or Claude Desktop is enabled, update configs automatically.

---

## Local Setup on macOS

Use repository scripts:

- `scripts/macos/install-local-stack.sh`
- `scripts/macos/install-codex-mcp.sh`
- `scripts/macos/install-claude-mcp.sh`
- `scripts/macos/install-autostart.sh`
- `scripts/macos/uninstall-autostart.sh`
- `scripts/macos/start-local-stack.sh`
- `scripts/macos/stop-local-stack.sh`

Autostart uses LaunchAgent:
~/Library/LaunchAgents/com.ragconnect.local-stack.plist

Claude Desktop config:
~/Library/Application Support/Claude/claude_desktop_config.json

All scripts accept:
--repo-root /path/to/RAGConnect

Run them directly without requiring user interaction.

---

## Server Deployment

If the user requests server deployment, the agent must:

1. Connect via SSH.
2. Install Docker and Docker Compose if missing.
3. Clone the repository and checkout the specified branch/tag.
4. Create `.env` from `.env.example` and fill required values.
5. Run:
   docker compose up -d
6. Create a project token and return only a masked version.
7. If using a domain, verify DNS before exposing the endpoint.
8. After deployment, validate:
   - `/health`
   - `write`
   - `search`

---

## Post-Deployment Message (in user language)

"Your RAGConnect memory is now configured. Check our README.md to learn how to use it, or simply ask your AI agent. Use the snippet below."

Then provide:
- All endpoints (local + project if applicable)
- Admin credentials (if applicable)
- Short summary of what was done
- Important notes

Then include only the snippet from `config/AGENTS.md.example` with the note:

"Add this snippet to CLAUDE.md/AGENTS.md in projects where you want to use project memory (don’t forget to update memory-label)."

---

## Using Memory After Setup

Treat memory as your external long-term memory.

- Before answering questions about architecture, decisions, constraints, bugs, history, or agreements → call `memory_search`.
- After decisions, findings, root causes, requirement clarifications, or completed steps → call `memory_write`.
- For project work always use:
  project_label="LABEL_HERE"
- For personal or cross-project notes, omit `project_label` (use local memory).
- Never silently ignore memory write errors.
- If the answer already exists in memory, reuse it.

---

## Security Rules

- Never expose API keys, tokens, or passwords.
- Mask secrets in logs and reports.
- If required data is missing, ask the user first.
- If Docker mode is requested but unavailable, switch to local-only mode only after user confirmation.