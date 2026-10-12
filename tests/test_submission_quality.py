"""Behavioral checks for the submission-quality iteration, with real PostgreSQL."""

import asyncio
import json
import os
import shutil
import socket
import sys
from pathlib import Path

import httpx
import pytest
from test_pipeline import by_key, drain, submit

from pipeline.storage.database import one


async def test_browser_intent_lost_response_keeps_one_database_task(engine):
    node = os.getenv("NODE_BIN") or shutil.which("node")
    if not node:
        pytest.skip("Node 22+ needed for the frontend/real-HTTP contract test; CI installs it")
    # Keep a reserved socket open until uvicorn inherits it (no bind-then-close race).
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        port = listener.getsockname()[1]
        env = {
            **os.environ,
            "DATABASE_URL": engine.settings.database_url,
            "ARTIFACT_ROOT": str(engine.settings.artifact_root),
        }
        server = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "uvicorn",
            "pipeline.api:create_app",
            "--factory",
            "--fd",
            str(listener.fileno()),
            "--log-level",
            "error",
            env=env,
            pass_fds=(listener.fileno(),),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            async with httpx.AsyncClient() as client:
                for _ in range(100):
                    try:
                        response = await client.get(f"http://127.0.0.1:{port}/readyz", timeout=0.2)
                        if response.status_code == 200:
                            break
                    except httpx.HTTPError:
                        pass
                    await asyncio.sleep(0.05)
                else:
                    raise AssertionError("Isolated API failed to start")
            child = await asyncio.create_subprocess_exec(
                node,
                str(Path(__file__).parent / "frontend/lost-response.mjs"),
                f"http://127.0.0.1:{port}",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                stdout, stderr = await asyncio.wait_for(child.communicate(), timeout=20)
            finally:
                if child.returncode is None:
                    child.kill()
                    await child.wait()
            assert child.returncode == 0, stderr.decode()
            evidence = json.loads(stdout)
            async with engine.db.connection() as c:
                assert (await one(c, "SELECT count(*) AS n FROM tasks"))["n"] == 1
                assert (await one(c, "SELECT count(*) AS n FROM events WHERE type='task.created'"))[
                    "n"
                ] == 1
            task = await engine.task(evidence["task_id"])
            assert task["params"]["seed"] == 17
            assert task["params"]["frames"] == 7
            assert evidence["conflict_status"] == 409
        finally:
            if server.returncode is None:
                server.terminate()
            await asyncio.wait_for(server.communicate(), timeout=10)


async def test_cache_lineage_uses_producing_attempt_and_historical_hit(engine, fast_request):
    request = fast_request.model_copy(update={"cache_enabled": True})
    source = await drain(engine, await submit(engine, request))
    second = await drain(engine, await submit(engine, request))
    third = await drain(engine, await submit(engine, request))
    for task in (second, third):
        assert all(s["status"] == "CACHED" and not s["attempts"] for s in task["steps"])
        for step in task["steps"]:
            assert step["cache_lineage"]["task_id"] == source["id"]
            assert step["cache_lineage"]["step_key"] == step["key"]
            assert step["cache_lineage"]["validated_at"] is not None
            assert step["artifact"]["id"] == by_key(source, step["key"])["artifact_id"]
    # Missing now must not erase historical provenance or claim fresh validation.
    artifact = by_key(third, "compose")["artifact"]
    engine.files.resolve(artifact["path"]).unlink()
    assert not await engine.files.valid(artifact)
    historical = by_key(await engine.task(third["id"]), "compose")
    assert historical["cache_lineage"] == by_key(third, "compose")["cache_lineage"]
