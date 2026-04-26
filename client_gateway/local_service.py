from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import click

from shared.ops_log import read_json, tail_text, utc_now_iso, write_json
from shared.runtime import ensure_dir, get_local_log_dir, get_local_state_dir, get_ragconnect_home


SUPERVISOR_PID = "local_supervisor.pid"
STATE_FILE = "local_service_state.json"
HEARTBEAT_FILE = "local_supervisor_heartbeat.json"
PORTS = {"proxy": 9622, "lightrag": 9621, "web": 8090}


class LocalServiceManager:
    def __init__(self, repo_root: str | None = None, rag_home: str | None = None) -> None:
        self.rag_home = Path(rag_home).expanduser() if rag_home else get_ragconnect_home()
        self.repo_root = Path(repo_root).expanduser().resolve() if repo_root else Path.cwd()
        self.log_dir = ensure_dir(get_local_log_dir())
        self.state_dir = ensure_dir(get_local_state_dir())
        self.data_dir = ensure_dir(self.rag_home / "data" / "lightrag")
        self.env_path = self.rag_home / ".env"
        self.state_path = self.state_dir / STATE_FILE
        self.supervisor_pid_path = self.state_dir / SUPERVISOR_PID
        self.heartbeat_path = self.state_dir / HEARTBEAT_FILE
        self.stop_requested = False

    def python_executable(self) -> Path:
        windows = self.rag_home / ".venv" / "Scripts" / "python.exe"
        if windows.exists():
            return windows
        return self.rag_home / ".venv" / "bin" / "python3"

    def executable(self, name: str) -> Path:
        candidates = [
            self.rag_home / ".venv" / "Scripts" / f"{name}.exe",
            self.rag_home / ".venv" / "Scripts" / name,
            self.rag_home / ".venv" / "bin" / name,
        ]
        for candidate in candidates:
            if candidate.exists():
                return candidate
        return candidates[0]

    def load_env(self) -> dict[str, str]:
        env = dict(os.environ)
        if self.env_path.exists():
            for raw_line in self.env_path.read_text(encoding="utf-8", errors="replace").splitlines():
                line = raw_line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                env[key.strip()] = value.strip().strip("'").strip('"')
        env["PYTHONUTF8"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"
        env["RAGCONNECT_HOME"] = str(self.rag_home)
        env["RAGCONNECT_REPO_ROOT"] = str(self.repo_root)
        env["PYTHONPATH"] = str(self.repo_root) + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
        if self.needs_proxy(env):
            env["LLM_BINDING_HOST"] = f"http://127.0.0.1:{PORTS['proxy']}/v1"
            env["EMBEDDING_BINDING_HOST"] = f"http://127.0.0.1:{PORTS['proxy']}/v1"
        else:
            llm_base = env.get("OPENAI_API_BASE", "https://api.openai.com/v1")
            env["LLM_BINDING_HOST"] = llm_base
            env["EMBEDDING_BINDING_HOST"] = env.get("EMBEDDING_API_BASE") or llm_base
        return env

    def needs_proxy(self, env: dict[str, str]) -> bool:
        if env.get("LOCAL_EMBEDDING_MODE", "false").lower() == "true":
            return True
        embed_base = env.get("EMBEDDING_API_BASE", "")
        llm_base = env.get("OPENAI_API_BASE", "https://api.openai.com/v1")
        return bool(embed_base and embed_base != llm_base)

    def load_state(self) -> dict[str, Any]:
        return read_json(self.state_path, {"repo_root": str(self.repo_root), "components": {}, "started_at": None})

    def save_state(self, state: dict[str, Any]) -> None:
        write_json(self.state_path, state)

    def mark_heartbeat(self, state: dict[str, Any]) -> None:
        write_json(
            self.heartbeat_path,
            {"timestamp": utc_now_iso(), "pid": os.getpid(), "repo_root": str(self.repo_root), "state": state},
        )

    def is_process_running(self, pid: int | None) -> bool:
        if not pid or pid <= 0:
            return False
        try:
            if sys.platform == "win32":
                result = subprocess.run(["tasklist", "/FI", f"PID eq {pid}"], capture_output=True, check=False)
                stdout = result.stdout.decode(errors="ignore")
                return str(pid) in stdout
            os.kill(pid, 0)
            return True
        except Exception:
            return False

    def stop_pid(self, pid: int) -> None:
        if not self.is_process_running(pid):
            return
        if sys.platform == "win32":
            subprocess.run(["taskkill", "/PID", str(pid), "/T"], check=False, capture_output=True)
            time.sleep(1)
            if self.is_process_running(pid):
                subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], check=False, capture_output=True)
        else:
            try:
                os.kill(pid, signal.SIGTERM)
            except OSError:
                return

    def port_open(self, port: int) -> bool:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.5)
            return sock.connect_ex(("127.0.0.1", port)) == 0

    def http_ok(self, port: int, path: str = "/health") -> bool:
        import urllib.request

        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=3) as response:
                return 200 <= response.status < 300
        except Exception:
            return False

    def component_spec(self, env: dict[str, str]) -> dict[str, dict[str, Any]]:
        return {
            "proxy": {
                "command": [str(self.python_executable()), "-m", "local_embeddings.proxy"],
                "cwd": str(self.repo_root),
                "enabled": self.needs_proxy(env),
            },
            "lightrag": {
                "command": [
                    str(self.executable("lightrag-server")),
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(PORTS["lightrag"]),
                    "--working-dir",
                    str(self.data_dir),
                    "--llm-binding",
                    "openai",
                    "--embedding-binding",
                    "openai",
                ],
                "cwd": str(self.rag_home),
                "enabled": True,
            },
            "web": {
                "command": [str(self.executable("ragconnect-web"))],
                "cwd": str(self.rag_home),
                "enabled": True,
            },
        }

    def spawn_component(self, name: str, command: list[str], cwd: str, env: dict[str, str]) -> int:
        stdout = (self.log_dir / f"{name}.stdout.log").open("ab")
        stderr = (self.log_dir / f"{name}.stderr.log").open("ab")
        kwargs: dict[str, Any] = {
            "cwd": cwd,
            "env": env,
            "stdout": stdout,
            "stderr": stderr,
        }
        if sys.platform == "win32":
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            kwargs["start_new_session"] = True
        process = subprocess.Popen(command, **kwargs)
        return process.pid

    def ensure_components(self, state: dict[str, Any]) -> dict[str, Any]:
        env = self.load_env()
        specs = self.component_spec(env)
        components = state.setdefault("components", {})
        for name, spec in specs.items():
            if not spec["enabled"]:
                entry = components.get(name)
                if entry and self.is_process_running(entry.get("pid")):
                    self.stop_pid(entry["pid"])
                components.pop(name, None)
                continue
            entry = components.setdefault(name, {"restart_count": 0})
            pid = entry.get("pid")
            if self.is_process_running(pid):
                continue
            entry["pid"] = self.spawn_component(name, spec["command"], spec["cwd"], env)
            entry["started_at"] = utc_now_iso()
            entry["command"] = spec["command"]
            entry["restart_count"] = int(entry.get("restart_count", 0)) + 1
        state["repo_root"] = str(self.repo_root)
        state["started_at"] = state.get("started_at") or utc_now_iso()
        self.save_state(state)
        self.mark_heartbeat(state)
        return state

    def health_snapshot(self, state: dict[str, Any] | None = None) -> dict[str, Any]:
        state = state or self.load_state()
        components = state.get("components", {})
        snapshot: dict[str, Any] = {"status": "ok", "components": {}, "repo_root": state.get("repo_root")}
        for name, port in PORTS.items():
            entry = components.get(name, {})
            running = self.is_process_running(entry.get("pid"))
            healthy = self.http_ok(port) if name != "proxy" or running else False
            snapshot["components"][name] = {
                "pid": entry.get("pid"),
                "running": running,
                "healthy": healthy,
                "port": port,
                "restart_count": entry.get("restart_count", 0),
                "started_at": entry.get("started_at"),
                "port_conflict": self.port_open(port) and not running,
            }
            if name in {"lightrag", "web"} and not healthy:
                snapshot["status"] = "degraded"
        return snapshot

    def stop_all(self) -> dict[str, Any]:
        state = self.load_state()
        for entry in state.get("components", {}).values():
            pid = entry.get("pid")
            if pid:
                self.stop_pid(pid)
        if self.supervisor_pid_path.exists():
            try:
                pid = int(self.supervisor_pid_path.read_text(encoding="utf-8").strip())
            except ValueError:
                pid = 0
            if pid and pid != os.getpid():
                self.stop_pid(pid)
            self.supervisor_pid_path.unlink(missing_ok=True)
        state["components"] = {}
        self.save_state(state)
        self.mark_heartbeat(state)
        return self.health_snapshot(state)

    def doctor(self) -> dict[str, Any]:
        env = self.load_env()
        state = self.load_state()
        return {
            "repo_root": str(self.repo_root),
            "rag_home": str(self.rag_home),
            "env_path": str(self.env_path),
            "python_executable": str(self.python_executable()),
            "lightrag_executable": str(self.executable("lightrag-server")),
            "web_executable": str(self.executable("ragconnect-web")),
            "env_exists": self.env_path.exists(),
            "needs_proxy": self.needs_proxy(env),
            "ports": {name: {"port": port, "open": self.port_open(port)} for name, port in PORTS.items()},
            "state": state,
            "health": self.health_snapshot(state),
        }

    def recent_logs(self, component: str, lines: int) -> list[str]:
        if component == "audit":
            path = self.log_dir / "client_audit.jsonl"
        else:
            path = self.log_dir / f"{component}.stdout.log"
        return tail_text(path, limit=lines)

    def start_detached(self) -> dict[str, Any]:
        if self.supervisor_pid_path.exists():
            try:
                pid = int(self.supervisor_pid_path.read_text(encoding="utf-8").strip())
            except ValueError:
                pid = 0
            if self.is_process_running(pid):
                return self.health_snapshot()

        command = [str(self.python_executable()), "-m", "client_gateway.local_service", "run-supervisor", "--repo-root", str(self.repo_root), "--rag-home", str(self.rag_home)]
        stdout = (self.log_dir / "supervisor.stdout.log").open("ab")
        stderr = (self.log_dir / "supervisor.stderr.log").open("ab")
        kwargs: dict[str, Any] = {"stdout": stdout, "stderr": stderr, "cwd": str(self.repo_root)}
        if sys.platform == "win32":
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            kwargs["start_new_session"] = True
        process = subprocess.Popen(command, **kwargs)
        self.supervisor_pid_path.write_text(str(process.pid), encoding="utf-8")
        time.sleep(1)
        return self.health_snapshot()

    def run_supervisor(self) -> None:
        self.supervisor_pid_path.write_text(str(os.getpid()), encoding="utf-8")

        def _handle_stop(_sig, _frame) -> None:
            self.stop_requested = True

        signal.signal(signal.SIGTERM, _handle_stop)
        if hasattr(signal, "SIGINT"):
            signal.signal(signal.SIGINT, _handle_stop)

        state = self.load_state()
        while not self.stop_requested:
            state = self.ensure_components(state)
            time.sleep(5)
        self.stop_all()


@click.group()
def cli() -> None:
    """Manage the local RAGConnect stack."""


@cli.command("start")
@click.option("--repo-root", default=None)
@click.option("--rag-home", default=None)
def start_cmd(repo_root: str | None, rag_home: str | None) -> None:
    manager = LocalServiceManager(repo_root=repo_root, rag_home=rag_home)
    click.echo(json.dumps(manager.start_detached(), ensure_ascii=False, indent=2))


@cli.command("run-supervisor")
@click.option("--repo-root", default=None)
@click.option("--rag-home", default=None)
def run_supervisor_cmd(repo_root: str | None, rag_home: str | None) -> None:
    manager = LocalServiceManager(repo_root=repo_root, rag_home=rag_home)
    manager.run_supervisor()


@cli.command("stop")
@click.option("--repo-root", default=None)
@click.option("--rag-home", default=None)
def stop_cmd(repo_root: str | None, rag_home: str | None) -> None:
    manager = LocalServiceManager(repo_root=repo_root, rag_home=rag_home)
    click.echo(json.dumps(manager.stop_all(), ensure_ascii=False, indent=2))


@cli.command("status")
@click.option("--repo-root", default=None)
@click.option("--rag-home", default=None)
def status_cmd(repo_root: str | None, rag_home: str | None) -> None:
    manager = LocalServiceManager(repo_root=repo_root, rag_home=rag_home)
    click.echo(json.dumps(manager.health_snapshot(), ensure_ascii=False, indent=2))


@cli.command("doctor")
@click.option("--repo-root", default=None)
@click.option("--rag-home", default=None)
def doctor_cmd(repo_root: str | None, rag_home: str | None) -> None:
    manager = LocalServiceManager(repo_root=repo_root, rag_home=rag_home)
    click.echo(json.dumps(manager.doctor(), ensure_ascii=False, indent=2))


@cli.command("logs")
@click.option("--repo-root", default=None)
@click.option("--rag-home", default=None)
@click.option("--component", default="lightrag", type=click.Choice(["proxy", "lightrag", "web", "audit", "supervisor"]))
@click.option("--lines", default=50, type=int)
def logs_cmd(repo_root: str | None, rag_home: str | None, component: str, lines: int) -> None:
    manager = LocalServiceManager(repo_root=repo_root, rag_home=rag_home)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    for line in manager.recent_logs(component, lines):
        click.echo(line)


if __name__ == "__main__":
    cli()
