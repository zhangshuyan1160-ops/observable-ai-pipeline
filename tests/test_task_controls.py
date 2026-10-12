"""Queue liveness, cancellation and history controls against real PostgreSQL."""

import asyncio
from uuid import uuid4

import httpx
import pytest

from pipeline.api import create_app
from pipeline.engine import Conflict
from pipeline.storage.database import one
from pipeline.worker import run_job


async def submit(engine, request):
    return (await engine.create(request, uuid4().hex))["task_id"]


async def test_cpu_claim_reaches_past_32_gpu_waiters(engine, fast_request):
    # Real predecessor results; leave older tasks awaiting GPU with no CPU-ready work.
    for _ in range(33):
        await submit(engine, fast_request)
        for _ in range(3):
            job = await engine.claim("cpu", "cpu")
            assert job
            await run_job(engine, job)
    newest = await submit(engine, fast_request)
    job = await engine.claim("cpu", "cpu")
    assert job is not None, "Older GPU-only tasks must not hide a later ready CPU step"
    assert job["task"]["id"] == newest and job["step"]["key"] == "probe"


async def test_claim_skips_32_future_retries(engine, fast_request):
    ids = [await submit(engine, fast_request) for _ in range(33)]
    async with engine.db.connection() as c:
        await c.execute(
            "UPDATE steps SET status='RETRY_WAIT',next_run_at=clock_timestamp()+interval '1 hour' "
            "WHERE key='probe' AND task_id=ANY(%s)",
            (ids,),
        )
    newest = await submit(engine, fast_request)
    assert (await engine.claim("cpu", "cpu"))["task"]["id"] == newest


async def test_cancel_queued_is_idempotent_and_never_claimed(engine, fast_request):
    task_id = await submit(engine, fast_request)
    results = await asyncio.gather(*(engine.cancel(task_id) for _ in range(5)))
    assert all(r["status"] == "CANCELLED" for r in results)
    task = await engine.task(task_id)
    assert all(s["status"] == "CANCELLED" and not s["attempts"] for s in task["steps"])
    assert not task["cancel_unconfirmed"]
    assert await engine.claim("cpu", "cpu") is None
    assert sum(e["type"] == "task.cancel_requested" for e in await engine.events(task_id)) == 1


async def test_running_cancel_waits_for_worker_and_preserves_checkpoint(engine, fast_request):
    task_id = await submit(
        engine, fast_request.model_copy(update={"frames": 16, "batch_delay": 0.12})
    )
    for _ in range(2):
        await run_job(engine, await engine.claim("cpu", "cpu"))
    job = await engine.claim("gpu", "gpu")
    running = asyncio.create_task(run_job(engine, job))
    try:
        async with asyncio.timeout(4):
            while True:
                task = await engine.task(task_id)
                step = next(s for s in task["steps"] if s["key"] == "stylize")
                if step["checkpoint"]:
                    break
                await asyncio.sleep(0.02)
        assert (await engine.cancel(task_id))["status"] == "CANCELLING"
        assert (await engine.resources())["slots"][0]["attempt_id"] == job["attempt"]["id"]
        await asyncio.wait_for(running, 3)
        task = await engine.task(task_id)
        step = next(s for s in task["steps"] if s["key"] == "stylize")
        assert task["status"] == "CANCELLED" and not task["cancel_unconfirmed"]
        assert step["attempts"][-1]["status"] == "CANCELLED" and step["checkpoint"]
        assert all(s["status"] == "SUCCEEDED" for s in task["steps"][:2])
        assert (await engine.resources())["slots"][0]["attempt_id"] is None
        artifact = await engine.files.write(b"late", "late.txt", "text/plain")
        assert not await engine.complete(job, artifact)
        with pytest.raises(Conflict):
            await engine.retry(step["id"])
    finally:
        running.cancel()
        await asyncio.gather(running, return_exceptions=True)


async def test_cancel_lost_worker_is_explicitly_unconfirmed(engine, fast_request):
    task_id = await submit(engine, fast_request)
    job = await engine.claim("lost", "cpu")
    await engine.cancel(task_id)
    async with engine.db.connection() as c:
        await c.execute(
            "UPDATE attempts SET lease_expires_at=clock_timestamp()-interval '1 second' WHERE id=%s",
            (job["attempt"]["id"],),
        )
    await engine.reap()
    task = await engine.task(task_id)
    assert task["status"] == "CANCELLED" and task["cancel_unconfirmed"]
    assert task["steps"][0]["attempts"][0]["error_type"] == "CANCEL_WORKER_LOST"
    assert not await engine.acknowledge_cancel(job)
    assert await engine.claim("cpu", "cpu") is None


async def test_complete_races_cancel_without_publishing_after_cancel(engine, fast_request):
    for _ in range(8):
        task_id = await submit(engine, fast_request)
        job = await engine.claim("cpu", "cpu")
        artifact = await engine.files.write(b"result", "result.json", "application/json")
        await asyncio.gather(engine.complete(job, artifact), engine.cancel(task_id))
        await engine.acknowledge_cancel(job)
        task = await engine.task(task_id)
        assert task["status"] == "CANCELLED"
        events = await engine.events(task_id)
        cancelled = next(e["seq"] for e in events if e["type"] == "task.cancel_requested")
        assert not any(e["type"] == "attempt.succeeded" and e["seq"] > cancelled for e in events)
        assert len(task["steps"][0]["attempts"]) == 1


async def test_history_search_pagination_archive_and_api_controls(engine, settings, fast_request):
    ids = [
        await submit(engine, fast_request.model_copy(update={"title": f"history {i:02d}"}))
        for i in range(37)
    ]
    special = await submit(engine, fast_request.model_copy(update={"title": "literal %_search"}))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app(settings)), base_url="http://test"
    ) as client:
        seen, cursor = [], None
        while True:
            params = {"q": "history", "limit": 7}
            if cursor:
                params["before"] = cursor
            page = (await client.get("/api/tasks", params=params)).json()
            seen.extend(r["id"] for r in page["items"])
            cursor = page["next_cursor"]
            if not cursor:
                break
        assert seen == list(reversed(ids))
        assert (await client.get("/api/tasks", params={"q": "%_"})).json()["items"][0][
            "id"
        ] == special
        assert (await client.get("/api/tasks", params={"q": ids[0][:12]})).json()["items"][0][
            "id"
        ] == ids[0]
        assert (await client.post(f"/api/tasks/{ids[0]}/archive")).status_code == 409
        assert (await client.post(f"/api/tasks/{ids[0]}/cancel")).json()["status"] == "CANCELLED"
        assert (await client.get("/api/tasks", params={"status": "CANCELLED"})).json()["items"][0][
            "id"
        ] == ids[0]
        assert (await client.post(f"/api/tasks/{ids[0]}/archive")).status_code == 200
        assert not (await client.get("/api/tasks", params={"q": ids[0]})).json()["items"]
        assert (await client.get("/api/tasks", params={"view": "archived"})).json()["items"][0][
            "id"
        ] == ids[0]
        assert (await client.get(f"/api/tasks/{ids[0]}")).json()["params"]["title"] == "history 00"
        assert (await client.post(f"/api/tasks/{ids[0]}/archive?archived=false")).status_code == 200
        assert not (await client.get("/api/tasks", params={"view": "archived"})).json()["items"]
        assert (await client.get("/api/tasks?before=missing")).status_code == 422
        assert (await client.post("/api/tasks/missing/cancel")).status_code == 404
        assert (await client.get("/api/tasks?status=bogus")).status_code == 422
    # Re-running initialization must preserve old records and migrate only once.
    await engine.db.initialize()
    assert (await engine.task(ids[0]))["status"] == "CANCELLED"
    async with engine.db.connection() as c:
        assert (await one(c, "SELECT count(*) AS n FROM schema_version"))["n"] == 2


async def test_cancellation_waits_for_blocking_thread_before_ack(engine, fast_request, monkeypatch):
    import threading

    import pipeline.worker as worker
    from pipeline.blocking import run_blocking

    started, release = threading.Event(), threading.Event()

    def blocking_operation():
        started.set()
        assert release.wait(5)

    async def compute(*_):
        await run_blocking(blocking_operation)

    monkeypatch.setattr(worker, "execute", compute)
    task_id = await submit(engine, fast_request)
    job = await engine.claim("cpu", "cpu")
    running = asyncio.create_task(worker.run_job(engine, job))
    try:
        assert await asyncio.to_thread(started.wait, 2)
        await engine.cancel(task_id)
        await asyncio.sleep(0.3)  # Longer than heartbeat; thread is deliberately still alive.
        assert (await engine.task(task_id))["status"] == "CANCELLING"
        assert not running.done()
        release.set()
        await asyncio.wait_for(running, 3)
        assert (await engine.task(task_id))["status"] == "CANCELLED"
    finally:
        release.set()
        await asyncio.gather(running, return_exceptions=True)


async def test_v1_migration_preserves_history_and_idempotency(settings, fast_request):
    from pathlib import Path

    from psycopg.types.json import Jsonb

    import pipeline.storage.database as database
    from pipeline.domain import fingerprint
    from pipeline.engine import Engine

    engine = Engine(settings)
    async with engine.db.connection() as c:
        await c.execute(Path(database.__file__).with_name("schema.sql").read_text())
    task_id = (await engine.create(fast_request, "old-key"))["task_id"]
    old_params = fast_request.model_dump(exclude_none=True)
    async with engine.db.connection() as c:
        await c.execute(
            "UPDATE tasks SET params=%s,request_hash=%s WHERE id=%s",
            (Jsonb(old_params), fingerprint(old_params), task_id),
        )
    await asyncio.gather(engine.db.initialize(), engine.db.initialize())
    assert (await engine.create(fast_request, "old-key"))["task_id"] == task_id
    task = await engine.task(task_id)
    assert task["status"] == "QUEUED" and task["archived_at"] is None
    assert [e["type"] for e in await engine.events(task_id)] == ["task.created"]
    await engine.cancel(task_id)
    assert (await engine.task(task_id))["status"] == "CANCELLED"


async def test_cancel_gpu_waiter_does_not_release_owners_slot(engine, fast_request):
    ids = [await submit(engine, fast_request) for _ in range(2)]
    for _ in range(6):
        await run_job(engine, await engine.claim("cpu", "cpu"))
    owner = await engine.claim("gpu-owner", "gpu")
    assert owner["task"]["id"] == ids[0]
    assert await engine.claim("gpu-waiter", "gpu") is None
    waiting = await engine.task(ids[1])
    assert (
        next(s for s in waiting["steps"] if s["key"] == "stylize")["status"] == "WAITING_RESOURCE"
    )
    assert (await engine.cancel(ids[1]))["status"] == "CANCELLED"
    assert (await engine.resources())["slots"][0]["attempt_id"] == owner["attempt"]["id"]
    step = next(s for s in (await engine.task(ids[1]))["steps"] if s["key"] == "stylize")
    assert not step["attempts"]
    await run_job(engine, owner)
    assert (await engine.resources())["slots"][0]["attempt_id"] is None


async def test_cancel_retry_wait_does_not_requeue(engine, fast_request):
    task_id = await submit(engine, fast_request)
    job = await engine.claim("cpu", "cpu")
    await engine.fail(job, "temporary outage")
    assert (await engine.task(task_id))["steps"][0]["status"] == "RETRY_WAIT"
    await engine.cancel(task_id)
    await engine.reap()
    assert await engine.claim("cpu", "cpu") is None
    task = await engine.task(task_id)
    assert task["status"] == "CANCELLED" and len(task["steps"][0]["attempts"]) == 1
