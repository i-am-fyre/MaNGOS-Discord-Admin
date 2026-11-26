"""Server status: up/down checks, player counts, presence, alerts, voice counter."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import Counter
from dataclasses import dataclass
from typing import TYPE_CHECKING

import discord
from discord.ext import tasks

from .. import ui, wow
from ..config import RealmConfig
from ..database import DatabaseError

if TYPE_CHECKING:
    from ..bot import ServerBot

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class RealmStatus:
    realm: RealmConfig
    online: bool | None  # None = unknown (no address to check)
    players: int | None
    max_players: int | None
    started_at: int | None
    database_ok: bool


async def tcp_reachable(host: str, port: int, timeout: float = 3.0) -> bool:
    try:
        _, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout)
    except (OSError, asyncio.TimeoutError):
        return False
    writer.close()
    try:
        await writer.wait_closed()
    except OSError:
        pass
    return True


def _split_address(address: str) -> tuple[str, int]:
    host, _, port = address.rpartition(":")
    return host, int(port)


def status_icon(status: RealmStatus | None) -> str:
    if status is None or status.online is None:
        return "⚪"
    return "🟢" if status.online else "🔴"


def status_summary(status: RealmStatus | None) -> str:
    if status is None:
        return "Checking…"
    if status.online is False:
        return "Offline"
    parts = ["Online" if status.online else "Status unknown"]
    if status.players is not None:
        parts.append(f"**{status.players}** player{'s' if status.players != 1 else ''}")
    if status.started_at:
        parts.append(f"up since {ui.ts(status.started_at)}")
    return " · ".join(parts)


def status_embed(bot: "ServerBot") -> discord.Embed:
    statuses: dict[int, RealmStatus] = getattr(bot, "realm_status", {})
    e = ui.embed(f"{bot.config.server.name} — server status", color=bot.config.identity.color)
    for realm in bot.config.realms:
        st = statuses.get(realm.id)
        value = status_summary(st)
        if st and st.max_players:
            value += f"\nPeak since restart: {st.max_players}"
        e.add_field(name=f"{status_icon(st)} {realm.name}", value=value, inline=False)
    login = getattr(bot, "login_server_up", None)
    if login is not None:
        e.add_field(name="Login server", value="🟢 Accepting logins" if login else "🔴 Not responding", inline=False)
    checked = getattr(bot, "status_checked_at", None)
    if checked:
        e.description = f"Last checked {ui.ts(checked)}"
    return ui.footer(e, bot)


class StatusCog(discord.Cog, name="Status"):
    def __init__(self, bot: "ServerBot") -> None:
        self.bot = bot
        self.cfg = bot.config.status
        self._failures: dict[int, int] = {}
        self._last_online: dict[int, bool | None] = {}
        self._realmlist_cache: tuple[float, dict[int, tuple[str, int]]] | None = None
        self._presence: tuple[str, str] | None = None
        self._voice_name: str | None = None
        self._voice_at = 0.0
        bot.realm_status = {}
        bot.login_server_up = None
        bot.status_checked_at = None
        self.refresh.change_interval(seconds=self.cfg.interval_seconds)

    def cog_unload(self) -> None:
        self.refresh.cancel()

    @discord.Cog.listener()
    async def on_ready(self) -> None:
        if not self.refresh.is_running():
            self.refresh.start()

    @tasks.loop(seconds=60)
    async def refresh(self) -> None:
        try:
            await self.update()
        except Exception:
            log.exception("Status refresh failed")

    # -- checks ----------------------------------------------------------------------

    async def _world_address(self, realm: RealmConfig) -> tuple[str, int] | None:
        if realm.world_address:
            return _split_address(realm.world_address)
        now = time.monotonic()
        if self._realmlist_cache is None or now - self._realmlist_cache[0] > 600:
            try:
                rows = await self.bot.mangos.realmlist()
                self._realmlist_cache = (now, {int(r["id"]): (str(r["address"]), int(r["port"])) for r in rows})
            except DatabaseError:
                return None
        return self._realmlist_cache[1].get(realm.id)

    async def check_realm(self, realm: RealmConfig) -> RealmStatus:
        address = await self._world_address(realm)
        reachable = await tcp_reachable(*address) if address else None
        if reachable is False:
            self._failures[realm.id] = self._failures.get(realm.id, 0) + 1
            # Don't flap on one missed probe: stay "up" until N failures in a row.
            online: bool | None = False if self._failures[realm.id] >= self.cfg.down_after_failures else self._last_online.get(realm.id, True)
        else:
            self._failures[realm.id] = 0
            online = reachable
        self._last_online[realm.id] = online

        players = max_players = started_at = None
        database_ok = True
        try:
            if online is not False:
                players = await self.bot.mangos.online_count(realm)
            uptime = await self.bot.mangos.latest_uptime(realm.id)
            if uptime:
                max_players = int(uptime["maxplayers"] or 0) or None
                if online:
                    started_at = int(uptime["starttime"] or 0) or None
        except DatabaseError as exc:
            database_ok = False
            log.warning("Status query failed for %s: %s", realm.name, exc)
        return RealmStatus(realm, online, players, max_players, started_at, database_ok)

    async def update(self) -> dict[int, RealmStatus]:
        results = await asyncio.gather(*(self.check_realm(r) for r in self.bot.config.realms))
        statuses = {s.realm.id: s for s in results}
        login = None
        if self.bot.config.server.realmd_address:
            login = await tcp_reachable(*_split_address(self.bot.config.server.realmd_address))
        self.bot.realm_status = statuses
        self.bot.login_server_up = login
        self.bot.status_checked_at = int(time.time())
        if self.cfg.alerts:
            await self._alerts(statuses)
        if self.cfg.presence:
            await self._update_presence(statuses)
        if self.cfg.voice_counter_minutes:
            await self._update_voice(statuses)
        self.bot.dispatch("mangos_status", statuses)
        return statuses

    # -- outputs -------------------------------------------------------------------

    async def _alerts(self, statuses: dict[int, RealmStatus]) -> None:
        channel = self.bot.channel(self.bot.config.channels.status)
        for status in statuses.values():
            if status.online is None:
                continue
            key = f"status:{status.realm.id}"
            raw = await self.bot.storage.get(key)
            previous = json.loads(raw) if raw else None
            now = int(time.time())
            if previous is not None and previous.get("online") == status.online:
                continue
            await self.bot.storage.set(key, json.dumps({"online": status.online, "since": now}))
            if previous is None or channel is None:
                continue  # first observation: record it, don't announce
            since = int(previous.get("since") or now)
            if status.online:
                e = ui.embed(f"🟢 {status.realm.name} is back online",
                             f"Downtime: {wow.format_duration(now - since)}. See you in game!", color=ui.SUCCESS)
            else:
                e = ui.embed(f"🔴 {status.realm.name} is offline",
                             "The world server stopped responding. We'll post here when it's back.", color=ui.ERROR)
            e.timestamp = discord.utils.utcnow()
            try:
                await channel.send(embed=ui.footer(e, self.bot))
            except discord.HTTPException as exc:
                log.warning("Could not post status alert: %s", exc)

    async def _update_presence(self, statuses: dict[int, RealmStatus]) -> None:
        known = [s for s in statuses.values() if s.online is not None]
        players = sum(s.players or 0 for s in statuses.values() if s.online)
        if known and not any(s.online for s in known):
            text, state = "Server offline", "dnd"
        else:
            text, state = f"{players} adventurer{'s' if players != 1 else ''} online", "online"
        if self._presence == (text, state):
            return
        self._presence = (text, state)
        await self.bot.change_presence(
            activity=discord.Activity(type=discord.ActivityType.watching, name=text),
            status=discord.Status.dnd if state == "dnd" else discord.Status.online,
        )

    async def _update_voice(self, statuses: dict[int, RealmStatus]) -> None:
        channel = self.bot.channel(self.bot.config.channels.player_count_voice)
        if not isinstance(channel, (discord.VoiceChannel, discord.StageChannel)):
            return
        known = [s for s in statuses.values() if s.online is not None]
        if known and not any(s.online for s in known):
            name = "🔴 Server offline"
        else:
            name = f"🟢 Online: {sum(s.players or 0 for s in statuses.values() if s.online)}"
        # Discord allows two channel renames per 10 minutes.
        if name == self._voice_name or time.monotonic() - self._voice_at < self.cfg.voice_counter_minutes * 60:
            return
        try:
            await channel.edit(name=name, reason="Player counter")
            self._voice_name, self._voice_at = name, time.monotonic()
        except discord.HTTPException as exc:
            log.warning("Could not rename the player counter channel: %s", exc)

    # -- commands ------------------------------------------------------------------

    @discord.slash_command(name="status", description="Is the server up, and how many people are playing?")
    async def status_command(self, ctx: discord.ApplicationContext) -> None:
        if not getattr(self.bot, "realm_status", None):
            await ctx.defer()
            await self.update()
        await ctx.respond(embed=status_embed(self.bot))

    @discord.slash_command(name="online", description="Who's playing right now, by faction and class")
    @discord.option("realm", str, description="Realm (defaults to the main realm)", required=False,
                    autocomplete=discord.utils.basic_autocomplete(lambda ctx: ctx.bot.realm_names()))
    async def online_command(self, ctx: discord.ApplicationContext, realm: str | None = None) -> None:
        try:
            realm_cfg = self.bot.config.realm_by_name(realm)
        except KeyError:
            await ctx.respond(embed=ui.error("Unknown realm", ui.safe(realm)), ephemeral=True)
            return
        await ctx.defer()
        rows = await self.bot.mangos.online_characters(realm_cfg)
        factions = Counter(wow.faction(int(r["race"])) for r in rows)
        classes = Counter(int(r["class"]) for r in rows)
        e = ui.embed(f"🌍 {len(rows)} online on {realm_cfg.name}", color=self.bot.config.identity.color)
        if rows:
            e.add_field(
                name="Factions",
                value="\n".join(f"{wow.FACTION_EMOJI[f]} {f}: **{n}**" for f, n in factions.most_common()),
            )
            e.add_field(
                name="Classes",
                value="\n".join(f"{wow.class_name(c)}: **{n}**" for c, n in classes.most_common()),
            )
            if self.cfg.show_online_names:
                visible = []
                for r in rows:
                    if await self.bot.visibility.can_show(int(r["account"])):
                        visible.append(f"{ui.safe(r['name'])} ({r['level']})")
                if visible:
                    e.add_field(name="Adventurers", value=ui.truncate(", ".join(visible), 1024), inline=False)
        else:
            e.description = "Nobody is online right now. Be the first!"
        await ctx.respond(embed=ui.footer(e, self.bot))
