"""An independent worker process. Restarting it never resets persistent steps."""

import argparse
import asyncio
import contextlib
import json
import logging
import os
import signal
from uuid import uuid4

import psycopg

from pipeline.config import Settings
from pipeline.engine import Engine, StaleAttempt
from pipeline.pipelines.animation import PermanentFailure, execute

log = logging.getLogger("pipeline.worker")


async def run_job(engine: Engine, job: dict) -> None:
    async def heartbeat() -> None:
        while True:
            await asyncio.sleep(engine.settings.heartbeat_seconds)
            if not await engine.heartbeat(job):
                raise StaleAttempt()

    compute = asyncio.create_task(execute(engine, job))
    renewal = asyncio.create_task(heartbeat())
    try:
        done, _ = await asyncio.wait(
            {compute, renewal},
            return_when=asyncio.FIRST_COMPLETED,
            timeout=job["task"]["params"]["attempt_timeout"],
        )
        if not done:
            # The DB reaper classifies the elapsed deadline and applies retry policy.
            await engine.reap()
            return
        if renewal in done:
            await renewal  # Failure means ownership is lost; do not publish compute output.
        result = await compute
        await engine.complete(job, result)
    except PermanentFailure as exc:
        await engine.fail(job, str(exc), retryable=False, error_type="PERMANENT")
    except StaleAttempt:
        log.warning(
            json.dumps({"event_type": "worker.ownership_lost", "attempt_id": job["attempt"]["id"]})
        )
    except psycopg.Error:
        # We cannot report success or safely renew ownership without the database.
        log.exception("Database unavailable; abandon attempt until lease recovery")
    except Exception as exc:
        await engine.fail(job, str(exc), error_type=type(exc).__name__)
    finally:
        for task in (compute, renewal):
            task.cancel()
        await asyncio.gather(compute, renewal, return_exceptions=True)


async def serve(settings: Settings, resource: str, stop: asyncio.Event) -> None:
    engine = Engine(settings)
    await engine.db.initialize()
    worker_id = f"{resource}-{os.getpid()}-{uuid4().hex[:6]}"
    while not stop.is_set():
        try:
            await engine.register(worker_id, resource)
            await engine.reap()
            job = await engine.claim(worker_id, resource)
            if job:
                await run_job(engine, job)
            else:
                with contextlib.suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(stop.wait(), settings.poll_seconds)
        except psycopg.Error:
            log.exception("Worker database connection failed; retrying")
            await asyncio.sleep(1)


async def main_async(resource: str) -> None:
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    await serve(Settings.from_env(), resource, stop)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--resource", choices=["cpu", "gpu"], required=True)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    asyncio.run(main_async(args.resource))


if __name__ == "__main__":
    main()
