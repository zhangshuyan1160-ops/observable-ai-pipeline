"""Local supervisor: private PostgreSQL + API + independent workers.

All state, logs, and control files live under .local/. No system installation.
Use `interrupt gpu` to SIGKILL only a child owned by this supervisor and restart it.
"""

import argparse
import asyncio
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / ".local"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "action", choices=["start", "stop", "interrupt", "status"], nargs="?", default="start"
    )
    parser.add_argument("target", choices=["cpu", "gpu", "gpu2", "api"], nargs="?", default="gpu")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    RUNTIME.mkdir(exist_ok=True)
    control = RUNTIME / "control"
    control.mkdir(exist_ok=True)
    if args.action == "status":
        path = RUNTIME / "runtime.json"
        print(path.read_text() if path.exists() else "Not running")
        return
    if args.action != "start":
        runtime = RUNTIME / "runtime.json"
        if not runtime.exists():
            parser.error("No local supervisor is registered")
        import psutil

        info = json.loads(runtime.read_text())
        try:
            supervisor = psutil.Process(info["supervisor_pid"])
            if abs(supervisor.create_time() - info["supervisor_started"]) > 0.01:
                raise ValueError("Supervisor process changed")
        except (psutil.Error, ValueError):
            parser.error("Supervisor has stopped; start it before issuing commands")
        (control / ("stop" if args.action == "stop" else "interrupt-" + args.target)).touch()
        if args.action == "interrupt" and args.target == "gpu":
            (control / "interrupt-gpu2").touch()
        print("Command queued for this project's supervisor:", args.action, args.target)
        return

    import fasteners
    import pgserver
    import psutil

    from pipeline.config import Settings
    from pipeline.engine import Engine

    lock = fasteners.InterProcessLock(str(RUNTIME / "supervisor.lock"))
    if not lock.acquire(blocking=False):
        parser.error("This project already has a supervisor running")
    pg = None
    children: dict[str, subprocess.Popen] = {}
    logs = []
    running = True

    def stop(*unused) -> None:
        nonlocal running
        running = False

    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, stop)
    try:
        for marker in control.iterdir():
            if marker.name in {
                "stop",
                "interrupt-cpu",
                "interrupt-gpu",
                "interrupt-api",
                "interrupt-gpu2",
            }:
                marker.unlink()
        data = RUNTIME / "postgres"
        data.mkdir(exist_ok=True)
        pg = pgserver.get_server(data, cleanup_mode="stop")
        env = {
            **os.environ,
            "DATABASE_URL": pg.get_uri(),
            "ARTIFACT_ROOT": str(RUNTIME / "artifacts"),
        }
        (RUNTIME / "database-url.txt").write_text(pg.get_uri())
        os.chmod(RUNTIME / "database-url.txt", 0o600)
        asyncio.run(Engine(Settings(pg.get_uri(), RUNTIME / "artifacts")).db.initialize())
        commands = {
            "api": [
                sys.executable,
                "-m",
                "uvicorn",
                "pipeline.api:create_app",
                "--factory",
                "--host",
                "127.0.0.1",
                "--port",
                str(args.port),
            ],
            "cpu": [sys.executable, "-m", "pipeline.worker", "--resource", "cpu"],
            "gpu": [sys.executable, "-m", "pipeline.worker", "--resource", "gpu"],
            "gpu2": [sys.executable, "-m", "pipeline.worker", "--resource", "gpu"],
        }

        def start(name: str) -> None:
            path = RUNTIME / "logs"
            path.mkdir(exist_ok=True)
            stream = (path / f"{name}.log").open("a")
            logs.append(stream)
            children[name] = subprocess.Popen(
                commands[name], env=env, cwd=ROOT, stdout=stream, stderr=stream
            )
            state = {
                "url": f"http://127.0.0.1:{args.port}",
                "supervisor_pid": os.getpid(),
                "supervisor_started": psutil.Process().create_time(),
                "children": {key: process.pid for key, process in children.items()},
            }
            (RUNTIME / "runtime.json").write_text(json.dumps(state, indent=2))

        for name in commands:
            start(name)
        print(f"Frameflow ready to connect: http://127.0.0.1:{args.port}", flush=True)
        print("State: .local/ | Stop: Ctrl+C or python scripts/dev.py stop", flush=True)
        while running:
            if (control / "stop").exists():
                (control / "stop").unlink()
                break
            for name, child in list(children.items()):
                marker = control / ("interrupt-" + name)
                if marker.exists():
                    marker.unlink()
                    if child.poll() is None:
                        child.kill()  # Only our existing Popen child, never an arbitrary supplied PID.
                        child.wait(timeout=3)
                    print("Injected SIGKILL:", name, child.pid, flush=True)
                if child.poll() is not None:
                    print("Restarting:", name, flush=True)
                    start(name)
            time.sleep(0.2)
    finally:
        for child in children.values():
            if child.poll() is None:
                child.terminate()
        deadline = time.monotonic() + 4
        for child in children.values():
            try:
                child.wait(timeout=max(0.1, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait(timeout=3)
        for stream in logs:
            stream.close()
        if pg:
            pg.cleanup()
        (RUNTIME / "runtime.json").unlink(missing_ok=True)
        lock.release()


if __name__ == "__main__":
    main()
