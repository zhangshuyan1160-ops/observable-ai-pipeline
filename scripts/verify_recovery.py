"""Own an isolated API/CPU/GPU/PostgreSQL stack and inject real failures.

Default is a short rehearsal; --duration 3600 runs one task for roughly an hour.
Only this script's children/database are stopped. Evidence excludes DB credentials.
"""

import argparse
import asyncio
import hashlib
import json
import os
import socket
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import httpx
import pgserver

ROOT = Path(__file__).resolve().parents[1]


async def verify(duration: float, output: Path) -> None:
    run = ROOT / ".local" / ("recovery-" + uuid4().hex[:10])
    run.mkdir(parents=True)
    server = pgserver.get_server(run / "postgres", cleanup_mode="stop")
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    base = f"http://127.0.0.1:{port}"
    env = {
        **os.environ,
        "DATABASE_URL": server.get_uri(),
        "ARTIFACT_ROOT": str(run / "artifacts"),
        "LEASE_SECONDS": "1.5",
        "HEARTBEAT_SECONDS": "0.2",
        "POLL_SECONDS": "0.05",
        "RETRY_BASE_SECONDS": "0.05",
    }
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
            str(port),
        ],
        "cpu": [sys.executable, "-m", "pipeline.worker", "--resource", "cpu"],
        "gpu": [sys.executable, "-m", "pipeline.worker", "--resource", "gpu"],
    }
    children, streams = {}, []
    evidence = {
        "started_at": datetime.now(UTC).isoformat(),
        "requested_compute_seconds": duration,
        "isolated_runtime": str(run.relative_to(ROOT)),
        "injections": [],
        "status": "RUNNING",
    }
    start = time.monotonic()

    def save():
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n")

    def launch():
        for name, command in commands.items():
            stream = (run / (name + ".log")).open("a")
            streams.append(stream)
            children[name] = subprocess.Popen(
                command, cwd=ROOT, env=env, stdout=stream, stderr=stream
            )

    def kill_children():
        for child in children.values():
            if child.poll() is None:
                child.kill()
        for child in children.values():
            child.wait(timeout=5)

    try:
        launch()
        async with httpx.AsyncClient(base_url=base, timeout=4) as client:

            async def ready():
                async with asyncio.timeout(20):
                    while True:
                        try:
                            if (await client.get("/readyz")).status_code == 200:
                                return
                        except httpx.HTTPError:
                            pass
                        await asyncio.sleep(0.15)

            await ready()
            response = await client.post(
                "/api/tasks",
                headers={"Idempotency-Key": uuid4().hex},
                json={
                    "title": "独立故障恢复演练",
                    "frames": 64,
                    "step_delay": 0.01,
                    "batch_delay": max(0.15, duration / 16),
                    "cache_enabled": False,
                    "attempt_timeout": min(14400, duration + 300),
                    "task_timeout": min(14400, duration * 1.5 + 300),
                    "max_attempts": 5,
                },
            )
            response.raise_for_status()
            task_id = response.json()["task_id"]
            evidence["task_id"] = task_id
            save()
            injected, last_report = 0, 0
            async with asyncio.timeout(duration * 1.5 + 120):
                while True:
                    task = (await client.get("/api/tasks/" + task_id)).json()
                    step = next(s for s in task["steps"] if s["key"] == "stylize")
                    frames = len(step["checkpoint"].get("frames", []))
                    if (injected == 0 and frames >= 4) or (injected == 1 and frames >= 8):
                        before = {
                            "frames": frames,
                            "attempt_id": step["current_attempt_id"],
                            "pids": {n: p.pid for n, p in children.items()},
                        }
                        if injected == 1:
                            kill_children()
                        server.cleanup()  # Actual server stop; no monkeypatching DB calls.
                        if injected == 0:
                            assert (await client.get("/readyz")).status_code == 503
                            assert all(p.poll() is None for p in children.values())
                        await asyncio.sleep(
                            2.2
                        )  # Longer than the lease; all application connections fail.
                        server = pgserver.get_server(run / "postgres", cleanup_mode="stop")
                        assert server.get_uri() == env["DATABASE_URL"]
                        if injected == 1:
                            launch()
                        await ready()
                        evidence["injections"].append(
                            {
                                "type": "database_outage"
                                if injected == 0
                                else "full_stack_restart",
                                **before,
                            }
                        )
                        injected += 1
                        save()
                        print(json.dumps(evidence["injections"][-1]), flush=True)
                    if task["status"] in {"SUCCEEDED", "FAILED", "CANCELLED"}:
                        assert task["status"] == "SUCCEEDED", task
                        break
                    if time.monotonic() - last_report > 60:
                        evidence["elapsed_seconds"] = round(time.monotonic() - start, 2)
                        evidence["last_checkpoint_frames"] = frames
                        save()
                        print(
                            json.dumps({"elapsed": evidence["elapsed_seconds"], "frames": frames}),
                            flush=True,
                        )
                        last_report = time.monotonic()
                    await asyncio.sleep(0.1 if duration < 60 else 1)
            assert injected == 2
            attempts = step["attempts"]
            assert [a["status"] for a in attempts] == ["INTERRUPTED", "INTERRUPTED", "SUCCEEDED"], (
                attempts
            )
            assert [a["resume_from"] for a in attempts[1:]] == [
                i["frames"] for i in evidence["injections"]
            ]
            assert all(len(s["attempts"]) == 1 for s in task["steps"] if s["key"] != "stylize")
            artifact = next(s for s in task["steps"] if s["key"] == "package")["artifact"]
            result = await client.get("/api/artifacts/" + artifact["id"])
            result.raise_for_status()
            assert hashlib.sha256(result.content).hexdigest() == artifact["sha256"]
            events = (await client.get(f"/api/tasks/{task_id}/events?limit=500")).json()["items"]
            assert [e["seq"] for e in events] == list(range(1, task["event_seq"] + 1))
            assert not (await client.get("/api/resources")).json()["slots"][0]["attempt_id"]
            evidence.update(
                status="PASSED",
                elapsed_seconds=round(time.monotonic() - start, 2),
                attempts=[
                    {
                        k: a[k]
                        for k in ("status", "resume_from", "worker_id", "started_at", "finished_at")
                    }
                    for a in attempts
                ],
                event_count=len(events),
                artifact_sha256=artifact["sha256"],
                completed_at=datetime.now(UTC).isoformat(),
            )
    except BaseException as exc:
        evidence.update(status="FAILED", error=f"{type(exc).__name__}: {exc}")
        raise
    finally:
        kill_children()
        server.cleanup()
        for stream in streams:
            stream.close()
        save()
    print(
        json.dumps(
            {
                "status": evidence["status"],
                "elapsed_seconds": evidence["elapsed_seconds"],
                "evidence": str(output),
            }
        ),
        flush=True,
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--duration", type=float, default=8)
    parser.add_argument("--output", type=Path, default=ROOT / "docs/evidence/recovery-short.json")
    args = parser.parse_args()
    if not 2 <= args.duration <= 7200:
        parser.error("duration must be 2..7200 seconds")
    asyncio.run(verify(args.duration, args.output))
