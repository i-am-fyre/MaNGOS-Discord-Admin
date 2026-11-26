"""The bot's own state, kept in SQLite (never in the MaNGOS databases).

Stores Discord-user-to-game-account links, privacy choices, rate-limit
attempts, panel message IDs, ticket threads and announced world events.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import aiosqlite

_SCHEMA = """
CREATE TABLE IF NOT EXISTS account_links (
    discord_id  INTEGER NOT NULL,
    account_id  INTEGER NOT NULL UNIQUE,
    username    TEXT    NOT NULL,
    origin      TEXT    NOT NULL,          -- 'registered' or 'linked'
    created_at  INTEGER NOT NULL,
    PRIMARY KEY (discord_id, account_id)
);
CREATE TABLE IF NOT EXISTS privacy (
    discord_id  INTEGER PRIMARY KEY,
    visible     INTEGER NOT NULL           -- 1 = show in armory/leaderboards, 0 = hide
);
CREATE TABLE IF NOT EXISTS attempts (
    kind        TEXT    NOT NULL,
    discord_id  INTEGER NOT NULL,
    ts          INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_attempts ON attempts (kind, discord_id, ts);
CREATE TABLE IF NOT EXISTS panels (
    channel_id  INTEGER PRIMARY KEY,
    message_id  INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS tickets (
    realm_id    INTEGER NOT NULL,
    ticket_id   INTEGER NOT NULL,
    thread_id   INTEGER NOT NULL,
    state       TEXT    NOT NULL,          -- 'open', 'claimed', 'closed'
    claimed_by  INTEGER,
    text_hash   TEXT    NOT NULL,
    created_at  INTEGER NOT NULL,
    PRIMARY KEY (realm_id, ticket_id)
);
CREATE TABLE IF NOT EXISTS event_occurrences (
    realm_id            INTEGER NOT NULL,
    entry               INTEGER NOT NULL,
    start_ts            INTEGER NOT NULL,
    end_ts              INTEGER NOT NULL,
    announced_start     INTEGER NOT NULL DEFAULT 0,
    announced_reminder  INTEGER NOT NULL DEFAULT 0,
    announced_end       INTEGER NOT NULL DEFAULT 0,
    scheduled_event_id  INTEGER,
    PRIMARY KEY (realm_id, entry, start_ts)
);
CREATE TABLE IF NOT EXISTS kv (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


@dataclass(frozen=True)
class AccountLink:
    discord_id: int
    account_id: int
    username: str
    origin: str
    created_at: int


@dataclass(frozen=True)
class TicketRecord:
    realm_id: int
    ticket_id: int
    thread_id: int
    state: str
    claimed_by: int | None
    text_hash: str
    created_at: int


@dataclass(frozen=True)
class OccurrenceRecord:
    realm_id: int
    entry: int
    start_ts: int
    end_ts: int
    announced_start: bool
    announced_reminder: bool
    announced_end: bool
    scheduled_event_id: int | None


def _now() -> int:
    return int(time.time())


class Storage:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._db: aiosqlite.Connection | None = None

    async def open(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(self.path)
        self._db.row_factory = aiosqlite.Row
        await self._db.execute("PRAGMA journal_mode=WAL")
        await self._db.executescript(_SCHEMA)
        await self._db.commit()

    async def close(self) -> None:
        if self._db is not None:
            await self._db.close()
            self._db = None

    @property
    def db(self) -> aiosqlite.Connection:
        if self._db is None:
            raise RuntimeError("Storage is not open")
        return self._db

    async def _all(self, sql: str, args: Iterable[Any] = ()) -> list[aiosqlite.Row]:
        async with self.db.execute(sql, tuple(args)) as cur:
            return list(await cur.fetchall())

    async def _one(self, sql: str, args: Iterable[Any] = ()) -> aiosqlite.Row | None:
        async with self.db.execute(sql, tuple(args)) as cur:
            return await cur.fetchone()

    async def _write(self, sql: str, args: Iterable[Any] = ()) -> None:
        await self.db.execute(sql, tuple(args))
        await self.db.commit()

    # -- account links ---------------------------------------------------------

    async def links_for(self, discord_id: int) -> list[AccountLink]:
        rows = await self._all(
            "SELECT * FROM account_links WHERE discord_id = ? ORDER BY created_at", (discord_id,)
        )
        return [AccountLink(**dict(r)) for r in rows]

    async def link_for_account(self, account_id: int) -> AccountLink | None:
        row = await self._one("SELECT * FROM account_links WHERE account_id = ?", (account_id,))
        return AccountLink(**dict(row)) if row else None

    async def add_link(self, discord_id: int, account_id: int, username: str, origin: str) -> None:
        await self._write(
            "INSERT INTO account_links (discord_id, account_id, username, origin, created_at) VALUES (?, ?, ?, ?, ?)",
            (discord_id, account_id, username, origin, _now()),
        )

    async def remove_link(self, discord_id: int, account_id: int) -> bool:
        cur = await self.db.execute(
            "DELETE FROM account_links WHERE discord_id = ? AND account_id = ?", (discord_id, account_id)
        )
        await self.db.commit()
        return cur.rowcount > 0

    # -- privacy -----------------------------------------------------------------

    async def set_visible(self, discord_id: int, visible: bool) -> None:
        await self._write(
            "INSERT INTO privacy (discord_id, visible) VALUES (?, ?) "
            "ON CONFLICT(discord_id) DO UPDATE SET visible = excluded.visible",
            (discord_id, int(visible)),
        )

    async def visibility(self, discord_id: int) -> bool | None:
        row = await self._one("SELECT visible FROM privacy WHERE discord_id = ?", (discord_id,))
        return None if row is None else bool(row["visible"])

    async def account_ids_with_visibility(self, visible: bool) -> set[int]:
        rows = await self._all(
            "SELECT l.account_id FROM account_links l JOIN privacy p ON p.discord_id = l.discord_id "
            "WHERE p.visible = ?",
            (int(visible),),
        )
        return {int(r["account_id"]) for r in rows}

    # -- rate limiting -----------------------------------------------------------

    async def record_attempt(self, kind: str, discord_id: int) -> None:
        now = _now()
        await self.db.execute("INSERT INTO attempts (kind, discord_id, ts) VALUES (?, ?, ?)", (kind, discord_id, now))
        # Nothing we rate-limit looks back more than a week.
        await self.db.execute("DELETE FROM attempts WHERE ts < ?", (now - 7 * 86400,))
        await self.db.commit()

    async def count_attempts(self, kind: str, since_seconds: int, discord_id: int | None = None) -> int:
        since = _now() - since_seconds
        if discord_id is None:
            row = await self._one("SELECT COUNT(*) AS n FROM attempts WHERE kind = ? AND ts >= ?", (kind, since))
        else:
            row = await self._one(
                "SELECT COUNT(*) AS n FROM attempts WHERE kind = ? AND discord_id = ? AND ts >= ?",
                (kind, discord_id, since),
            )
        return int(row["n"]) if row else 0

    async def last_attempt(self, kind: str, discord_id: int) -> int | None:
        row = await self._one(
            "SELECT MAX(ts) AS ts FROM attempts WHERE kind = ? AND discord_id = ?", (kind, discord_id)
        )
        return int(row["ts"]) if row and row["ts"] is not None else None

    # -- panels --------------------------------------------------------------------

    async def panels(self) -> list[tuple[int, int]]:
        rows = await self._all("SELECT channel_id, message_id FROM panels")
        return [(int(r["channel_id"]), int(r["message_id"])) for r in rows]

    async def set_panel(self, channel_id: int, message_id: int) -> None:
        await self._write(
            "INSERT INTO panels (channel_id, message_id) VALUES (?, ?) "
            "ON CONFLICT(channel_id) DO UPDATE SET message_id = excluded.message_id",
            (channel_id, message_id),
        )

    async def remove_panel(self, channel_id: int) -> None:
        await self._write("DELETE FROM panels WHERE channel_id = ?", (channel_id,))

    # -- tickets -------------------------------------------------------------------

    async def ticket(self, realm_id: int, ticket_id: int) -> TicketRecord | None:
        row = await self._one("SELECT * FROM tickets WHERE realm_id = ? AND ticket_id = ?", (realm_id, ticket_id))
        return TicketRecord(**dict(row)) if row else None

    async def tickets(self, realm_id: int | None = None, states: tuple[str, ...] = ("open", "claimed")) -> list[TicketRecord]:
        marks = ",".join("?" for _ in states)
        if realm_id is None:
            rows = await self._all(f"SELECT * FROM tickets WHERE state IN ({marks}) ORDER BY ticket_id", states)
        else:
            rows = await self._all(
                f"SELECT * FROM tickets WHERE realm_id = ? AND state IN ({marks}) ORDER BY ticket_id",
                (realm_id, *states),
            )
        return [TicketRecord(**dict(r)) for r in rows]

    async def save_ticket(self, record: TicketRecord) -> None:
        await self._write(
            "INSERT INTO tickets (realm_id, ticket_id, thread_id, state, claimed_by, text_hash, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT(realm_id, ticket_id) DO UPDATE SET "
            "thread_id = excluded.thread_id, state = excluded.state, claimed_by = excluded.claimed_by, "
            "text_hash = excluded.text_hash",
            (record.realm_id, record.ticket_id, record.thread_id, record.state, record.claimed_by,
             record.text_hash, record.created_at),
        )

    # -- world events ----------------------------------------------------------------

    async def occurrence(self, realm_id: int, entry: int, start_ts: int) -> OccurrenceRecord | None:
        row = await self._one(
            "SELECT * FROM event_occurrences WHERE realm_id = ? AND entry = ? AND start_ts = ?",
            (realm_id, entry, start_ts),
        )
        return self._occ(row) if row else None

    async def open_occurrences(self, realm_id: int) -> list[OccurrenceRecord]:
        rows = await self._all(
            "SELECT * FROM event_occurrences WHERE realm_id = ? AND announced_end = 0 "
            "AND (announced_start = 1 OR scheduled_event_id IS NOT NULL)",
            (realm_id,),
        )
        return [self._occ(r) for r in rows]

    async def upsert_occurrence(self, realm_id: int, entry: int, start_ts: int, end_ts: int, **flags: Any) -> None:
        await self.db.execute(
            "INSERT OR IGNORE INTO event_occurrences (realm_id, entry, start_ts, end_ts) VALUES (?, ?, ?, ?)",
            (realm_id, entry, start_ts, end_ts),
        )
        allowed = {"announced_start", "announced_reminder", "announced_end", "scheduled_event_id"}
        for key, value in flags.items():
            if key not in allowed:
                raise ValueError(key)
            value = int(value) if isinstance(value, bool) else value
            await self.db.execute(
                f"UPDATE event_occurrences SET {key} = ? WHERE realm_id = ? AND entry = ? AND start_ts = ?",
                (value, realm_id, entry, start_ts),
            )
        await self.db.commit()

    async def prune_occurrences(self, older_than_ts: int) -> None:
        await self._write("DELETE FROM event_occurrences WHERE end_ts < ?", (older_than_ts,))

    @staticmethod
    def _occ(row: aiosqlite.Row) -> OccurrenceRecord:
        return OccurrenceRecord(
            realm_id=row["realm_id"], entry=row["entry"], start_ts=row["start_ts"], end_ts=row["end_ts"],
            announced_start=bool(row["announced_start"]), announced_reminder=bool(row["announced_reminder"]),
            announced_end=bool(row["announced_end"]), scheduled_event_id=row["scheduled_event_id"],
        )

    # -- key/value -------------------------------------------------------------------

    async def get(self, key: str) -> str | None:
        row = await self._one("SELECT value FROM kv WHERE key = ?", (key,))
        return row["value"] if row else None

    async def set(self, key: str, value: str) -> None:
        await self._write(
            "INSERT INTO kv (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
