"""Real PostgreSQL per-test databases. No SQLite stand-in for locking semantics."""

import os
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from pipeline.config import Settings
from pipeline.engine import Engine


def pytest_configure(config):
    """Fresh external-DB checkouts have not run the local bootstrap script."""
    if base := config.getoption("basetemp"):
        Path(base).resolve().parent.mkdir(parents=True, exist_ok=True)


@pytest.fixture(scope="session")
def postgres_url():
    if os.getenv("TEST_DATABASE_URL"):
        yield os.environ["TEST_DATABASE_URL"]
        return
    import pgserver

    path = Path(".local/test-postgres").resolve()
    path.mkdir(parents=True, exist_ok=True)
    server = pgserver.get_server(path, cleanup_mode="stop")
    try:
        yield server.get_uri()
    finally:
        server.cleanup()


@pytest.fixture
def settings(postgres_url, tmp_path):
    name = "test_" + uuid4().hex
    with psycopg.connect(postgres_url, autocommit=True) as c:
        c.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    options = conninfo_to_dict(postgres_url)
    options["dbname"] = name
    result = Settings(
        make_conninfo(**options),
        tmp_path / "artifacts",
        lease_seconds=1,
        heartbeat_seconds=0.1,
        poll_seconds=0.02,
        retry_base_seconds=0.01,
    )
    try:
        yield result
    finally:
        with psycopg.connect(postgres_url, autocommit=True) as c:
            c.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name)))


@pytest.fixture
async def engine(settings):
    result = Engine(settings)
    await result.db.initialize()
    yield result


@pytest.fixture
def fast_request():
    from pipeline.domain import TaskRequest

    return TaskRequest(step_delay=0.01, frames=8, cache_enabled=False)
