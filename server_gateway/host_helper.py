from __future__ import annotations

import io
import json
import shutil
import subprocess
import tarfile
import tempfile
import time
import zipfile
from pathlib import Path
from typing import Any

import click
import httpx

from shared.control_plane import list_pending_requests, mark_heartbeat, read_result, requests_dir, write_result
from shared.dotenv import read_dotenv, update_dotenv
from shared.ops_log import mask_secret, utc_now_iso, write_json
from shared.runtime import ensure_dir, get_server_backup_dir, get_server_control_dir


class HostHelper:
    def __init__(self, repo_root: str | Path | None = None) -> None:
        self.repo_root = Path(repo_root).resolve() if repo_root else Path.cwd().resolve()
        self.control_dir = get_server_control_dir()
        self.backup_dir = get_server_backup_dir()
        self.state_dir = ensure_dir(self.control_dir / "state")
        self.config_path = self.state_dir / "helper_config.json"
        self.heartbeat_path = self.state_dir / "helper_heartbeat.json"
        self.env_path = self.repo_root / ".env"

    def compose(self, *args: str, input_bytes: bytes | None = None) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["docker", "compose", *args],
            cwd=str(self.repo_root),
            input=input_bytes,
            capture_output=True,
            check=False,
        )

    def helper_config(self) -> dict[str, Any]:
        default = {"backup_schedule_minutes": 0, "backup_retention_count": 5, "backup_retention_days": 14}
        if not self.config_path.exists():
            return default
        try:
            loaded = json.loads(self.config_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return default
        return {**default, **loaded}

    @staticmethod
    def parse_compose_services(raw: bytes) -> list[dict[str, Any]]:
        text = raw.decode("utf-8", errors="replace").strip()
        if not text:
            return []
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            services: list[dict[str, Any]] = []
            for line in text.splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    item = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(item, dict):
                    services.append(item)
            return services
        if isinstance(parsed, list):
            return [item for item in parsed if isinstance(item, dict)]
        if isinstance(parsed, dict):
            return [parsed]
        return []

    def save_helper_config(self, payload: dict[str, Any]) -> None:
        write_json(self.config_path, payload)

    def heartbeat(self, extra: dict[str, Any] | None = None) -> None:
        payload = {"timestamp": utc_now_iso(), "repo_root": str(self.repo_root)}
        if extra:
            payload.update(extra)
        write_json(self.heartbeat_path, payload)
        mark_heartbeat({"repo_root": str(self.repo_root), **(extra or {})})

    def status(self) -> dict[str, Any]:
        runtime = read_dotenv(self.env_path)
        compose_ps = self.compose("ps", "--format", "json")
        services: list[dict[str, Any]] = []
        if compose_ps.returncode == 0:
            services = self.parse_compose_services(compose_ps.stdout)
        return {
            "status": "ok" if compose_ps.returncode == 0 else "error",
            "repo_root": str(self.repo_root),
            "env_path": str(self.env_path),
            "helper_online": self.heartbeat_path.exists(),
            "services": services,
            "runtime": {
                "openai_api_base": runtime.get("OPENAI_API_BASE", ""),
                "llm_model": runtime.get("LLM_MODEL", ""),
                "has_openai_api_key": bool(runtime.get("OPENAI_API_KEY")),
                "masked_openai_api_key": mask_secret(runtime.get("OPENAI_API_KEY", "")),
            },
            "backups": self.list_backups(),
            "helper_config": self.helper_config(),
        }

    def validate_runtime(self, payload: dict[str, Any]) -> dict[str, Any]:
        base = (payload.get("openai_api_base") or "").strip()
        api_key = (payload.get("openai_api_key") or "").strip()
        llm_model = (payload.get("llm_model") or "").strip()
        if not base:
            return {"status": "error", "message": "OPENAI_API_BASE is required for validation.", "model_available": False}
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        result = {
            "status": "ok",
            "openai_api_base": base,
            "llm_model": llm_model,
            "model_available": False,
            "reachable": False,
            "authenticated": False,
            "models_checked": [],
        }
        try:
            with httpx.Client(timeout=15.0) as client:
                response = client.get(f"{base.rstrip('/')}/models", headers=headers)
            result["reachable"] = True
            result["authenticated"] = response.status_code < 400
            if response.status_code >= 400:
                result["status"] = "warning"
                result["message"] = f"Model endpoint returned HTTP {response.status_code}."
                return result
            data = response.json() if response.content else {}
            models = [item.get("id") for item in data.get("data", []) if isinstance(item, dict)]
            result["models_checked"] = models[:50]
            if llm_model and llm_model in models:
                result["model_available"] = True
            elif not llm_model:
                result["status"] = "warning"
                result["message"] = "LLM_MODEL is empty."
            else:
                result["status"] = "warning"
                result["message"] = f"Model '{llm_model}' was not found in provider response."
        except Exception as exc:
            result["status"] = "warning"
            result["message"] = f"Validation request failed: {exc}"
        return result

    def apply_runtime(self, payload: dict[str, Any]) -> dict[str, Any]:
        previous_env = self.env_path.read_text(encoding="utf-8") if self.env_path.exists() else ""
        updates = {
            "OPENAI_API_BASE": (payload.get("openai_api_base") or "").strip() or None,
            "LLM_MODEL": (payload.get("llm_model") or "").strip() or None,
            "OPENAI_API_KEY": (payload.get("openai_api_key") or "").strip() or None,
            "LOCAL_EMBEDDING_MODE": (payload.get("local_embedding_mode") or "").strip() or None,
            "LOCAL_EMBEDDING_MODEL": (payload.get("local_embedding_model") or "").strip() or None,
            "LOCAL_EMBEDDING_DIM": str(payload.get("local_embedding_dim")).strip() if payload.get("local_embedding_dim") else None,
            "EMBEDDING_MODEL": (payload.get("embedding_model") or "").strip() or None,
            "EMBEDDING_DIM": str(payload.get("embedding_dim")).strip() if payload.get("embedding_dim") else None,
        }
        update_dotenv(self.env_path, updates)
        compose = self.compose("up", "-d", "--force-recreate", "lightrag", "server-gateway")
        if compose.returncode != 0:
            self.env_path.write_text(previous_env, encoding="utf-8")
            rollback = self.compose("up", "-d", "--force-recreate", "lightrag", "server-gateway")
            return {
                "status": "apply_failed_rolled_back",
                "message": compose.stderr.decode("utf-8", errors="replace"),
                "rollback_status": rollback.returncode,
            }

        deadline = time.time() + 90
        healthy = False
        while time.time() < deadline:
            health = self.compose("exec", "-T", "server-gateway", "python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=5)")
            if health.returncode == 0:
                healthy = True
                break
            time.sleep(3)

        if not healthy:
            self.env_path.write_text(previous_env, encoding="utf-8")
            rollback = self.compose("up", "-d", "--force-recreate", "lightrag", "server-gateway")
            return {
                "status": "apply_failed_rolled_back",
                "message": "Health check did not recover after runtime apply.",
                "rollback_status": rollback.returncode,
            }

        return {"status": "applied_ok", "message": "Runtime applied and services restarted successfully."}

    def list_backups(self) -> list[dict[str, Any]]:
        output: list[dict[str, Any]] = []
        for artifact in sorted(self.backup_dir.glob("*.zip"), reverse=True):
            output.append(
                {
                    "name": artifact.name,
                    "size_bytes": artifact.stat().st_size,
                    "modified_at": utc_now_iso() if not artifact.exists() else time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(artifact.stat().st_mtime)),
                }
            )
        return output

    def create_backup(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        payload = payload or {}
        ensure_dir(self.backup_dir)
        backup_id = payload.get("backup_id") or f"backup_{time.strftime('%Y%m%d_%H%M%S')}"
        archive_path = self.backup_dir / f"{backup_id}.zip"
        manifest = {
            "backup_id": backup_id,
            "created_at": utc_now_iso(),
            "repo_root": str(self.repo_root),
            "runtime": read_dotenv(self.env_path),
        }
        with tempfile.TemporaryDirectory(prefix="ragconnect-backup-") as tmpdir:
            tmp_path = Path(tmpdir)
            (tmp_path / ".env").write_text(self.env_path.read_text(encoding="utf-8") if self.env_path.exists() else "", encoding="utf-8")
            self._dump_service_tar("lightrag", "tar -C /data -czf - lightrag", tmp_path / "lightrag_data.tar.gz")
            self._dump_service_tar("server-gateway", "tar -C /data -czf - server_tokens.yaml", tmp_path / "server_tokens.tar.gz", optional=True)
            write_json(tmp_path / "manifest.json", manifest)
            with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                for item in tmp_path.iterdir():
                    archive.write(item, arcname=item.name)
        self.prune_backups(self.helper_config())
        return {"status": "ok", "backup_id": backup_id, "artifact": str(archive_path)}

    def restore_backup(self, payload: dict[str, Any]) -> dict[str, Any]:
        artifact = self.backup_dir / payload.get("artifact", "")
        if not artifact.exists():
            return {"status": "error", "message": f"Backup artifact '{artifact.name}' not found."}
        with tempfile.TemporaryDirectory(prefix="ragconnect-restore-") as tmpdir:
            tmp_path = Path(tmpdir)
            with zipfile.ZipFile(artifact, "r") as archive:
                archive.extractall(tmp_path)
            if (tmp_path / ".env").exists():
                shutil.copyfile(tmp_path / ".env", self.env_path)
            self.compose("up", "-d", "lightrag", "server-gateway")
            self._restore_service_tar("lightrag", tmp_path / "lightrag_data.tar.gz", "rm -rf /data/lightrag/* && tar -C /data -xzf -")
            if (tmp_path / "server_tokens.tar.gz").exists():
                self._restore_service_tar("server-gateway", tmp_path / "server_tokens.tar.gz", "rm -f /data/server_tokens.yaml && tar -C /data -xzf -")
            compose = self.compose("up", "-d", "--force-recreate", "lightrag", "server-gateway")
            return {
                "status": "ok" if compose.returncode == 0 else "warning",
                "message": "Backup restored." if compose.returncode == 0 else compose.stderr.decode("utf-8", errors="replace"),
            }

    def prune_backups(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        settings = {**self.helper_config(), **(payload or {})}
        retention_count = int(settings.get("backup_retention_count", 5))
        retention_days = int(settings.get("backup_retention_days", 14))
        removed: list[str] = []
        backups = sorted(self.backup_dir.glob("*.zip"), reverse=True)
        for artifact in backups[retention_count:]:
            removed.append(artifact.name)
            artifact.unlink(missing_ok=True)
        if retention_days > 0:
            cutoff = time.time() - retention_days * 86400
            for artifact in list(self.backup_dir.glob("*.zip")):
                if artifact.exists() and artifact.stat().st_mtime < cutoff and artifact.name not in removed:
                    removed.append(artifact.name)
                    artifact.unlink(missing_ok=True)
        return {"status": "ok", "removed": removed}

    def _dump_service_tar(self, service: str, shell_cmd: str, target: Path, optional: bool = False) -> None:
        result = self.compose("exec", "-T", service, "sh", "-lc", shell_cmd)
        if result.returncode != 0:
            if optional:
                return
            raise RuntimeError(result.stderr.decode("utf-8", errors="replace"))
        target.write_bytes(result.stdout)

    def _restore_service_tar(self, service: str, source: Path, shell_cmd: str) -> None:
        if not source.exists():
            return
        result = self.compose("exec", "-T", service, "sh", "-lc", shell_cmd, input_bytes=source.read_bytes())
        if result.returncode != 0:
            raise RuntimeError(result.stderr.decode("utf-8", errors="replace"))

    def process_request(self, request: dict[str, Any]) -> dict[str, Any]:
        action = request.get("action")
        payload = request.get("payload") or {}
        if action == "status":
            return self.status()
        if action == "validate-runtime":
            return self.validate_runtime(payload)
        if action == "apply-runtime":
            return self.apply_runtime(payload)
        if action == "backup":
            return self.create_backup(payload)
        if action == "restore":
            return self.restore_backup(payload)
        if action == "prune-backups":
            return self.prune_backups(payload)
        if action == "update-helper-config":
            self.save_helper_config(payload)
            return {"status": "ok", "helper_config": self.helper_config()}
        return {"status": "error", "message": f"Unknown action '{action}'."}

    def run_daemon(self, interval_seconds: int = 5) -> None:
        next_backup = 0.0
        while True:
            self.heartbeat({"mode": "daemon"})
            config = self.helper_config()
            schedule_minutes = int(config.get("backup_schedule_minutes", 0) or 0)
            now = time.time()
            if schedule_minutes > 0 and now >= next_backup:
                self.create_backup({"backup_id": f"scheduled_{time.strftime('%Y%m%d_%H%M%S')}"})
                next_backup = now + schedule_minutes * 60

            for request in list_pending_requests():
                request_id = request.get("id", "")
                result = self.process_request(request)
                result.update({"request_id": request_id, "completed_at": utc_now_iso()})
                write_result(request_id, result)
                Path(request["_path"]).unlink(missing_ok=True)
            time.sleep(interval_seconds)


@click.group()
def cli() -> None:
    """Host-side helper for runtime apply and backups."""


@cli.command("daemon")
@click.option("--repo-root", default=".")
def daemon_cmd(repo_root: str) -> None:
    HostHelper(repo_root).run_daemon()


@cli.command("status")
@click.option("--repo-root", default=".")
def status_cmd(repo_root: str) -> None:
    click.echo(json.dumps(HostHelper(repo_root).status(), ensure_ascii=False, indent=2))


@cli.command("validate-runtime")
@click.option("--repo-root", default=".")
@click.option("--openai-api-base", required=True)
@click.option("--openai-api-key", default="")
@click.option("--llm-model", default="")
def validate_runtime_cmd(repo_root: str, openai_api_base: str, openai_api_key: str, llm_model: str) -> None:
    result = HostHelper(repo_root).validate_runtime(
        {"openai_api_base": openai_api_base, "openai_api_key": openai_api_key, "llm_model": llm_model}
    )
    click.echo(json.dumps(result, ensure_ascii=False, indent=2))


@cli.command("backup")
@click.option("--repo-root", default=".")
def backup_cmd(repo_root: str) -> None:
    click.echo(json.dumps(HostHelper(repo_root).create_backup(), ensure_ascii=False, indent=2))


@cli.command("restore")
@click.option("--repo-root", default=".")
@click.option("--artifact", required=True)
def restore_cmd(repo_root: str, artifact: str) -> None:
    click.echo(json.dumps(HostHelper(repo_root).restore_backup({"artifact": artifact}), ensure_ascii=False, indent=2))


@cli.command("prune-backups")
@click.option("--repo-root", default=".")
@click.option("--backup-retention-count", default=5, type=int)
def prune_cmd(repo_root: str, backup_retention_count: int) -> None:
    click.echo(
        json.dumps(
            HostHelper(repo_root).prune_backups({"backup_retention_count": backup_retention_count}),
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    cli()
