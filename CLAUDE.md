# Memory configuration for this project

Claude must follow this file before starting memory setup.

## Project memory label

memory-label = "LABEL_HERE"

## Mandatory question flow before memory startup

Ask the user these exact questions, in order:

1. "Do you want only a local setup (connect to existing projects), or do you also want to deploy project memory on your own server?"
2. "If server deployment is needed, please provide SSH connection details (host, port, username, authentication method: password or key file)."
3. "Is Docker available on this machine right now?"
4. "Where is your `OPENAI_API_KEY` configured? (.env / environment / provide now)"
5. "Do you use default OpenAI endpoint or a compatible endpoint URL?"
6. "If compatible endpoint is used, what is `OPENAI_API_BASE`?"
7. "Do you want custom `LLM_MODEL` and `EMBEDDING_MODEL`?"
8. "Should we configure local memory, remote project memory, or both?"
9. "What `memory-label` should be used for this repository?"
10. "What is the project Server Gateway URL?"
11. "What is the project token (`tok_...`)?"
12. "Should no-label requests use local memory by default?"
13. "Enable `remote_only_mode`? (yes/no)"
14. "Enable strict routing (no fallback)? (yes/no)"

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

## Security behavior

- Do not echo tokens/API keys in responses.
- Mask secrets in logs and UI previews.
