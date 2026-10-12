"""Read/query/control API. Durable computation lives in separate processes."""

from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

from fastapi import FastAPI, Header, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from pipeline.config import Settings
from pipeline.domain import STEPS, TaskRequest
from pipeline.engine import Conflict, Engine, NotFound
from pipeline.storage.database import many, one


def create_app(settings: Settings | None = None) -> FastAPI:
    engine = Engine(settings or Settings.from_env())

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        await engine.db.initialize()
        yield

    app = FastAPI(
        title="Observable AI Pipeline",
        version="0.1.0",
        lifespan=lifespan,
        description="本地 mock 动画处理。持久化、重试、恢复与产物均真实运行。",
    )
    app.state.engine = engine

    @app.exception_handler(Conflict)
    async def conflict_handler(request, exc: Conflict) -> JSONResponse:
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    @app.exception_handler(NotFound)
    async def not_found_handler(request, exc: NotFound) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": "记录不存在"})

    @app.get("/healthz")
    async def health() -> dict:
        return {"status": "ok"}

    @app.get("/readyz")
    async def ready() -> dict:
        try:
            async with engine.db.connection() as c:
                await c.execute("SELECT 1")
        except Exception as exc:
            raise HTTPException(503, "数据库不可用") from exc
        return {"status": "ready"}

    @app.get("/api/definition")
    async def definition() -> dict:
        return {"version": "animation-v1", "steps": STEPS, "gpu_mock": True}

    @app.post("/api/tasks", status_code=202)
    async def create_task(
        payload: TaskRequest, idempotency_key: Annotated[str, Header(min_length=1, max_length=100)]
    ) -> dict:
        task = await engine.create(payload, idempotency_key)
        return {**task, "status_url": f"/api/tasks/{task['task_id']}"}

    @app.get("/api/tasks")
    async def list_tasks(
        limit: Annotated[int, Query(ge=1, le=100)] = 30,
        before: str | None = None,
        status: str | None = None,
    ) -> dict:
        if status and status not in {"QUEUED", "RUNNING", "SUCCEEDED", "FAILED"}:
            raise HTTPException(422, "未知任务状态")
        async with engine.db.connection() as c:
            rows = await many(
                c,
                """SELECT id,params->>'title' AS title,status,created_at,updated_at
                FROM tasks WHERE (%s::text IS NULL OR status=%s)
                AND (%s::text IS NULL OR (created_at,id)<(SELECT created_at,id FROM tasks WHERE id=%s))
                ORDER BY created_at DESC,id DESC LIMIT %s""",
                (status, status, before, before, limit),
            )
        return {"items": rows, "next_cursor": rows[-1]["id"] if len(rows) == limit else None}

    @app.get("/api/tasks/{task_id}")
    async def task_detail(task_id: str) -> dict:
        return await engine.task(task_id)

    @app.get("/api/tasks/{task_id}/events")
    async def task_events(
        task_id: str,
        after_seq: Annotated[int, Query(ge=0)] = 0,
        limit: Annotated[int, Query(ge=1, le=500)] = 100,
    ) -> dict:
        rows = await engine.events(task_id, after_seq, limit)
        return {"items": rows, "next_seq": rows[-1]["seq"] if rows else after_seq}

    @app.get("/api/steps/{step_id}/attempts")
    async def step_attempts(step_id: str) -> dict:
        async with engine.db.connection() as c:
            if not await one(c, "SELECT id FROM steps WHERE id=%s", (step_id,)):
                raise NotFound(step_id)
            return {
                "items": await many(
                    c, "SELECT * FROM attempts WHERE step_id=%s ORDER BY started_at", (step_id,)
                )
            }

    @app.post("/api/steps/{step_id}/retry", status_code=202)
    async def retry_step(step_id: str) -> dict:
        return await engine.retry(step_id)

    @app.get("/api/resources")
    async def resources() -> dict:
        return await engine.resources()

    @app.get("/api/artifacts/{artifact_id}")
    async def artifact(artifact_id: str, download: bool = False) -> FileResponse:
        async with engine.db.connection() as c:
            row = await one(c, "SELECT * FROM artifacts WHERE id=%s", (artifact_id,))
        if row is None:
            raise NotFound(artifact_id)
        if not await engine.files.valid(row):
            raise HTTPException(410, "产物已丢失或损坏，请创建新任务恢复")
        return FileResponse(
            engine.files.resolve(row["path"]),
            media_type=row["media_type"],
            filename=row["name"] if download else None,
        )

    @app.get("/metrics", response_class=PlainTextResponse)
    async def metrics() -> str:
        async with engine.db.connection() as c:
            steps = await many(c, "SELECT key,status,count(*) AS n FROM steps GROUP BY key,status")
            attempts = await many(
                c,
                """SELECT status,count(*) AS n,
                COALESCE(sum(EXTRACT(epoch FROM finished_at-started_at)),0) AS seconds
                FROM attempts WHERE status!='RUNNING' GROUP BY status""",
            )
            slots = await one(
                c,
                "SELECT count(*) FILTER(WHERE attempt_id IS NOT NULL) AS busy FROM resource_slots",
            )
        lines = ["# TYPE pipeline_steps gauge"]
        lines += [
            f'pipeline_steps{{step="{s["key"]}",status="{s["status"]}"}} {s["n"]}' for s in steps
        ]
        lines += ["# TYPE pipeline_attempts_total counter"]
        lines += [f'pipeline_attempts_total{{status="{a["status"]}"}} {a["n"]}' for a in attempts]
        lines += ["# TYPE pipeline_attempt_duration_seconds summary"]
        for a in attempts:
            lines.extend(
                [
                    f'pipeline_attempt_duration_seconds_sum{{status="{a["status"]}"}} {a["seconds"]}',
                    f'pipeline_attempt_duration_seconds_count{{status="{a["status"]}"}} {a["n"]}',
                ]
            )
        lines += ["# TYPE pipeline_gpu_mock_busy gauge", f"pipeline_gpu_mock_busy {slots['busy']}"]
        return "\n".join(lines) + "\n"

    web = Path(__file__).with_name("web")
    app.mount("/static", StaticFiles(directory=web), name="static")

    @app.get("/", include_in_schema=False)
    async def index() -> FileResponse:
        return FileResponse(web / "index.html")

    return app
