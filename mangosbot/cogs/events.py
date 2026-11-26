"""World event announcements and Discord Scheduled Events, from ``game_event``."""

from __future__ import annotations

import logging
import random
import time
from datetime import datetime, timedelta, timezone, tzinfo
from typing import TYPE_CHECKING

import discord
from discord.ext import tasks

from .. import gameevents, ui
from ..config import RealmConfig
from ..database import DatabaseError
from ..gameevents import GameEvent, Occurrence

if TYPE_CHECKING:
    from ..bot import ServerBot

log = logging.getLogger(__name__)

START_LINES = (
    "🎉 **{name}** has begun! It runs until {end_f} ({end_r}).",
    "Gather round, adventurers: **{name}** is underway and lasts until {end_f}.",
    "{bot_name} brings news: **{name}** is here! Join in before {end_f}.",
    "Dust off your gear, because **{name}** is live until {end_f}.",
)
REMINDER_LINES = (
    "⏳ **{name}** ends {end_r}. Last chance to join in!",
    "Only a little time left for **{name}**. It wraps up {end_r}.",
    "The sands are running out on **{name}**, which ends {end_f}.",
)
END_LINES = (
    "🌙 **{name}** has ended. Thanks to everyone who took part!{next}",
    "The curtain falls on **{name}**. See you next time!{next}",
)
RELOAD_SECONDS = 900
MAX_SCHEDULED_PER_PASS = 10


def _local_tz() -> tzinfo:
    return datetime.now().astimezone().tzinfo or timezone.utc


class EventsCog(discord.Cog, name="Events"):
    def __init__(self, bot: "ServerBot") -> None:
        self.bot = bot
        self.cfg = bot.config.events
        self.events: dict[int, list[GameEvent]] = {}
        self._loaded_at = 0.0
        self._scheduled_ok = self.cfg.scheduled_events

    @property
    def tz(self) -> tzinfo:
        return self.cfg.timezone or _local_tz()

    def cog_unload(self) -> None:
        self.tick.cancel()

    @discord.Cog.listener()
    async def on_ready(self) -> None:
        if not self.tick.is_running():
            self.tick.start()

    # -- data -------------------------------------------------------------------------

    async def reload(self, force: bool = False) -> None:
        if not force and time.monotonic() - self._loaded_at < RELOAD_SECONDS:
            return
        for realm in self.bot.config.realms:
            try:
                rows = await self.bot.mangos.game_events(realm)
            except DatabaseError as exc:
                log.warning("Could not load game_event for %s: %s", realm.name, exc)
                continue
            self.events[realm.id] = [gameevents.from_row(r, self.tz) for r in rows]
        self._loaded_at = time.monotonic()

    def announceable(self, realm: RealmConfig) -> list[GameEvent]:
        return [
            ev for ev in self.events.get(realm.id, [])
            if ev.schedulable and gameevents.is_announceable(
                ev, only_holidays=self.cfg.only_holidays, include=self.cfg.include, exclude=self.cfg.exclude)
        ]

    def find(self, realm: RealmConfig, name: str) -> GameEvent | None:
        name = name.strip().lower()
        for ev in self.events.get(realm.id, []):
            if ev.description.lower() == name or str(ev.entry) == name:
                return ev
        return None

    # -- loop -------------------------------------------------------------------------

    @tasks.loop(minutes=1)
    async def tick(self) -> None:
        try:
            await self.reload()
            now = gameevents.utc_now()
            for realm in self.bot.config.realms:
                await self.process(realm, now)
            await self.bot.storage.prune_occurrences(int(now.timestamp()) - 30 * 86400)
        except Exception:
            log.exception("Event tick failed")

    async def process(self, realm: RealmConfig, now: datetime) -> None:
        storage = self.bot.storage
        grace = timedelta(hours=self.cfg.start_grace_hours)
        reminder = timedelta(hours=self.cfg.reminder_hours)
        events = self.announceable(realm)

        for ev in events:
            occ = gameevents.occurrence_at(ev, now)
            if occ is None:
                continue
            rec = await storage.occurrence(realm.id, ev.entry, occ.key)
            if rec is None or not rec.announced_start:
                await storage.upsert_occurrence(realm.id, ev.entry, occ.key, int(occ.end.timestamp()))
                if now - occ.start <= grace:
                    await self.announce(realm, occ, "start")
                    await storage.upsert_occurrence(realm.id, ev.entry, occ.key, int(occ.end.timestamp()), announced_start=True)
                    if rec and rec.scheduled_event_id:
                        await self._set_scheduled_status(rec.scheduled_event_id, discord.ScheduledEventStatus.active)
                rec = await storage.occurrence(realm.id, ev.entry, occ.key)
            if (rec and rec.announced_start and not rec.announced_reminder and self.cfg.reminder_hours
                    and occ.end - now <= reminder and occ.end - occ.start > 2 * reminder):
                await self.announce(realm, occ, "reminder")
                await storage.upsert_occurrence(realm.id, ev.entry, occ.key, int(occ.end.timestamp()), announced_reminder=True)

        by_entry = {ev.entry: ev for ev in self.events.get(realm.id, [])}
        for rec in await storage.open_occurrences(realm.id):
            if rec.end_ts > now.timestamp():
                continue
            ev = by_entry.get(rec.entry)
            if ev is not None and rec.announced_start:
                occ = Occurrence(ev, datetime.fromtimestamp(rec.start_ts, timezone.utc), datetime.fromtimestamp(rec.end_ts, timezone.utc))
                await self.announce(realm, occ, "end")
            if rec.scheduled_event_id:
                await self._set_scheduled_status(rec.scheduled_event_id, discord.ScheduledEventStatus.completed)
            await storage.upsert_occurrence(realm.id, rec.entry, rec.start_ts, rec.end_ts, announced_end=True)

        if self._scheduled_ok:
            await self._schedule_upcoming(realm, events, now)

    # -- output -------------------------------------------------------------------------

    def _channel(self):
        channels = self.bot.config.channels
        return self.bot.channel(channels.events) or self.bot.channel(channels.announcements)

    def _message(self, occ: Occurrence, kind: str, now: datetime) -> str:
        lines = {"start": START_LINES, "reminder": REMINDER_LINES, "end": END_LINES}[kind]
        nxt = ""
        if kind == "end":
            when = gameevents.next_start(occ.event, now)
            if when:
                nxt = f" It returns {ui.ts(when, 'R')}."
        return random.choice(lines).format(
            name=ui.safe(occ.event.description), end_f=ui.ts(occ.end, "F"), end_r=ui.ts(occ.end, "R"),
            bot_name=self.bot.config.identity.name, next=nxt,
        )

    async def announce(self, realm: RealmConfig, occ: Occurrence, kind: str) -> bool:
        channel = self._channel()
        if channel is None:
            return False
        color = {"start": ui.SUCCESS, "reminder": ui.WARNING, "end": ui.MUTED}[kind]
        e = ui.embed(description=self._message(occ, kind, gameevents.utc_now()), color=color)
        if len(self.bot.config.realms) > 1:
            e.set_author(name=realm.name)
        content, mentions = None, discord.AllowedMentions.none()
        if kind == "start" and self.cfg.mention_role:
            content, mentions = f"<@&{self.cfg.mention_role}>", discord.AllowedMentions(roles=True)
        try:
            await channel.send(content=content, embed=ui.footer(e, self.bot), allowed_mentions=mentions)
            return True
        except discord.HTTPException as exc:
            log.warning("Could not announce %s: %s", occ.event.description, exc)
            return False

    async def _schedule_upcoming(self, realm: RealmConfig, events: list[GameEvent], now: datetime) -> None:
        guild = self.bot.guild
        if guild is None:
            return
        horizon = now + timedelta(days=self.cfg.schedule_days_ahead)
        created = 0
        for ev in events:
            for occ in gameevents.upcoming(ev, now + timedelta(minutes=5), horizon, limit=3):
                if created >= MAX_SCHEDULED_PER_PASS:
                    return
                rec = await self.bot.storage.occurrence(realm.id, ev.entry, occ.key)
                if rec and rec.scheduled_event_id:
                    continue
                description = (f"{ev.description} on {realm.name}. Log in to {self.bot.config.server.name} to take part!")
                try:
                    scheduled = await guild.create_scheduled_event(
                        name=ui.truncate(ev.description, 100),
                        description=ui.truncate(description, 1000),
                        start_time=occ.start,
                        end_time=occ.end,
                        location=ui.truncate(f"{self.bot.config.server.name} · {realm.name}", 100),
                        reason="World event schedule",
                    )
                except discord.Forbidden:
                    log.warning("Missing the Manage Events permission; Discord scheduled events disabled.")
                    self._scheduled_ok = False
                    return
                except discord.HTTPException as exc:
                    log.warning("Could not schedule %s: %s", ev.description, exc)
                    return
                if scheduled is None:
                    continue
                created += 1
                await self.bot.storage.upsert_occurrence(realm.id, ev.entry, occ.key, int(occ.end.timestamp()),
                                                         scheduled_event_id=scheduled.id)

    async def _set_scheduled_status(self, event_id: int, status: discord.ScheduledEventStatus) -> None:
        guild = self.bot.guild
        if guild is None:
            return
        event = guild.get_scheduled_event(event_id)
        try:
            if event is None:
                event = await guild.fetch_scheduled_event(event_id)
            if event is not None and event.status not in (discord.ScheduledEventStatus.completed,
                                                          discord.ScheduledEventStatus.canceled):
                await event.edit(status=status)
        except discord.HTTPException as exc:
            log.debug("Could not update scheduled event %s: %s", event_id, exc)

    # -- commands -----------------------------------------------------------------------

    def schedule_embed(self, realm: RealmConfig) -> discord.Embed:
        now = gameevents.utc_now()
        horizon = now + timedelta(days=self.cfg.schedule_days_ahead)
        active: list[Occurrence] = []
        coming: list[Occurrence] = []
        for ev in self.announceable(realm):
            occ = gameevents.occurrence_at(ev, now)
            if occ:
                active.append(occ)
            coming.extend(gameevents.upcoming(ev, now, horizon, limit=2))
        active.sort(key=lambda o: o.end)
        coming.sort(key=lambda o: o.start)
        e = ui.embed(f"📅 World events on {realm.name}", color=self.bot.config.identity.color)
        e.add_field(
            name="Happening now",
            value="\n".join(f"**{ui.safe(o.event.description)}**, ends {ui.ts(o.end, 'R')}" for o in active[:10])
            or "Nothing right now.",
            inline=False,
        )
        e.add_field(
            name=f"Coming up (next {self.cfg.schedule_days_ahead} days)",
            value="\n".join(f"**{ui.safe(o.event.description)}**, {ui.ts(o.start, 'F')} ({ui.ts(o.start, 'R')})"
                            for o in coming[:12]) or "Nothing scheduled.",
            inline=False,
        )
        return ui.footer(e, self.bot)

    @discord.slash_command(name="events", description="World events happening now and coming up")
    @discord.option("realm", str, description="Realm (defaults to the main realm)", required=False,
                    autocomplete=discord.utils.basic_autocomplete(lambda ctx: ctx.bot.realm_names()))
    async def events_command(self, ctx: discord.ApplicationContext, realm: str | None = None) -> None:
        try:
            realm_cfg = self.bot.config.realm_by_name(realm)
        except KeyError:
            await ctx.respond(embed=ui.error("Unknown realm", ui.safe(realm)), ephemeral=True)
            return
        await ctx.defer()
        await self.reload()
        await ctx.respond(embed=self.schedule_embed(realm_cfg))
