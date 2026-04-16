# RAGConnect Agent Bootstrap Rules

These rules tell any coding agent how to initialize memory for this project.

## Goal

Use RAGConnect memory correctly:
- Local memory is default for personal knowledge (no label).
- Project memory is used when `project_label` is provided.
- For this repository, project memory label is:

memory-label = "LABEL_HERE"

## Startup protocol (must follow)

Before running memory commands, the agent must ask the user configuration questions in the exact order below.
Do not skip questions, and do not assume defaults without explicit confirmation.

### Step 1: detect runtime mode (on user's language)

You must ask:
1. "Do you want only a local setup (connect to existing projects), or do you also want to deploy project memory on your own server?"
2. "If server deployment is needed, please provide SSH connection details (host, port, username, authentication method: password or key file)."
3. "Is Docker installed and available on this machine right now?"
4. "If server deployment is needed, please provide the server sudo password (or confirm passwordless sudo)."
5. "Do you already have a domain for this server? If yes, provide the domain name."
6. "If domain is provided, please create and confirm DNS records now (A/AAAA to server IP)."
7. "What Git repository URL should be used on the server to clone RAGConnect?"
8. "Which branch or tag should be deployed on the server?"

If user is unsure, the agent may run checks and report result:
- `docker --version`
- `docker compose version`

### Step 2: collect required configuration (on user's language)

You must ask:
9. "What is your `OPENAI_API_KEY` source? (already in environment / set in .env / provide now manually)"
10. "Do you use default OpenAI endpoint or an OpenAI-compatible URL?"
11. "If compatible URL is used, what is `OPENAI_API_BASE`?"
12. "Do you want custom `LLM_MODEL` and `EMBEDDING_MODEL` values? If yes, provide both."
13. "Will you use local personal memory, remote project memory, or both?"
14. "If remote project memory is used, what is the project label (`memory-label`) for this repository?"
15. "If remote project memory is used, what is the Server Gateway URL?"
16. "If remote project memory is used, what is the access token (`tok_...`)?"
17. "Should requests without label go to local memory? (yes/no)"
18. "Do you want `remote_only_mode=true`? (yes/no)"
19. "Do you want strict routing (no fallback when project label is wrong/unavailable)? (yes/no)"

### Step 3: apply and verify

After answers are collected, the agent must:
1. If server deployment is requested:
   - connect via SSH;
   - install Docker + Docker Compose if missing;
   - clone repository from the provided Git URL and checkout the requested branch/tag;
   - create `.env` from `.env.example` and fill required values;
   - run `docker compose up -d`;
   - generate project token and report only masked form back to user;
   - if domain is provided, verify DNS resolves to server IP before exposing the endpoint.
2. Configure destination(s) in `ragconnect-web`.
3. Start `ragconnect-client`.
4. Run health checks.
5. Confirm routing behavior:
   - without `project_label` -> local memory (unless remote-only mode)
   - with `project_label="LABEL_HERE"` -> project memory

### Step 4: memory usage behavior

For project work in this repo:
- Always search with `project_label="LABEL_HERE"` before answering architecture/history questions.
- Always write project decisions with `project_label="LABEL_HERE"`.

For personal notes:
- Omit `project_label`.

## Safety rules

- Never print secrets (API keys, tokens) back in plain text.
- If a required value is missing, ask the user before proceeding.
- If Docker mode is requested but Docker is unavailable, fall back to local mode only after user confirmation.
