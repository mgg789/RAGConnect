# Memory configuration for this project

If user tell `Create my own memory here` help him create LightRAG memory with gateways (this is instruction for you)

Claude must follow this file before starting memory setup.

## Project memory label

memory-label = "LABEL_HERE"

## Mandatory question flow before memory startup

Ask the user these exact questions, in order:

1. "Do you want only a local setup (connect to existing projects), or do you also want to deploy project memory on your own server?"
2. "If server deployment is needed, please provide SSH connection details (host, port, username, authentication method: password or key file)."
3. "Is Docker available on this machine right now?"
4. "If server deployment is needed, please provide the server sudo password (or confirm passwordless sudo)."
5. "Do you already have a domain for this server? If yes, provide the domain name."
6. "If domain is provided, please create and confirm DNS records now (A/AAAA to server IP)."
7. "What Git repository URL should be used on the server to clone RAGConnect?"
8. "Which branch or tag should be deployed on the server?"
9. "Where is your `OPENAI_API_KEY` configured? (.env / environment / provide now)"
10. "Do you use default OpenAI endpoint or a compatible endpoint URL?"
11. "If compatible endpoint is used, what is `OPENAI_API_BASE`?"
12. "Do you want custom `LLM_MODEL` and `EMBEDDING_MODEL`?"
13. "Should we configure local memory, remote project memory, or both?"
14. "What `memory-label` should be used for this repository?"
15. "What is the project Server Gateway URL?"
16. "What is the project token (`tok_...`)?"
17. "Should no-label requests use local memory by default?"
18. "Enable `remote_only_mode`? (yes/no)"
19. "Enable strict routing (no fallback)? (yes/no)"

## Runtime behavior rules

- Without `project_label`: use local personal memory (if local configured).
- With `project_label`: use project memory.
- For this repository, use `project_label="LABEL_HERE"` for project knowledge.
- Personal/cross-project notes must omit `project_label`.

## Docker detection behavior

If user is unsure about Docker availability, run:
- `docker --version`
- `docker compose version`

If Docker is unavailable, ask confirmation before switching to local non-Docker startup path.

## Server deployment over SSH (if requested)

When server deployment is requested, Claude must:

1. Connect via SSH with user-provided access details.
2. Install Docker and Docker Compose if missing.
3. Clone repository from the provided Git URL and checkout requested branch/tag.
4. Create `.env` from `.env.example` and fill required values.
5. Run `docker compose up -d`.
6. Create token via server CLI and return only masked token in chat.
7. If domain is provided, verify DNS A/AAAA records resolve to the server IP before final confirmation.

## Security behavior

- Do not echo tokens/API keys in responses.
- Mask secrets in logs and UI previews.
