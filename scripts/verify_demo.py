"""Verify a running local supervisor over HTTP, including actual API and worker SIGKILL."""

import argparse
import hashlib
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import httpx

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8765")
    args = parser.parse_args()
    if args.url != "http://127.0.0.1:8765":
        parser.error("This destructive fault demo targets only the default local supervisor")
    client = httpx.Client(base_url=args.url, timeout=3)
    evidence = {"recorded_at": datetime.now(timezone.utc).isoformat(), "checks": []}

    def get(path):
        response = client.get(path)
        response.raise_for_status()
        return response.json()

    def create(**params):
        response = client.post(
            "/api/tasks",
            json={"step_delay": 0.1, **params},
            headers={"Idempotency-Key": uuid4().hex},
        )
        response.raise_for_status()
        return response.json()["task_id"]

    def wait(task_id, predicate, timeout=60):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                task = get("/api/tasks/" + task_id)
                if predicate(task):
                    return task
            except httpx.HTTPError:
                pass
            time.sleep(0.2)
        raise AssertionError("Timed out: " + task_id)

    def terminal(task_id):
        return wait(task_id, lambda t: t["status"] in {"FAILED", "SUCCEEDED"})

    def step(task, key):
        return next(s for s in task["steps"] if s["key"] == key)

    def interrupt(target):
        subprocess.run(
            [sys.executable, "scripts/dev.py", "interrupt", target], cwd=ROOT, check=True
        )

    base = create(title="结果复用 / 原始任务", seed=123)
    assert terminal(base)["status"] == "SUCCEEDED"
    copied = create(title="结果复用 / 全部命中", seed=123)
    task = terminal(copied)
    assert all(s["status"] == "CACHED" and not s["attempts"] for s in task["steps"])
    evidence["checks"].append({"name": "cross_task_cache", "task_id": copied, "cached_steps": 6})
    manual = create(title="手动恢复 / 预算耗尽", cache_enabled=False, fault_mode="exhaust_once")
    failed = terminal(manual)
    assert failed["status"] == "FAILED"
    response = client.post("/api/steps/" + step(failed, "stylize")["id"] + "/retry")
    response.raise_for_status()
    task = terminal(manual)
    assert task["status"] == "SUCCEEDED" and len(step(task, "stylize")["attempts"]) == 4
    evidence["checks"].append({"name": "manual_retry", "task_id": manual, "attempts": 4})
    crash = create(title="进程中断 / 检查点恢复", frames=32, step_delay=0.8, cache_enabled=False)
    before = wait(crash, lambda t: len(step(t, "stylize")["checkpoint"].get("frames", [])) >= 4)
    attempt = step(before, "stylize")["attempts"][-1]
    runtime = json.loads((ROOT / ".local/runtime.json").read_text())
    target = next(
        name
        for name, pid in runtime["children"].items()
        if attempt["worker_id"].startswith(f"gpu-{pid}-")
    )
    saved = len(step(before, "stylize")["checkpoint"]["frames"])
    interrupt(target)
    old_api = runtime["children"]["api"]
    interrupt("api")
    task = terminal(crash)
    history = step(task, "stylize")["attempts"]
    assert task["status"] == "SUCCEEDED"
    assert [a["status"] for a in history] == ["INTERRUPTED", "SUCCEEDED"]
    assert history[-1]["resume_from"] >= saved
    assert history[0]["worker_id"] != history[-1]["worker_id"]
    assert len(step(task, "probe")["attempts"]) == 1
    assert json.loads((ROOT / ".local/runtime.json").read_text())["children"]["api"] != old_api
    artifact = step(task, "package")["artifact"]
    body = client.get("/api/artifacts/" + artifact["id"]).content
    assert hashlib.sha256(body).hexdigest() == artifact["sha256"]
    evidence["checks"].append(
        {
            "name": "real_worker_and_api_sigkill",
            "task_id": crash,
            "saved_frames": saved,
            "attempts": history,
            "bundle_sha256": artifact["sha256"],
        }
    )
    evidence["passed"] = True
    path = ROOT / "docs/evidence/live-demo.json"
    path.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n")
    print("PASS: cache, manual retry, real worker/API SIGKILL, checkpoint and download integrity")
    print(path.relative_to(ROOT))


if __name__ == "__main__":
    main()
