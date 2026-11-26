"""Async MySQL/MariaDB access with one connection pool per server.

Every query uses parameters for values; only database names (validated in
config.py) are ever interpolated into SQL.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Sequence

import aiomysql
import pymysql

from .config import DbConnection

log = logging.getLogger(__name__)

# Re-exported so callers don't need to import pymysql.
DatabaseError = pymysql.MySQLError
IntegrityError = pymysql.err.IntegrityError


class Database:
    def __init__(self) -> None:
        self._pools: dict[tuple[str, int, str], aiomysql.Pool] = {}
        self._lock = asyncio.Lock()

    async def _pool(self, conn: DbConnection) -> aiomysql.Pool:
        pool = self._pools.get(conn.key)
        if pool is not None:
            return pool
        async with self._lock:
            pool = self._pools.get(conn.key)
            if pool is None:
                pool = await aiomysql.create_pool(
                    host=conn.host,
                    port=conn.port,
                    user=conn.user,
                    password=conn.password,
                    minsize=0,
                    maxsize=conn.pool_size,
                    autocommit=True,
                    charset="utf8mb4",
                    connect_timeout=5,
                    pool_recycle=1800,
                    cursorclass=aiomysql.DictCursor,
                )
                self._pools[conn.key] = pool
                log.info("Opened MySQL pool to %s:%s as %s", conn.host, conn.port, conn.user)
        return pool

    async def fetchall(self, conn: DbConnection, sql: str, args: Sequence[Any] = ()) -> list[dict[str, Any]]:
        pool = await self._pool(conn)
        async with pool.acquire() as connection:
            async with connection.cursor() as cur:
                await cur.execute(sql, tuple(args))
                return list(await cur.fetchall())

    async def fetchone(self, conn: DbConnection, sql: str, args: Sequence[Any] = ()) -> dict[str, Any] | None:
        pool = await self._pool(conn)
        async with pool.acquire() as connection:
            async with connection.cursor() as cur:
                await cur.execute(sql, tuple(args))
                return await cur.fetchone()

    async def execute(self, conn: DbConnection, sql: str, args: Sequence[Any] = ()) -> int:
        pool = await self._pool(conn)
        async with pool.acquire() as connection:
            async with connection.cursor() as cur:
                await cur.execute(sql, tuple(args))
                return cur.rowcount

    @asynccontextmanager
    async def transaction(self, conn: DbConnection) -> AsyncIterator[aiomysql.DictCursor]:
        pool = await self._pool(conn)
        async with pool.acquire() as connection:
            await connection.begin()
            try:
                async with connection.cursor() as cur:
                    yield cur
                await connection.commit()
            except BaseException:
                await connection.rollback()
                raise

    async def close(self) -> None:
        for pool in self._pools.values():
            pool.close()
            await pool.wait_closed()
        self._pools.clear()
