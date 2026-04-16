# RAGConnect — Agent Setup Instructions

Claude must follow this file before starting any memory setup or deployment.

## What this project does

RAGConnect is a distributed memory system that connects AI agents to LightRAG knowledge bases.
A single phrase from the user is enough to trigger full setup: local LightRAG instance,
remote server deployment, nginx + HTTPS, token creation, and client configuration.

## Mandatory question flow

Ask the user these questions **in order**, skipping those that are irrelevant
based on earlier answers. Explain the purpose of each question briefly before asking.

1. "Do you want only a local setup, or also deploy memory on your own server?"

2. "Please provide SSH connection details for the server:
   host, port, username, and authentication method (password or key file)."

3. "Is Docker available on the server right now?
   (If unsure, I can check with `docker --version`)"

4. "Please provide the server sudo password, or confirm passwordless sudo."

5. "Do you have a domain for this server? If yes, provide the domain name."

6. "Please create a DNS A record pointing `<domain>` → `<server IP>` and confirm when done."

7. "Which Git repository URL should I clone on the server?"
   *(Default for this project: `https://github.com/mgg789/RAGConnect.git`)*

8. "Which branch or tag should be deployed?"
   *(Default for this project: `dev`)*

9. "Please provide your LLM API key now."
   *(This is the key used to call the language model — OpenAI or compatible.)*

10. "Please provide the API base URL."
    *(Default: `https://api.openai.com/v1`. Use a custom URL for Timeweb AI,
    Azure, local Ollama, etc.)*

11. "Do you want custom `LLM_MODEL` and `EMBEDDING_MODEL` names?"
    *(Default LLM: `gpt-4o-mini`. Embeddings default to local Jina or `text-embedding-3-small`)*

12. "Should we use local embeddings (Jina/BGE from HuggingFace, no API key needed),
    or an external embedding API?"

13. "Should we configure local memory, remote project memory, or both?"

14. "Do you want to immediately configure a memory label for a specific repository?
    If yes, provide the repo path and the label name."
    *(A label is a short string like `sverk` or `droidje` that routes memory calls
    for a specific project to a specific server.)*

15. "Should no-label memory requests use local memory by default?"

16. "Enable `remote_only_mode`?
    (When enabled, all requests go only to the remote server — local memory is ignored.
    Useful if you don't want local storage at all.)"

17. "Enable strict routing (no fallback to local if remote is unavailable)?"

18. *(After deployment)* "What label should be used for the deployed server?
    This label will be used in `client_config.yaml` to route requests to this server."

## Non-Docker deployment (preferred)

When the user declines Docker, deploy as follows:

1. SSH into the server.
2. Clone the repository to `~/ragconnect` (not `/opt/`).
3. Create a Python venv: `python3 -m venv ~/ragconnect/.venv`
4. Install dependencies:
   ```
   .venv/bin/pip install "lightrag-hku[api]>=1.0.0" sentence-transformers einops httpx
   .venv/bin/pip install -r requirements.txt
   ```
5. If `pip install .` fails with `setuptools.backends.legacy` error, downgrade setuptools:
   `.venv/bin/pip install "setuptools==74.1.3"` then retry.
6. Create `bin/ragconnect-server` wrapper script (since editable install may fail):
   ```python
   #!/path/to/.venv/bin/python
   import sys; sys.path.insert(0, '/home/<user>/ragconnect')
   from server_gateway.cli import cli
   if __name__ == '__main__': cli()
   ```
7. Copy `local_embeddings/` and `lightrag_server/start.sh` if not in repo.
8. Create `~/ragconnect/.env` from `.env.example` with provided values.
9. Create systemd services for `ragconnect-lightrag` and `ragconnect-server`.
   - Use a non-standard port for Server Gateway (e.g. `1661`, not `8080`).
   - Write service files to `/tmp` first, then `sudo mv` to `/etc/systemd/system/`
     (avoids stdin conflict between sudo password and heredoc).
10. Configure nginx reverse proxy and run certbot for HTTPS.
11. Create a token: `bin/ragconnect-server token create --role write --description "main"`
12. Show only the masked token prefix to the user.

### LightRAG startup flags (non-Docker)

Always start `lightrag-server` with explicit binding flags:
```
lightrag-server \
  --host 0.0.0.0 --port 9621 \
  --working-dir <path> \
  --llm-binding openai \
  --embedding-binding openai
```
Set `OPENAI_API_BASE=http://localhost:9622/v1` when using the embedding proxy.
Set `PYTHONUTF8=1` and `PYTHONIOENCODING=utf-8` on Windows.

### Embedding model compatibility

| Model | Python | Transformers | Notes |
|-------|--------|--------------|-------|
| `jinaai/jina-embeddings-v3` | 3.12 | 5.x | Requires `einops`. Works on Linux/server. |
| `jinaai/jina-embeddings-v2-small-en` | — | <4.40 | Deprecated, avoid. |
| `BAAI/bge-small-en-v1.5` | any | any | 384 dim, reliable, use for Windows/Python 3.14+. |

## Local memory setup (Windows)

1. Install venv at `~/.ragconnect/.venv`.
2. Copy `local_embeddings/` to `~/.ragconnect/local_embeddings/`.
3. Start proxy: `python -m local_embeddings.proxy` (loads model on first run).
4. Start lightrag-server with `--llm-binding openai --embedding-binding openai`
   and `OPENAI_API_BASE=http://localhost:9622/v1`.
5. Write `~/.ragconnect/client_config.yaml` with local + remote destinations.
6. Write `~/.ragconnect/start_local.bat` for persistent startup.

## client_config.yaml structure

```yaml
destinations:
  - url: http://127.0.0.1:9621        # local LightRAG, no label
    display_name: Local memory
    enabled: true

  - url: https://<domain>             # remote server
    label: <label>
    token: tok_...
    display_name: <description>
    enabled: true

default_project: null
remote_only_mode: false
strict_project_routing: false
```

## Docker deployment (if Docker is available)

1. Copy `.env.example` to `.env` and fill values.
2. Run `docker compose up -d`.
3. Create token: `docker compose exec server-gateway ragconnect-server token create --role write`
4. Return masked token only.

## Security rules

- Never echo API keys, tokens, or passwords in responses.
- Mask secrets in all log previews.
- Store `.env` with `chmod 600`.
- Token shown once at creation — instruct user to save it.

## Runtime routing rules

- No label → local LightRAG (personal memory).
- Label present → route to matching project server.
- `remote_only_mode=true` → ignore local, always use remote.
- `strict_project_routing=false` → fall back to local on remote failure (search only).
