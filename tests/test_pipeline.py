import asyncio
import os
import subprocess
import sys
import time
from uuid import uuid4

import httpx
import pytest
from psycopg.types.json import Jsonb

from pipeline.api import create_app
from pipeline.domain import STEPS, TaskRequest, step_fingerprint, validate_dag
from pipeline.engine import Conflict, StaleAttempt
from pipeline.storage.database import one
from pipeline.worker import run_job


async def submit(engine, request):
    return (await engine.create(request, uuid4().hex))["task_id"]


async def drain(engine, task_id, timeout=15):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        await engine.reap()
        for resource in ("cpu", "gpu"):
            job = await engine.claim("test-" + resource, resource)
            if job:
                await run_job(engine, job)
        task = await engine.task(task_id)
        if task["status"] in {"FAILED", "SUCCEEDED"}:
            return task
        await asyncio.sleep(0.015)
    raise AssertionError("Task did not settle")


async def ready_gpu(engine, request):
    task_id = await submit(engine, request)
    # A global CPU queue can also contain another task's thumbnail branch.
    for _ in range(20):
        if by_key(await engine.task(task_id), "stylize")["status"] == "READY":
            break
        job = await engine.claim("cpu", "cpu")
        if job:
            await run_job(engine, job)
    else:
        raise AssertionError("Target task did not reach GPU queue")
    return task_id


def by_key(task, key):
    return next(s for s in task["steps"] if s["key"] == key)


def test_dag_and_semantic_fingerprint():
    validate_dag(STEPS)
    with pytest.raises(ValueError):
        validate_dag([{"key": "a", "depends_on": ["b"]}, {"key": "b", "depends_on": ["a"]}])
    with pytest.raises(ValueError):
        validate_dag([{"key": "a", "depends_on": ["missing"]}])
    p = TaskRequest().model_dump()
    assert step_fingerprint("probe", p, {}) == step_fingerprint(
        "probe", {**p, "model_version": "v2"}, {}
    )
    assert step_fingerprint("stylize", p, {}) != step_fingerprint("stylize", {**p, "seed": 0}, {})
    assert step_fingerprint("stylize", p, {}) == step_fingerprint(
        "stylize", {**p, "step_delay": 8}, {}
    )


async def test_happy_path_real_files_and_dependency_order(engine, fast_request):
    task = await drain(engine, await submit(engine, fast_request))
    assert task["status"] == "SUCCEEDED"
    assert all(await asyncio.gather(*(engine.files.valid(s["artifact"]) for s in task["steps"])))
    import io
    import zipfile

    bundle = await engine.files.read(by_key(task, "package")["artifact"])
    with zipfile.ZipFile(io.BytesIO(bundle)) as z:
        assert set(z.namelist()) == {"animation.gif", "thumbnail.png", "manifest.json"}
    for step in task["steps"]:
        for parent in step["depends_on"]:
            assert (
                step["attempts"][0]["started_at"]
                >= by_key(task, parent)["attempts"][-1]["finished_at"]
            )


async def test_transient_retry_preserves_parents(engine, fast_request):
    task = await drain(
        engine,
        await submit(engine, fast_request.model_copy(update={"fault_mode": "transient_once"})),
    )
    assert task["status"] == "SUCCEEDED"
    assert len(by_key(task, "probe")["attempts"]) == 1
    assert [a["status"] for a in by_key(task, "stylize")["attempts"]] == ["FAILED", "SUCCEEDED"]


@pytest.mark.parametrize(
    "updates", [{"asset": "invalid"}, {"fault_mode": "permanent", "fault_step": "probe"}]
)
async def test_permanent_failure_blocks_without_retry(engine, fast_request, updates):
    task = await drain(engine, await submit(engine, fast_request.model_copy(update=updates)))
    assert task["status"] == "FAILED"
    assert len(task["steps"][0]["attempts"]) == 1
    assert all(s["status"] == "BLOCKED" for s in task["steps"][1:])


async def test_budget_exhaustion_then_manual_retry_preserves_history(engine, fast_request):
    task_id = await submit(engine, fast_request.model_copy(update={"fault_mode": "exhaust_once"}))
    failed = await drain(engine, task_id)
    step = by_key(failed, "stylize")
    assert failed["status"] == "FAILED" and len(step["attempts"]) == 3
    await engine.retry(step["id"])
    with pytest.raises(Conflict):
        await engine.retry(step["id"])
    final = await drain(engine, task_id)
    assert final["status"] == "SUCCEEDED"
    assert len(by_key(final, "stylize")["attempts"]) == 4
    assert len(by_key(final, "thumbnail")["attempts"]) == 1


async def test_cache_reuse_and_model_invalidation(engine, fast_request):
    request = fast_request.model_copy(update={"cache_enabled": True})
    original = await drain(engine, await submit(engine, request))
    reused = await drain(engine, await submit(engine, request))
    assert all(s["status"] == "CACHED" and s["attempts"] == [] for s in reused["steps"])
    assert [s["artifact_id"] for s in original["steps"]] == [
        s["artifact_id"] for s in reused["steps"]
    ]
    changed = await drain(
        engine, await submit(engine, request.model_copy(update={"model_version": "mock-v2"}))
    )
    assert {s["key"] for s in changed["steps"] if s["status"] == "CACHED"} == {
        "probe",
        "extract",
        "thumbnail",
    }


async def test_corrupt_cached_artifact_recomputed(engine, fast_request):
    request = fast_request.model_copy(update={"cache_enabled": True})
    original = await drain(engine, await submit(engine, request))
    engine.files.resolve(by_key(original, "stylize")["artifact"]["path"]).write_bytes(b"corrupted")
    task_id = await submit(engine, request)
    final = await drain(engine, task_id)
    assert final["status"] == "SUCCEEDED" and by_key(final, "stylize")["status"] == "SUCCEEDED"
    assert any(e["type"] == "cache.invalidated" for e in await engine.events(task_id))


async def test_concurrent_claim_and_request_idempotency(engine, fast_request):
    results = await asyncio.gather(*(engine.create(fast_request, "same-key") for _ in range(6)))
    assert len({r["task_id"] for r in results}) == 1
    with pytest.raises(Conflict):
        await engine.create(fast_request.model_copy(update={"seed": 999}), "same-key")
    claims = await asyncio.gather(*(engine.claim(f"cpu-{i}", "cpu") for i in range(6)))
    assert sum(c is not None for c in claims) == 1


async def test_stale_fencing_heartbeat_checkpoint_complete_and_orphan_file(engine, fast_request):
    task_id = await submit(engine, fast_request)
    old = await engine.claim("old", "cpu")
    output = await engine.files.write(b"old result", "old.json", "application/json")
    async with engine.db.connection() as c:
        await c.execute(
            "UPDATE attempts SET lease_expires_at=clock_timestamp()-interval '1 second' WHERE id=%s",
            (old["attempt"]["id"],),
        )
    assert await engine.reap() == 1
    await asyncio.sleep(0.03)
    new = await engine.claim("new", "cpu")
    assert new["step"]["token"] > old["step"]["token"]
    assert not await engine.heartbeat(old)
    with pytest.raises(StaleAttempt):
        await engine.checkpoint(old, {}, 0.5)
    assert not await engine.complete(old, output)
    async with engine.db.connection() as c:
        assert await one(c, "SELECT id FROM artifacts WHERE id=%s", (output["id"],)) is None
    await run_job(engine, new)
    final = await drain(engine, task_id)
    assert final["status"] == "SUCCEEDED"
    assert by_key(final, "probe")["artifact_id"] != output["id"]


async def test_gpu_slot_waiting_capacity_and_release(engine, fast_request):
    first = await ready_gpu(engine, fast_request)
    second = await ready_gpu(engine, fast_request)
    job = await engine.claim("gpu-a", "gpu")
    assert job["task"]["id"] == first
    assert await engine.claim("gpu-b", "gpu") is None
    pending = by_key(await engine.task(second), "stylize")
    assert pending["status"] == "WAITING_RESOURCE" and pending["attempts"] == []
    assert (await engine.resources())["slots"][0]["attempt_id"] == job["attempt"]["id"]
    await engine.fail(job, "test transient")
    assert (await engine.resources())["slots"][0]["attempt_id"] is None
    await drain(engine, first)
    await drain(engine, second)
    oversized = await drain(
        engine, await submit(engine, fast_request.model_copy(update={"gpu_memory_mb": 16384}))
    )
    assert oversized["status"] == "FAILED" and by_key(oversized, "stylize")["attempts"] == []


async def test_attempt_timeout_with_live_heartbeats(engine, fast_request):
    request = fast_request.model_copy(update={"fault_mode": "timeout_once", "attempt_timeout": 0.3})
    task = await drain(engine, await submit(engine, request))
    attempts = by_key(task, "stylize")["attempts"]
    assert task["status"] == "SUCCEEDED"
    assert attempts[0]["status"] == "TIMED_OUT"
    assert attempts[0]["heartbeat_at"] > attempts[0]["started_at"]


async def test_overall_deadline_without_workers(engine, fast_request):
    task_id = await submit(engine, fast_request)
    async with engine.db.connection() as c:
        await c.execute(
            "UPDATE tasks SET deadline=clock_timestamp()-interval '1 second' WHERE id=%s",
            (task_id,),
        )
    await engine.reap()
    task = await engine.task(task_id)
    assert task["status"] == "FAILED"
    with pytest.raises(Conflict):
        await engine.retry(task["steps"][0]["id"])


async def test_corrupt_checkpoint_is_invalidated(engine, fast_request):
    task_id = await ready_gpu(engine, fast_request)
    async with engine.db.connection() as c:
        await c.execute(
            "UPDATE steps SET checkpoint=%s,progress=0.5 WHERE task_id=%s AND key='stylize'",
            (Jsonb({"schema": 999, "frames": [{"path": "missing"}]}), task_id),
        )
    task = await drain(engine, task_id)
    assert task["status"] == "SUCCEEDED"
    assert any(e["type"] == "checkpoint.invalidated" for e in await engine.events(task_id))


async def test_real_worker_sigkill_checkpoint_recovery(engine, settings, fast_request, tmp_path):
    request = fast_request.model_copy(update={"frames": 32, "step_delay": 0.1})
    task_id = await ready_gpu(engine, request)
    env = {
        **os.environ,
        "DATABASE_URL": settings.database_url,
        "ARTIFACT_ROOT": str(settings.artifact_root),
        "LEASE_SECONDS": "1",
        "HEARTBEAT_SECONDS": "0.1",
        "POLL_SECONDS": "0.02",
        "RETRY_BASE_SECONDS": "0.01",
    }
    log = (tmp_path / "worker.log").open("w")
    children = []
    try:
        old = subprocess.Popen(
            [sys.executable, "-m", "pipeline.worker", "--resource", "gpu"],
            env=env,
            stdout=log,
            stderr=log,
        )
        children.append(old)
        for _ in range(250):
            task = await engine.task(task_id)
            step = by_key(task, "stylize")
            if len(step["checkpoint"].get("frames", [])) >= 4:
                break
            assert old.poll() is None, (tmp_path / "worker.log").read_text()
            await asyncio.sleep(0.02)
        else:
            raise AssertionError("Worker never persisted a checkpoint")
        old.kill()
        await asyncio.to_thread(old.wait, 3)
        checkpoint_count = len(
            by_key(await engine.task(task_id), "stylize")["checkpoint"]["frames"]
        )
        assert checkpoint_count < request.frames
        replacement = subprocess.Popen(
            [sys.executable, "-m", "pipeline.worker", "--resource", "gpu"],
            env=env,
            stdout=log,
            stderr=log,
        )
        children.append(replacement)
        deadline = time.monotonic() + 12
        while time.monotonic() < deadline:
            cpu = await engine.claim("cpu-test", "cpu")
            if cpu:
                await run_job(engine, cpu)
            task = await engine.task(task_id)
            if task["status"] == "SUCCEEDED":
                break
            await asyncio.sleep(0.03)
        assert task["status"] == "SUCCEEDED", (tmp_path / "worker.log").read_text()
        attempts = by_key(task, "stylize")["attempts"]
        assert [a["status"] for a in attempts] == ["INTERRUPTED", "SUCCEEDED"]
        assert attempts[1]["resume_from"] == checkpoint_count
        assert attempts[0]["worker_id"] != attempts[1]["worker_id"]
        assert len(by_key(task, "extract")["attempts"]) == 1
    finally:
        for child in children:
            if child.poll() is None:
                child.kill()
            await asyncio.to_thread(child.wait, 3)
        log.close()


async def test_api_restart_history_metrics_and_artifact(engine, settings, fast_request):
    app = create_app(settings)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/tasks", json=fast_request.model_dump(), headers={"Idempotency-Key": "api-test"}
        )
        assert response.status_code == 202
        task_id = response.json()["task_id"]
        invalid = await client.post(
            "/api/tasks", json={"frames": 10000}, headers={"Idempotency-Key": "invalid"}
        )
        assert invalid.status_code == 422
    await drain(engine, task_id)
    # New app instance; no in-process task state survives.
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app(settings)), base_url="http://test"
    ) as client:
        task = (await client.get(f"/api/tasks/{task_id}")).json()
        assert task["status"] == "SUCCEEDED"
        seen, cursor = [], 0
        while True:
            page = (
                await client.get(f"/api/tasks/{task_id}/events?after_seq={cursor}&limit=3")
            ).json()
            if not page["items"]:
                break
            seen.extend(e["seq"] for e in page["items"])
            cursor = page["next_seq"]
        assert seen == list(range(1, task["event_seq"] + 1))
        response = await client.get("/api/artifacts/" + by_key(task, "package")["artifact_id"])
        assert response.status_code == 200 and response.content.startswith(b"PK")
        metrics = (await client.get("/metrics")).text
        assert "pipeline_gpu_mock_busy 0" in metrics and task_id not in metrics
        assert (await client.get("/api/tasks/nonexistent")).status_code == 404
        assert (await client.get("/readyz")).status_code == 200


async def test_event_cursor_waits_for_commit_order(engine, fast_request):
    task_id = await submit(engine, fast_request)
    baseline = (await engine.events(task_id))[-1]["seq"]
    first_written = asyncio.Event()
    allow_commit = asyncio.Event()

    async def first():
        async with engine.db.connection() as c:
            await engine.event(c, task_id, "test.first")
            first_written.set()
            await allow_commit.wait()

    async def second():
        await first_written.wait()
        async with engine.db.connection() as c:
            await engine.event(c, task_id, "test.second")

    first_task, second_task = asyncio.create_task(first()), asyncio.create_task(second())
    try:
        await first_written.wait()
        assert await engine.events(task_id, baseline) == []
        assert not second_task.done()
    finally:
        allow_commit.set()
    await asyncio.gather(first_task, second_task)
    page = await engine.events(task_id, baseline, 1)
    rest = await engine.events(task_id, page[-1]["seq"], 1)
    assert [page[0]["type"], rest[0]["type"]] == ["test.first", "test.second"]
    assert rest[0]["seq"] == page[0]["seq"] + 1


async def test_concurrent_cache_miss_publishes_one_valid_reference(engine, fast_request):
    request = fast_request.model_copy(update={"cache_enabled": True})
    first, second = await submit(engine, request), await submit(engine, request)
    jobs = [await engine.claim("cpu-a", "cpu"), await engine.claim("cpu-b", "cpu")]
    assert {job["task"]["id"] for job in jobs} == {first, second}
    assert jobs[0]["step"]["fingerprint"] == jobs[1]["step"]["fingerprint"]
    await asyncio.gather(*(run_job(engine, job) for job in jobs))
    async with engine.db.connection() as c:
        cached = await one(
            c,
            "SELECT a.* FROM cache_entries ce JOIN artifacts a ON a.id=ce.artifact_id WHERE fingerprint=%s",
            (jobs[0]["step"]["fingerprint"],),
        )
    assert await engine.files.valid(cached)
    for task_id in (first, second):
        assert by_key(await engine.task(task_id), "probe")["status"] == "SUCCEEDED"


async def test_rollback_does_not_publish_event_or_state(engine, fast_request):
    task_id = await submit(engine, fast_request)
    before = await engine.events(task_id)
    with pytest.raises(RuntimeError):
        async with engine.db.connection() as c:
            await c.execute("UPDATE tasks SET status='FAILED' WHERE id=%s", (task_id,))
            await engine.event(c, task_id, "test.rollback")
            raise RuntimeError("transaction aborted")
    assert (await engine.task(task_id))["status"] == "QUEUED"
    assert await engine.events(task_id) == before


async def test_sigkill_after_file_write_before_state_commit(
    engine, settings, fast_request, tmp_path
):
    """Crash a real process at the artifact/publication boundary, not just a stale call."""
    import json

    task_id = await submit(engine, fast_request)
    marker = tmp_path / "unpublished.json"
    env = {
        **os.environ,
        "DATABASE_URL": settings.database_url,
        "ARTIFACT_ROOT": str(settings.artifact_root),
        "LEASE_SECONDS": str(settings.lease_seconds),
        "HEARTBEAT_SECONDS": str(settings.heartbeat_seconds),
        "PUBLISH_MARKER": str(marker),
    }
    program = """
import asyncio, json, os
from pathlib import Path
from pipeline.config import Settings
from pipeline.engine import Engine
from pipeline.pipelines.animation import execute
async def main():
    engine = Engine(Settings.from_env())
    job = await engine.claim("publication-crash", "cpu")
    artifact = await execute(engine, job)
    Path(os.environ["PUBLISH_MARKER"]).write_text(json.dumps(artifact))
    await asyncio.Event().wait()  # Parent kills here, before the complete transaction.
    await engine.complete(job, artifact)
asyncio.run(main())
"""
    with (tmp_path / "publication.log").open("w") as log:
        child = subprocess.Popen([sys.executable, "-c", program], env=env, stdout=log, stderr=log)
        try:
            async with asyncio.timeout(5):
                while not marker.exists():
                    assert child.poll() is None
                    await asyncio.sleep(0.02)
            artifact = json.loads(marker.read_text())
            assert await engine.files.valid(artifact)
            child.kill()
            await asyncio.to_thread(child.wait, 3)
            async with engine.db.connection() as c:
                assert (
                    await one(c, "SELECT id FROM artifacts WHERE id=%s", (artifact["id"],)) is None
                )
            task = await drain(engine, task_id)
            assert task["status"] == "SUCCEEDED"
            step = by_key(task, "probe")
            assert [a["status"] for a in step["attempts"]] == ["INTERRUPTED", "SUCCEEDED"]
            assert step["artifact_id"] != artifact["id"]
            assert await engine.files.valid(artifact)  # An orphan cannot overwrite the new result.
        finally:
            if child.poll() is None:
                child.kill()
            await asyncio.to_thread(child.wait, 3)
