"""Server Gateway management CLI.

Usage examples:

    # Start the server
    ragconnect-server start --host 0.0.0.0 --port 8080 --lightrag-url http://127.0.0.1:9621

    # Create a write token
    ragconnect-server token create --role write --description "Alice"

    # Create a readonly token
    ragconnect-server token create --role readonly --description "CI pipeline"

    # List all tokens
    ragconnect-server token list

    # Revoke a token by its prefix
    ragconnect-server token revoke tok_abc123
"""

from __future__ import annotations

import os
import secrets
from pathlib import Path

import click
import yaml


# ---------------------------------------------------------------------------
# Root group
# ---------------------------------------------------------------------------

@click.group()
def cli() -> None:
    """RAGConnect Server Gateway management CLI."""


# ---------------------------------------------------------------------------
# start
# ---------------------------------------------------------------------------

@cli.command("start")
@click.option("--host", default="0.0.0.0", show_default=True, help="Bind host.")
@click.option("--port", default=8080, show_default=True, type=int, help="Bind port.")
@click.option(
    "--lightrag-url",
    default="http://127.0.0.1:9621",
    show_default=True,
    envvar="LIGHTRAG_URL",
    help="URL of the LightRAG backend.",
)
@click.option(
    "--token-store",
    default="server_tokens.yaml",
    show_default=True,
    envvar="TOKEN_STORE_PATH",
    help="Path to the token store YAML file.",
)
def start(host: str, port: int, lightrag_url: str, token_store: str) -> None:
    """Start the Server Gateway HTTP server."""
    os.environ["LIGHTRAG_URL"] = lightrag_url
    os.environ["TOKEN_STORE_PATH"] = token_store

    import uvicorn
    from server_gateway.app import app  # noqa: PLC0415 (deferred to pick up env vars)

    click.echo(f"Starting Server Gateway on {host}:{port}")
    click.echo(f"  LightRAG backend : {lightrag_url}")
    click.echo(f"  Token store      : {token_store}")
    uvicorn.run(app, host=host, port=port)


# ---------------------------------------------------------------------------
# token sub-group
# ---------------------------------------------------------------------------

@cli.group("token")
def token_group() -> None:
    """Manage project access tokens."""


@token_group.command("create")
@click.option(
    "--role",
    type=click.Choice(["readonly", "write"]),
    required=True,
    help="Token role.",
)
@click.option("--description", default="", help="Human-readable description.")
@click.option(
    "--token-store",
    default="server_tokens.yaml",
    show_default=True,
    help="Path to the token store YAML file.",
)
def token_create(role: str, description: str, token_store: str) -> None:
    """Create a new access token and append it to the token store."""
    path = Path(token_store)

    raw = secrets.token_hex(24)
    new_token = f"tok_{raw}"
    entry: dict = {"token": new_token, "role": role, "enabled": True}
    if description:
        entry["description"] = description

    data: dict = {}
    if path.exists():
        with open(path) as fh:
            data = yaml.safe_load(fh) or {}
    data.setdefault("tokens", []).append(entry)

    with open(path, "w") as fh:
        yaml.dump(data, fh, default_flow_style=False, allow_unicode=True)

    click.echo(f"Token created  : {new_token}")
    click.echo(f"Role           : {role}")
    if description:
        click.echo(f"Description    : {description}")
    click.echo(f"Store          : {path}")


@token_group.command("list")
@click.option(
    "--token-store",
    default="server_tokens.yaml",
    show_default=True,
    help="Path to the token store YAML file.",
)
def token_list(token_store: str) -> None:
    """List all tokens in the token store."""
    path = Path(token_store)
    if not path.exists():
        click.echo("Token store not found.")
        return

    with open(path) as fh:
        data = yaml.safe_load(fh) or {}
    tokens = data.get("tokens", [])
    if not tokens:
        click.echo("No tokens configured.")
        return

    header = f"{'TOKEN (masked)':<32} {'ROLE':<10} {'STATUS':<10} DESCRIPTION"
    click.echo(header)
    click.echo("-" * len(header))
    for t in tokens:
        tok: str = t["token"]
        masked = (tok[:10] + "…" + tok[-4:]) if len(tok) > 14 else tok
        status = "enabled" if t.get("enabled", True) else "disabled"
        desc = t.get("description", "")
        click.echo(f"{masked:<32} {t['role']:<10} {status:<10} {desc}")


@token_group.command("revoke")
@click.argument("token_prefix")
@click.option(
    "--token-store",
    default="server_tokens.yaml",
    show_default=True,
    help="Path to the token store YAML file.",
)
def token_revoke(token_prefix: str, token_store: str) -> None:
    """Disable all tokens whose value starts with TOKEN_PREFIX."""
    path = Path(token_store)
    if not path.exists():
        click.echo("Token store not found.", err=True)
        raise SystemExit(1)

    with open(path) as fh:
        data = yaml.safe_load(fh) or {}
    tokens = data.get("tokens", [])

    matched = [t for t in tokens if t["token"].startswith(token_prefix)]
    if not matched:
        click.echo(f"No token found with prefix '{token_prefix}'.", err=True)
        raise SystemExit(1)

    for t in matched:
        t["enabled"] = False
        click.echo(f"Revoked: {t['token'][:10]}…")

    with open(path, "w") as fh:
        yaml.dump(data, fh, default_flow_style=False, allow_unicode=True)


if __name__ == "__main__":
    cli()
