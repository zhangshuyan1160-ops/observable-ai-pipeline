"""Durable execution protocol. Lock order: task -> step -> GPU slot.

Every state transition and its event commit together. Computation is deliberately
outside transactions. Lease ownership never implies exactly-once computation.
"""

import json
import logging
import os
import random
from uuid import uuid4

from psycopg.types.json import Jsonb

from pipeline.config import Settings
from pipeline.domain import (
    STEPS,
    SUCCESS,
    TASK_TERMINAL,
    TERMINAL,
    TaskRequest,
    fingerprint,
    step_fingerprint,
)
from pipeline.storage.artifacts import ArtifactStore
from pipeline.storage.database import Database, many, one

log = logging.getLogger("pipeline")


class Conflict(Exception):
    """An idempotency key or operation conflicts with current persistent state."""


class NotFound(Exception):
    """Requested persistent object does not exist."""


class StaleAttempt(Exception):
    """The worker no longer owns this step."""


class Engine:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.db = Database(settings)
        self.files = ArtifactStore(settings.artifact_root)

    async def event(
        self,
        c,
        task_id: str,
        kind: str,
        step: dict | None = None,
        payload: dict | None = None,
        attempt_id: str | None = None,
    ) -> None:
        row = await one(
            c,
            """UPDATE tasks SET event_seq=event_seq+1,
            updated_at=clock_timestamp() WHERE id=%s RETURNING event_seq""",
            (task_id,),
        )
        await c.execute(
            """INSERT INTO events(task_id,seq,type,step_key,attempt_id,payload)
            VALUES (%s,%s,%s,%s,%s,%s)""",
            (
                task_id,
                row["event_seq"],
                kind,
                step["key"] if step else None,
                attempt_id or (step.get("current_attempt_id") if step else None),
                Jsonb(payload or {}),
            ),
        )
        log.info(
            json.dumps(
                {
                    "event_type": kind,
                    "task_id": task_id,
                    "step_key": step["key"] if step else None,
                    "attempt_id": attempt_id or (step.get("current_attempt_id") if step else None),
                    "entry_point": "engine",
                    **(payload or {}),
                },
                default=str,
            )
        )

    async def create(self, request: TaskRequest, idempotency_key: str) -> dict:
        params = request.model_dump(exclude_none=True)
        task_id = uuid4().hex
        digest = fingerprint(params)
        async with self.db.connection() as c:
            row = await one(
                c,
                """INSERT INTO tasks
                (id,idempotency_key,request_hash,params,definition,status,deadline)
                VALUES (%s,%s,%s,%s,%s,'QUEUED',clock_timestamp()+%s*interval '1 second')
                ON CONFLICT(idempotency_key) DO NOTHING RETURNING id""",
                (
                    task_id,
                    idempotency_key,
                    digest,
                    Jsonb(params),
                    Jsonb(STEPS),
                    request.task_timeout,
                ),
            )
            if row is None:
                existing = await one(
                    c, "SELECT * FROM tasks WHERE idempotency_key=%s", (idempotency_key,)
                )
                if existing["request_hash"] != digest:
                    raise Conflict("相同 Idempotency-Key 对应了不同请求")
                return {"task_id": existing["id"], "reused_request": True}
            for ordinal, spec in enumerate(STEPS):
                await c.execute(
                    """INSERT INTO steps
                    (id,task_id,key,ordinal,label,depends_on,resource,status)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (
                        uuid4().hex,
                        task_id,
                        spec["key"],
                        ordinal,
                        spec["label"],
                        Jsonb(spec["depends_on"]),
                        spec["resource"],
                        "READY" if not spec["depends_on"] else "PENDING",
                    ),
                )
            await self.event(c, task_id, "task.created")
        return {"task_id": task_id, "reused_request": False}

    async def task(self, task_id: str) -> dict:
        async with self.db.connection() as c:
            # One consistent snapshot for the UI, including all child rows.
            await c.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            task = await one(c, "SELECT * FROM tasks WHERE id=%s", (task_id,))
            if task is None:
                raise NotFound(task_id)
            task.pop("idempotency_key")
            task.pop("request_hash")
            task["steps"] = await many(
                c, "SELECT * FROM steps WHERE task_id=%s ORDER BY ordinal", (task_id,)
            )
            for step in task["steps"]:
                step["attempts"] = await many(
                    c, "SELECT * FROM attempts WHERE step_id=%s ORDER BY started_at", (step["id"],)
                )
                step["artifact"] = await one(
                    c, "SELECT * FROM artifacts WHERE id=%s", (step["artifact_id"],)
                )
                step["cache_lineage"] = None
                if step["status"] == "CACHED" and step["artifact"]:
                    # Follow the artifact's producing attempt, not the mutable cache index.
                    # validated_at is historical evidence, never a fresh file health claim.
                    step["cache_lineage"] = await one(
                        c,
                        """SELECT a.task_id,t.params->>'title' AS title,s.key AS step_key,
                        (SELECT e.created_at FROM events e WHERE e.task_id=%s
                         AND e.step_key=%s AND e.type='step.cached'
                         AND e.payload->>'artifact_id'=%s
                         ORDER BY e.seq DESC LIMIT 1) AS validated_at
                        FROM attempts a JOIN tasks t ON t.id=a.task_id
                        JOIN steps s ON s.id=a.step_id WHERE a.id=%s""",
                        (task_id, step["key"], step["artifact_id"], step["artifact"]["attempt_id"]),
                    )
            statuses = {s["status"] for s in task["steps"]}
            task["phase"] = (
                "cancelling"
                if task["status"] == "CANCELLING"
                else "executing"
                if "RUNNING" in statuses
                else "retrying"
                if "RETRY_WAIT" in statuses
                else "waiting_resource"
                if "WAITING_RESOURCE" in statuses
                else task["status"].lower()
            )
            task["progress"] = round(sum(s["progress"] for s in task["steps"]) / len(STEPS), 3)
            return task

    async def events(self, task_id: str, after: int = 0, limit: int = 100) -> list[dict]:
        async with self.db.connection() as c:
            if not await one(c, "SELECT id FROM tasks WHERE id=%s", (task_id,)):
                raise NotFound(task_id)
            return await many(
                c,
                "SELECT * FROM events WHERE task_id=%s AND seq>%s ORDER BY seq LIMIT %s",
                (task_id, after, limit),
            )

    async def refresh(self, c, task_id: str) -> None:
        """Called with task lock held. Topological order propagates blocking in one pass."""
        task = await one(c, "SELECT status FROM tasks WHERE id=%s", (task_id,))
        steps = await many(c, "SELECT * FROM steps WHERE task_id=%s ORDER BY ordinal", (task_id,))
        if task["status"] in {"CANCELLING", "CANCELLED"}:
            if task["status"] == "CANCELLING" and all(s["status"] in TERMINAL for s in steps):
                await c.execute(
                    "UPDATE tasks SET status='CANCELLED',cancelled_at=clock_timestamp() WHERE id=%s",
                    (task_id,),
                )
                await self.event(c, task_id, "task.cancelled")
            return
        states = {s["key"]: s["status"] for s in steps}
        for step in steps:
            if step["status"] not in {"PENDING", "BLOCKED"}:
                continue
            deps = [states[k] for k in step["depends_on"]]
            status = (
                "BLOCKED"
                if any(d in {"FAILED", "BLOCKED"} for d in deps)
                else "READY"
                if all(d in SUCCESS for d in deps)
                else "PENDING"
            )
            if status != step["status"]:
                await c.execute("UPDATE steps SET status=%s WHERE id=%s", (status, step["id"]))
                await self.event(c, task_id, "step." + status.lower(), step)
                step["status"] = status
                states[step["key"]] = status
        if all(s["status"] in SUCCESS for s in steps):
            status = "SUCCEEDED"
        elif all(s["status"] in TERMINAL for s in steps):
            status = "FAILED"
        else:
            status = task["status"]
        if status != task["status"]:
            await c.execute("UPDATE tasks SET status=%s WHERE id=%s", (status, task_id))
            await self.event(c, task_id, "task." + status.lower())

    async def register(self, worker_id: str, resource: str) -> None:
        async with self.db.connection() as c:
            await c.execute(
                """INSERT INTO workers(id,resource,pid) VALUES (%s,%s,%s)
                ON CONFLICT(id) DO UPDATE SET heartbeat_at=clock_timestamp(),pid=EXCLUDED.pid""",
                (worker_id, resource, os.getpid()),
            )

    async def claim(self, worker_id: str, resource: str) -> dict | None:
        async with self.db.connection() as c:
            # Short task locks serialize state/event publication, never computation.
            tasks = await many(
                c,
                """SELECT * FROM tasks t WHERE status IN ('QUEUED','RUNNING')
                AND deadline>clock_timestamp() AND EXISTS(
                    SELECT 1 FROM steps s WHERE s.task_id=t.id AND s.resource=%s
                    AND s.status IN ('READY','WAITING_RESOURCE','RETRY_WAIT')
                    AND s.next_run_at<=clock_timestamp())
                ORDER BY created_at,id FOR UPDATE OF t SKIP LOCKED LIMIT 32""",
                (resource,),
            )
            for task in tasks:
                await self.refresh(c, task["id"])
                steps = await many(
                    c,
                    """SELECT * FROM steps WHERE task_id=%s AND resource=%s
                    AND status IN ('READY','WAITING_RESOURCE','RETRY_WAIT')
                    AND next_run_at<=clock_timestamp() ORDER BY ordinal FOR UPDATE""",
                    (task["id"], resource),
                )
                for step in steps:
                    upstream = {}
                    for parent in step["depends_on"]:
                        upstream[parent] = await one(
                            c,
                            """SELECT a.* FROM artifacts a JOIN steps s
                            ON s.artifact_id=a.id WHERE s.task_id=%s AND s.key=%s
                            AND s.status IN ('SUCCEEDED','CACHED')""",
                            (task["id"], parent),
                        )
                    if any(v is None for v in upstream.values()):
                        continue
                    fp = step_fingerprint(step["key"], task["params"], upstream)
                    if task["params"]["cache_enabled"]:
                        cached = await one(
                            c,
                            """SELECT a.* FROM cache_entries ce JOIN artifacts a
                            ON a.id=ce.artifact_id WHERE ce.fingerprint=%s""",
                            (fp,),
                        )
                        if cached and await self.files.valid(cached):
                            await c.execute(
                                """UPDATE steps SET status='CACHED',progress=1,
                                artifact_id=%s,fingerprint=%s,error=NULL WHERE id=%s""",
                                (cached["id"], fp, step["id"]),
                            )
                            await c.execute(
                                "UPDATE tasks SET status='RUNNING' WHERE id=%s", (task["id"],)
                            )
                            await self.event(
                                c, task["id"], "step.cached", step, {"artifact_id": cached["id"]}
                            )
                            await self.refresh(c, task["id"])
                            continue
                        if cached:
                            await c.execute(
                                "DELETE FROM cache_entries WHERE fingerprint=%s AND artifact_id=%s",
                                (fp, cached["id"]),
                            )
                            await self.event(c, task["id"], "cache.invalidated", step)
                    slot = None
                    if resource == "gpu":
                        capacity = await one(
                            c, "SELECT max(capacity_mb) AS capacity FROM resource_slots"
                        )
                        if task["params"]["gpu_memory_mb"] > (capacity["capacity"] or 0):
                            await c.execute(
                                "UPDATE steps SET status='FAILED',error=%s WHERE id=%s",
                                ("请求显存超过 GPU mock 总容量", step["id"]),
                            )
                            await self.event(c, task["id"], "step.unschedulable", step)
                            await self.refresh(c, task["id"])
                            continue
                        slot = await one(
                            c,
                            """SELECT * FROM resource_slots WHERE attempt_id IS NULL
                            AND capacity_mb>=%s ORDER BY id FOR UPDATE SKIP LOCKED LIMIT 1""",
                            (task["params"]["gpu_memory_mb"],),
                        )
                        if slot is None:
                            if step["status"] != "WAITING_RESOURCE":
                                await c.execute(
                                    """UPDATE steps SET status='WAITING_RESOURCE',
                                    wait_since=COALESCE(wait_since,clock_timestamp()) WHERE id=%s""",
                                    (step["id"],),
                                )
                                await self.event(c, task["id"], "step.waiting_resource", step)
                            continue
                    attempt_id, token = uuid4().hex, step["token"] + 1
                    attempt = await one(
                        c,
                        """INSERT INTO attempts
                        (id,task_id,step_id,worker_id,token,retry_round,attempt_no,lease_expires_at,deadline,resume_from,status)
                        VALUES (%s,%s,%s,%s,%s,%s,%s,clock_timestamp()+%s*interval '1 second',
                        LEAST(clock_timestamp()+%s*interval '1 second',%s),%s,'RUNNING') RETURNING *""",
                        (
                            attempt_id,
                            task["id"],
                            step["id"],
                            worker_id,
                            token,
                            step["retry_round"],
                            step["attempts_in_round"] + 1,
                            self.settings.lease_seconds,
                            task["params"]["attempt_timeout"],
                            task["deadline"],
                            len(step["checkpoint"].get("frames", [])),
                        ),
                    )
                    await c.execute(
                        """UPDATE steps SET status='RUNNING',current_attempt_id=%s,token=%s,
                        attempts_in_round=attempts_in_round+1,fingerprint=%s,error=NULL WHERE id=%s""",
                        (attempt_id, token, fp, step["id"]),
                    )
                    await c.execute("UPDATE tasks SET status='RUNNING' WHERE id=%s", (task["id"],))
                    if slot:
                        await c.execute(
                            """UPDATE resource_slots SET attempt_id=%s,token=%s,
                            lease_expires_at=%s WHERE id=%s""",
                            (attempt_id, token, attempt["lease_expires_at"], slot["id"]),
                        )
                    await self.event(
                        c,
                        task["id"],
                        "attempt.started",
                        step,
                        {
                            "worker_id": worker_id,
                            "resume_from": attempt["resume_from"],
                            "token": token,
                        },
                        attempt_id,
                    )
                    return {
                        "task": task,
                        "step": {
                            **step,
                            "current_attempt_id": attempt_id,
                            "token": token,
                            "fingerprint": fp,
                        },
                        "attempt": attempt,
                        "upstream": upstream,
                    }
        return None

    async def owned(self, c, job: dict) -> dict | None:
        task_id, step = job["task"]["id"], job["step"]
        await c.execute("SELECT id FROM tasks WHERE id=%s FOR UPDATE", (task_id,))
        return await one(
            c,
            """SELECT s.* FROM steps s JOIN attempts a ON a.id=s.current_attempt_id
            JOIN tasks t ON t.id=s.task_id WHERE s.id=%s AND s.current_attempt_id=%s AND s.token=%s
            AND s.status='RUNNING' AND a.status='RUNNING' AND a.lease_expires_at>clock_timestamp()
            AND a.deadline>clock_timestamp() AND t.deadline>clock_timestamp()
            AND t.status='RUNNING' FOR UPDATE OF s""",
            (step["id"], job["attempt"]["id"], step["token"]),
        )

    async def heartbeat(self, job: dict) -> bool:
        async with self.db.connection() as c:
            if not await self.owned(c, job):
                return False
            row = await one(
                c,
                """UPDATE attempts SET heartbeat_at=clock_timestamp(),
                lease_expires_at=clock_timestamp()+%s*interval '1 second' WHERE id=%s RETURNING lease_expires_at""",
                (self.settings.lease_seconds, job["attempt"]["id"]),
            )
            await c.execute(
                "UPDATE resource_slots SET lease_expires_at=%s WHERE attempt_id=%s",
                (row["lease_expires_at"], job["attempt"]["id"]),
            )
            await c.execute(
                "UPDATE workers SET heartbeat_at=clock_timestamp() WHERE id=%s",
                (job["attempt"]["worker_id"],),
            )
            return True

    async def checkpoint(
        self, job: dict, checkpoint: dict, progress: float, reset: bool = False
    ) -> None:
        async with self.db.connection() as c:
            step = await self.owned(c, job)
            if step is None:
                raise StaleAttempt()
            await c.execute(
                "UPDATE steps SET checkpoint=%s,progress=%s WHERE id=%s",
                (Jsonb(checkpoint), progress, step["id"]),
            )
            if reset:
                await c.execute(
                    "UPDATE attempts SET resume_from=0 WHERE id=%s", (job["attempt"]["id"],)
                )
            await self.event(
                c,
                job["task"]["id"],
                "checkpoint.invalidated" if reset else "checkpoint.saved",
                step,
                {"completed_frames": len(checkpoint.get("frames", []))},
            )

    async def complete(self, job: dict, artifact: dict) -> bool:
        if not await self.files.valid(artifact):
            raise ValueError("Cannot publish an incomplete artifact")
        async with self.db.connection() as c:
            step = await self.owned(c, job)
            if step is None:
                await self.event(
                    c,
                    job["task"]["id"],
                    "attempt.stale_commit_rejected",
                    job["step"],
                    attempt_id=job["attempt"]["id"],
                )
                return False
            await c.execute(
                """INSERT INTO artifacts(id,path,sha256,size,media_type,name,attempt_id)
                VALUES (%s,%s,%s,%s,%s,%s,%s)""",
                tuple(artifact[k] for k in ("id", "path", "sha256", "size", "media_type", "name"))
                + (job["attempt"]["id"],),
            )
            await c.execute(
                "UPDATE attempts SET status='SUCCEEDED',finished_at=clock_timestamp() WHERE id=%s",
                (job["attempt"]["id"],),
            )
            await c.execute(
                """UPDATE steps SET status='SUCCEEDED',progress=1,artifact_id=%s,
                checkpoint='{}'::jsonb WHERE id=%s""",
                (artifact["id"], step["id"]),
            )
            if job["task"]["params"]["cache_enabled"]:
                await c.execute(
                    """INSERT INTO cache_entries(fingerprint,artifact_id) VALUES (%s,%s)
                    ON CONFLICT DO NOTHING""",
                    (step["fingerprint"], artifact["id"]),
                )
            await self.release(c, job["attempt"]["id"])
            await self.event(
                c, job["task"]["id"], "attempt.succeeded", step, {"artifact_id": artifact["id"]}
            )
            await self.refresh(c, job["task"]["id"])
            return True

    async def release(self, c, attempt_id: str) -> None:
        await c.execute(
            "UPDATE resource_slots SET attempt_id=NULL,token=NULL,lease_expires_at=NULL WHERE attempt_id=%s",
            (attempt_id,),
        )

    async def finish_failure(
        self,
        c,
        task: dict,
        step: dict,
        attempt: dict,
        status: str,
        error_type: str,
        error: str,
        retryable: bool,
    ) -> None:
        await c.execute(
            """UPDATE attempts SET status=%s,error_type=%s,error=%s,
            finished_at=clock_timestamp() WHERE id=%s""",
            (status, error_type, error[:500], attempt["id"]),
        )
        now = (await one(c, "SELECT clock_timestamp() AS now"))["now"]
        retry = (
            retryable
            and step["attempts_in_round"] < task["params"]["max_attempts"]
            and task["deadline"] > now
        )
        delay = (
            self.settings.retry_base_seconds
            * (2 ** max(0, step["attempts_in_round"] - 1))
            * random.uniform(0.8, 1.2)
        )
        await c.execute(
            """UPDATE steps SET status=%s,error=%s,
            next_run_at=clock_timestamp()+%s*interval '1 second' WHERE id=%s""",
            ("RETRY_WAIT" if retry else "FAILED", error[:500], delay if retry else 0, step["id"]),
        )
        await self.release(c, attempt["id"])
        await self.event(
            c,
            task["id"],
            "attempt." + status.lower(),
            step,
            {
                "error_type": error_type,
                "error": error[:500],
                "will_retry": retry,
                "retry_delay_seconds": round(delay, 3) if retry else None,
            },
        )
        await self.refresh(c, task["id"])

    async def fail(
        self,
        job: dict,
        error: str,
        retryable: bool = True,
        error_type: str = "TRANSIENT",
        status: str = "FAILED",
    ) -> bool:
        async with self.db.connection() as c:
            step = await self.owned(c, job)
            if step is None:
                return False
            await self.finish_failure(
                c, job["task"], step, job["attempt"], status, error_type, error, retryable
            )
            return True

    async def reap(self) -> int:
        count = 0
        async with self.db.connection() as c:
            tasks = await many(
                c,
                """SELECT * FROM tasks t WHERE t.status IN ('QUEUED','RUNNING','CANCELLING')
                AND (t.deadline<=clock_timestamp() OR EXISTS(SELECT 1 FROM attempts a WHERE a.task_id=t.id
                AND a.status='RUNNING' AND (a.lease_expires_at<=clock_timestamp() OR a.deadline<=clock_timestamp())))
                ORDER BY t.created_at FOR UPDATE SKIP LOCKED LIMIT 32""",
            )
            for task in tasks:
                now = (await one(c, "SELECT clock_timestamp() AS now"))["now"]
                expired_task = task["deadline"] <= now
                steps = await many(
                    c,
                    "SELECT * FROM steps WHERE task_id=%s ORDER BY ordinal FOR UPDATE",
                    (task["id"],),
                )
                for step in steps:
                    if step["status"] == "RUNNING":
                        attempt = await one(
                            c, "SELECT * FROM attempts WHERE id=%s", (step["current_attempt_id"],)
                        )
                        timeout = expired_task or attempt["deadline"] <= now
                        if task["status"] == "CANCELLING":
                            if attempt["lease_expires_at"] <= now:
                                await self.finish_cancel(c, task, step, confirmed=False)
                                count += 1
                            continue
                        if timeout or attempt["lease_expires_at"] <= now:
                            await self.finish_failure(
                                c,
                                task,
                                step,
                                attempt,
                                "TIMED_OUT" if timeout else "INTERRUPTED",
                                "TASK_DEADLINE"
                                if expired_task
                                else "TIMEOUT"
                                if timeout
                                else "WORKER_LOST",
                                "任务截止时间已到"
                                if expired_task
                                else "执行超时"
                                if timeout
                                else "Worker 心跳过期，恢复待执行",
                                not expired_task,
                            )
                            count += 1
                    elif expired_task and step["status"] not in TERMINAL:
                        await c.execute(
                            "UPDATE steps SET status='FAILED',error='任务截止时间已到' WHERE id=%s",
                            (step["id"],),
                        )
                        await self.event(c, task["id"], "step.deadline_exceeded", step)
                await self.refresh(c, task["id"])
        return count

    async def retry(self, step_id: str) -> dict:
        async with self.db.connection() as c:
            found = await one(c, "SELECT task_id FROM steps WHERE id=%s", (step_id,))
            if found is None:
                raise NotFound(step_id)
            task = await one(c, "SELECT * FROM tasks WHERE id=%s FOR UPDATE", (found["task_id"],))
            step = await one(c, "SELECT * FROM steps WHERE id=%s FOR UPDATE", (step_id,))
            if task["status"] in {"CANCELLING", "CANCELLED"} or task["archived_at"]:
                raise Conflict("取消或归档的任务不可重试；归档任务请先恢复到任务列表")
            if task["deadline"] <= (await one(c, "SELECT clock_timestamp() AS now"))["now"]:
                raise Conflict("任务已超过总体截止时间，请新建任务")
            if step["status"] != "FAILED":
                raise Conflict("仅 FAILED 步骤可手动重试；重复请求不会增加尝试")
            await c.execute(
                """UPDATE steps SET status='READY',retry_round=retry_round+1,
                attempts_in_round=0,next_run_at=clock_timestamp(),error=NULL WHERE id=%s""",
                (step_id,),
            )
            await c.execute("UPDATE tasks SET status='RUNNING' WHERE id=%s", (task["id"],))
            await self.event(c, task["id"], "step.manual_retry", step)
            await self.refresh(c, task["id"])
            return {
                "task_id": task["id"],
                "step_id": step_id,
                "retry_round": step["retry_round"] + 1,
            }

    async def resources(self) -> dict:
        async with self.db.connection() as c:
            return {
                "mock": True,
                "slots": await many(
                    c,
                    """SELECT r.*,a.worker_id,a.task_id FROM resource_slots r
                    LEFT JOIN attempts a ON a.id=r.attempt_id ORDER BY r.id""",
                ),
                "workers": await many(
                    c,
                    """SELECT *,heartbeat_at>clock_timestamp()-%s*interval '1 second' AS online
                    FROM workers ORDER BY heartbeat_at DESC LIMIT 20""",
                    (self.settings.lease_seconds,),
                ),
                "waiting": await many(
                    c,
                    """SELECT task_id,key,wait_since,
                    EXTRACT(epoch FROM clock_timestamp()-wait_since) AS waiting_seconds
                    FROM steps WHERE status='WAITING_RESOURCE' ORDER BY wait_since""",
                ),
            }

    async def cancel(self, task_id: str) -> dict:
        async with self.db.connection() as c:
            task = await one(c, "SELECT * FROM tasks WHERE id=%s FOR UPDATE", (task_id,))
            if task is None:
                raise NotFound(task_id)
            if task["status"] in {"CANCELLING", "CANCELLED"}:
                return {"task_id": task_id, "status": task["status"]}
            if task["status"] in TASK_TERMINAL:
                raise Conflict("任务已经结束，无需取消")
            await c.execute(
                "UPDATE tasks SET status='CANCELLING',cancel_requested_at=clock_timestamp() WHERE id=%s",
                (task_id,),
            )
            await self.event(c, task_id, "task.cancel_requested")
            steps = await many(
                c, "SELECT * FROM steps WHERE task_id=%s ORDER BY ordinal FOR UPDATE", (task_id,)
            )
            for step in steps:
                if step["status"] not in SUCCESS | {"FAILED", "RUNNING"}:
                    await c.execute(
                        "UPDATE steps SET status='CANCELLED' WHERE id=%s", (step["id"],)
                    )
                    await self.event(c, task_id, "step.cancelled", step)
            await self.refresh(c, task_id)
            return {
                "task_id": task_id,
                "status": (await one(c, "SELECT status FROM tasks WHERE id=%s", (task_id,)))[
                    "status"
                ],
            }

    async def finish_cancel(self, c, task: dict, step: dict, confirmed: bool) -> None:
        """Caller holds task + step locks; cancellation never consumes a retry round."""
        await c.execute(
            "UPDATE attempts SET status=%s,finished_at=clock_timestamp(),error_type=%s,error=%s WHERE id=%s",
            (
                "CANCELLED" if confirmed else "INTERRUPTED",
                "USER_CANCELLED" if confirmed else "CANCEL_WORKER_LOST",
                "Worker 已确认停止"
                if confirmed
                else "取消期间租约过期；执行停止未确认（仅释放模拟槽）",
                step["current_attempt_id"],
            ),
        )
        await c.execute("UPDATE steps SET status='CANCELLED' WHERE id=%s", (step["id"],))
        if not confirmed:
            await c.execute("UPDATE tasks SET cancel_unconfirmed=true WHERE id=%s", (task["id"],))
        await self.release(c, step["current_attempt_id"])
        await self.event(
            c,
            task["id"],
            "attempt.cancelled" if confirmed else "attempt.cancel_unconfirmed",
            step,
            {"stop_confirmed": confirmed},
        )
        await self.refresh(c, task["id"])

    async def acknowledge_cancel(self, job: dict) -> bool:
        """Only called after the compute coroutine and its blocking work have stopped."""
        async with self.db.connection() as c:
            task = await one(c, "SELECT * FROM tasks WHERE id=%s FOR UPDATE", (job["task"]["id"],))
            if not task or task["status"] != "CANCELLING":
                return False
            step = await one(c, "SELECT * FROM steps WHERE id=%s FOR UPDATE", (job["step"]["id"],))
            if (
                step["status"] != "RUNNING"
                or step["current_attempt_id"] != job["attempt"]["id"]
                or step["token"] != job["step"]["token"]
            ):
                return False
            await self.finish_cancel(c, task, step, confirmed=True)
            return True

    async def archive(self, task_id: str, archived: bool) -> dict:
        async with self.db.connection() as c:
            task = await one(c, "SELECT * FROM tasks WHERE id=%s FOR UPDATE", (task_id,))
            if task is None:
                raise NotFound(task_id)
            if task["status"] not in TASK_TERMINAL:
                raise Conflict("只能归档已结束的任务")
            if bool(task["archived_at"]) != archived:
                await c.execute(
                    "UPDATE tasks SET archived_at=CASE WHEN %s THEN clock_timestamp() ELSE NULL END WHERE id=%s",
                    (archived, task_id),
                )
                await self.event(c, task_id, "task.archived" if archived else "task.unarchived")
            return {"task_id": task_id, "archived": archived}
