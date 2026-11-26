"""Queries against the MaNGOS realmd, characters and world databases.

Schemas checked against mangos/Realm_DB and the Character/World setup files of
mangoszero through mangosfour. Where the cores differ (honour kill column,
item damage/armour columns) the code adapts per realm.
"""

from __future__ import annotations

from typing import Any, Iterable

from .config import Config, RealmConfig
from .database import Database, IntegrityError
from .accounts import AccountExistsError

# Columns selected for a character everywhere it's shown.
_CHAR_COLUMNS = "c.guid, c.account, c.name, c.race, c.class, c.gender, c.level, c.xp, c.money, c.totaltime, c.online"


def _q(schema: str, table: str) -> str:
    return f"`{schema}`.`{table}`"


def _marks(values: Iterable[Any]) -> str:
    return ",".join("%s" for _ in values)


def kills_column(realm: RealmConfig) -> str:
    # Zero keeps the vanilla honour system's lifetime counter.
    return "stored_honorable_kills" if realm.core == "zero" else "totalKills"


class Mangos:
    def __init__(self, db: Database, config: Config) -> None:
        self.db = db
        self.config = config

    # ------------------------------------------------------------------ realmd

    @property
    def _auth(self):
        return self.config.auth_database

    def _realmd(self, table: str) -> str:
        return _q(self.config.realmd_db, table)

    async def ping_realmd(self) -> int:
        row = await self.db.fetchone(self._auth, f"SELECT COUNT(*) AS n FROM {self._realmd('account')}")
        return int(row["n"]) if row else 0

    async def account_by_name(self, username: str) -> dict[str, Any] | None:
        return await self.db.fetchone(
            self._auth,
            f"SELECT id, username, sha_pass_hash, gmlevel, joindate, last_login, locked, expansion "
            f"FROM {self._realmd('account')} WHERE username = %s",
            (username,),
        )

    async def account_by_id(self, account_id: int) -> dict[str, Any] | None:
        return await self.db.fetchone(
            self._auth,
            f"SELECT id, username, gmlevel, joindate, last_login, locked, expansion "
            f"FROM {self._realmd('account')} WHERE id = %s",
            (account_id,),
        )

    async def create_account(self, username: str, pass_hash: str, expansion: int) -> int:
        """Insert an account exactly as AccountMgr::CreateAccount does."""
        try:
            async with self.db.transaction(self._auth) as cur:
                await cur.execute(
                    f"INSERT INTO {self._realmd('account')} (username, sha_pass_hash, joindate, expansion) "
                    "VALUES (%s, %s, NOW(), %s)",
                    (username, pass_hash, expansion),
                )
                account_id = int(cur.lastrowid)
                await cur.execute(
                    f"INSERT IGNORE INTO {self._realmd('realmcharacters')} (realmid, acctid, numchars) "
                    f"SELECT id, %s, 0 FROM {self._realmd('realmlist')}",
                    (account_id,),
                )
        except IntegrityError as exc:  # unique idx_username: lost a race with another signup
            raise AccountExistsError(username) from exc
        return account_id

    async def set_password_hash(self, account_id: int, pass_hash: str) -> None:
        """Same as AccountMgr::ChangePassword: reset v/s so realmd recomputes them."""
        await self.db.execute(
            self._auth,
            f"UPDATE {self._realmd('account')} SET v = '0', s = '0', sha_pass_hash = %s WHERE id = %s",
            (pass_hash, account_id),
        )

    async def gm_account_ids(self) -> set[int]:
        rows = await self.db.fetchall(self._auth, f"SELECT id FROM {self._realmd('account')} WHERE gmlevel > 0")
        return {int(r["id"]) for r in rows}

    async def active_ban(self, account_id: int) -> dict[str, Any] | None:
        # bandate = unbandate means a permanent ban.
        return await self.db.fetchone(
            self._auth,
            f"SELECT bandate, unbandate, bannedby, banreason FROM {self._realmd('account_banned')} "
            "WHERE id = %s AND active = 1 AND (unbandate > UNIX_TIMESTAMP() OR unbandate = bandate) "
            "ORDER BY bandate DESC LIMIT 1",
            (account_id,),
        )

    async def realmlist(self) -> list[dict[str, Any]]:
        return await self.db.fetchall(
            self._auth, f"SELECT id, name, address, port, population FROM {self._realmd('realmlist')}"
        )

    async def latest_uptime(self, realm_id: int) -> dict[str, Any] | None:
        return await self.db.fetchone(
            self._auth,
            f"SELECT starttime, uptime, maxplayers FROM {self._realmd('uptime')} "
            "WHERE realmid = %s ORDER BY starttime DESC LIMIT 1",
            (realm_id,),
        )

    # -------------------------------------------------------------- characters

    def _chars(self, realm: RealmConfig, table: str) -> str:
        return _q(realm.characters_db, table)

    async def ping_characters(self, realm: RealmConfig) -> int:
        row = await self.db.fetchone(realm.database, f"SELECT COUNT(*) AS n FROM {self._chars(realm, 'characters')}")
        return int(row["n"]) if row else 0

    async def online_count(self, realm: RealmConfig) -> int:
        row = await self.db.fetchone(
            realm.database, f"SELECT COUNT(*) AS n FROM {self._chars(realm, 'characters')} WHERE online = 1"
        )
        return int(row["n"]) if row else 0

    async def online_characters(self, realm: RealmConfig, limit: int = 500) -> list[dict[str, Any]]:
        return await self.db.fetchall(
            realm.database,
            f"SELECT {_CHAR_COLUMNS} FROM {self._chars(realm, 'characters')} c "
            "WHERE c.online = 1 ORDER BY c.level DESC, c.name LIMIT %s",
            (limit,),
        )

    async def character_by_name(self, realm: RealmConfig, name: str) -> dict[str, Any] | None:
        return await self.db.fetchone(
            realm.database,
            f"SELECT {_CHAR_COLUMNS}, c.{kills_column(realm)} AS kills, g.name AS guild_name "
            f"FROM {self._chars(realm, 'characters')} c "
            f"LEFT JOIN {self._chars(realm, 'guild_member')} gm ON gm.guid = c.guid "
            f"LEFT JOIN {self._chars(realm, 'guild')} g ON g.guildid = gm.guildid "
            "WHERE c.name = %s AND c.deleteDate IS NULL AND c.account <> 0",
            (name,),
        )

    async def character_by_guid(self, realm: RealmConfig, guid: int) -> dict[str, Any] | None:
        return await self.db.fetchone(
            realm.database,
            f"SELECT {_CHAR_COLUMNS} FROM {self._chars(realm, 'characters')} c WHERE c.guid = %s",
            (guid,),
        )

    async def search_character_names(
        self, realm: RealmConfig, prefix: str, limit: int, exclude_accounts: set[int], only_accounts: set[int] | None
    ) -> list[str]:
        sql = (
            f"SELECT c.name FROM {self._chars(realm, 'characters')} c "
            "WHERE c.name LIKE %s AND c.deleteDate IS NULL AND c.account <> 0"
        )
        escaped = prefix.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        args: list[Any] = [escaped + "%"]
        sql, args = self._account_filter(sql, args, exclude_accounts, only_accounts)
        if sql is None:
            return []
        sql += " ORDER BY c.level DESC, c.name LIMIT %s"
        args.append(limit)
        rows = await self.db.fetchall(realm.database, sql, args)
        return [r["name"] for r in rows]

    async def characters_for_account(self, realm: RealmConfig, account_id: int) -> list[dict[str, Any]]:
        return await self.db.fetchall(
            realm.database,
            f"SELECT {_CHAR_COLUMNS} FROM {self._chars(realm, 'characters')} c "
            "WHERE c.account = %s AND c.deleteDate IS NULL ORDER BY c.level DESC, c.name",
            (account_id,),
        )

    async def equipped_items(self, realm: RealmConfig, guid: int) -> dict[int, int]:
        """``{slot: item entry}`` for the character's equipped gear."""
        rows = await self.db.fetchall(
            realm.database,
            f"SELECT slot, item_template FROM {self._chars(realm, 'character_inventory')} "
            "WHERE guid = %s AND bag = 0 AND slot < 19",
            (guid,),
        )
        return {int(r["slot"]): int(r["item_template"]) for r in rows}

    async def open_tickets(self, realm: RealmConfig) -> list[dict[str, Any]]:
        return await self.db.fetchall(
            realm.database,
            f"SELECT t.ticket_id, t.guid, t.ticket_text, t.ticket_lastchange, "
            f"c.name, c.level, c.race, c.class, c.account "
            f"FROM {self._chars(realm, 'character_ticket')} t "
            f"LEFT JOIN {self._chars(realm, 'characters')} c ON c.guid = t.guid "
            "WHERE t.resolved = 0 ORDER BY t.ticket_id",
        )

    async def ticket(self, realm: RealmConfig, ticket_id: int) -> dict[str, Any] | None:
        return await self.db.fetchone(
            realm.database,
            f"SELECT ticket_id, guid, ticket_text, response_text, ticket_lastchange, resolved "
            f"FROM {self._chars(realm, 'character_ticket')} WHERE ticket_id = %s",
            (ticket_id,),
        )

    async def leaderboard(
        self, realm: RealmConfig, category: str, limit: int, exclude_accounts: set[int], only_accounts: set[int] | None
    ) -> list[dict[str, Any]]:
        order = {
            "level": "c.level DESC, c.xp DESC",
            "playtime": "c.totaltime DESC",
            "gold": "c.money DESC",
            "kills": f"c.{kills_column(realm)} DESC",
        }[category]
        sql = (
            f"SELECT {_CHAR_COLUMNS}, c.{kills_column(realm)} AS kills "
            f"FROM {self._chars(realm, 'characters')} c WHERE c.deleteDate IS NULL AND c.account <> 0"
        )
        args: list[Any] = []
        sql, args = self._account_filter(sql, args, exclude_accounts, only_accounts)
        if sql is None:
            return []
        sql += f" ORDER BY {order}, c.name LIMIT %s"
        args.append(limit)
        return await self.db.fetchall(realm.database, sql, args)

    @staticmethod
    def _account_filter(
        sql: str, args: list[Any], exclude: set[int], only: set[int] | None
    ) -> tuple[str | None, list[Any]]:
        if only is not None:
            allowed = sorted(only - exclude)
            if not allowed:
                return None, args
            return f"{sql} AND c.account IN ({_marks(allowed)})", [*args, *allowed]
        if exclude:
            excluded = sorted(exclude)
            return f"{sql} AND c.account NOT IN ({_marks(excluded)})", [*args, *excluded]
        return sql, args

    # ------------------------------------------------------------------ world

    async def ping_world(self, realm: RealmConfig) -> int:
        row = await self.db.fetchone(realm.database, f"SELECT COUNT(*) AS n FROM {_q(realm.world_db, 'game_event')}")
        return int(row["n"]) if row else 0

    async def item_templates(self, realm: RealmConfig, entries: Iterable[int]) -> dict[int, dict[str, Any]]:
        """Full item_template rows. SELECT * because columns differ by core
        (Three/Four have no armour or damage columns, Two has only two damage slots)."""
        entries = sorted(set(entries))
        if not entries:
            return {}
        rows = await self.db.fetchall(
            realm.database,
            f"SELECT * FROM {_q(realm.world_db, 'item_template')} WHERE entry IN ({_marks(entries)})",
            entries,
        )
        return {int(r["entry"]): r for r in rows}

    async def game_events(self, realm: RealmConfig) -> list[dict[str, Any]]:
        return await self.db.fetchall(
            realm.database,
            f"SELECT entry, start_time, end_time, occurence, length, holiday, description "
            f"FROM {_q(realm.world_db, 'game_event')}",
        )
