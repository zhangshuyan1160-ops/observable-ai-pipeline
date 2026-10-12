"""Small explicit PostgreSQL transactions; no queue/database dual writes."""

from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

import psycopg
from psycopg.rows import dict_row

from pipeline.config import Settings


class Database:
    def __init__(self, settings: Settings):
        self.settings = settings

    @asynccontextmanager
    async def connection(self) -> AsyncIterator[psycopg.AsyncConnection]:
        async with await psycopg.AsyncConnection.connect(
            self.settings.database_url,
            row_factory=dict_row,
            connect_timeout=3,
            options="-c timezone=UTC",
        ) as conn:
            yield conn

    async def initialize(self) -> None:
        async with self.connection() as conn:
            await conn.execute("SELECT pg_advisory_xact_lock(7391824)")
            await conn.execute(Path(__file__).with_name("schema.sql").read_text())


async def one(conn: psycopg.AsyncConnection, sql: str, args: tuple = ()) -> dict | None:
    return await (await conn.execute(sql, args)).fetchone()


async def many(conn: psycopg.AsyncConnection, sql: str, args: tuple = ()) -> list[dict]:
    return await (await conn.execute(sql, args)).fetchall()
