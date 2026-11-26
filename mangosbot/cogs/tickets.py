"""GM tickets mirrored into a Discord forum channel.

Each open ``character_ticket`` gets a forum thread with Claim / Respond / Close
buttons. Changes always go through SOAP (``ticket respond`` / ``ticket close``)
because mangosd caches tickets in memory: writing to the table directly would
be ignored and later overwritten by the server.

``ticket respond <id> <text>`` mails the response to the player in game *and*
closes the ticket; ``ticket close <id>`` closes it without a response.
"""

from __future__ import annotations

import hashlib
import logging
import time
from datetime import datetime
from typing import TYPE_CHECKING, Any

import discord
from discord.ext import tasks

from .. import ui, wow
from ..config import RealmConfig
from ..database import DatabaseError
from ..soap import SoapError, SoapUnavailable, clean_text
from ..storage import TicketRecord

if TYPE_CHECKING:
    from ..bot import ServerBot

log = logging.getLogger(__name__)

PREFIX = "mb:tk:"
TAG_NAMES = {"open": "Open", "claimed": "Claimed", "closed": "Closed"}
TAG_EMOJI = {"open": "🟠", "claimed": "🔵", "closed": "✅"}
STATE_COLORS = {"open": 0xE67E22, "claimed": 0x3498DB, "closed": 0x95A5A6}
RESPONSE_MAX = 1000


def _hash(text: str | None) -> str:
    return hashlib.sha1((text or "").encode("utf-8", "replace")).hexdigest()


def ticket_buttons(realm_id: int, ticket_id: int, *, closed: bool = False) -> discord.ui.View:
    view = discord.ui.View(timeout=None, store=False)  # handled by the on_interaction router
    view.add_item(discord.ui.Button(label="Claim", emoji="🙋", style=discord.ButtonStyle.primary,
                                    custom_id=f"{PREFIX}claim:{realm_id}:{ticket_id}", disabled=closed))
    view.add_item(discord.ui.Button(label="Respond", emoji="✉️", style=discord.ButtonStyle.success,
                                    custom_id=f"{PREFIX}respond:{realm_id}:{ticket_id}", disabled=closed))
    view.add_item(discord.ui.Button(label="Close", emoji="🔒", style=discord.ButtonStyle.secondary,
                                    custom_id=f"{PREFIX}close:{realm_id}:{ticket_id}", disabled=closed))
    return view


class RespondModal(ui.SafeModal):
    def __init__(self, cog: "TicketsCog", realm: RealmConfig, ticket_id: int) -> None:
        super().__init__(title=f"Respond to ticket #{ticket_id}")
        self.cog, self.realm, self.ticket_id = cog, realm, ticket_id
        self.add_item(discord.ui.InputText(
            label="Response (mailed to the player in game)", custom_id="response",
            style=discord.InputTextStyle.paragraph, min_length=2, max_length=RESPONSE_MAX,
            placeholder="Be courteous and specific. This also closes the ticket.",
        ))

    async def callback(self, interaction: discord.Interaction) -> None:
        await self.cog.respond(interaction, self.realm, self.ticket_id, self.children[0].value or "")


class CloseConfirmView(ui.SafeView):
    def __init__(self, cog: "TicketsCog", realm: RealmConfig, ticket_id: int, owner_id: int) -> None:
        super().__init__(timeout=60)
        self.cog, self.realm, self.ticket_id, self.owner_id = cog, realm, ticket_id, owner_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self.owner_id

    @discord.ui.button(label="Close without a response", style=discord.ButtonStyle.danger, emoji="🔒")
    async def confirm(self, button: discord.ui.Button, interaction: discord.Interaction) -> None:
        self.stop()
        await self.cog.close(interaction, self.realm, self.ticket_id)


class TicketsCog(discord.Cog, name="Tickets"):
    def __init__(self, bot: "ServerBot") -> None:
        self.bot = bot
        self.cfg = bot.config.tickets
        self._warned_forum = False
        self._tags: dict[str, discord.ForumTag] = {}
        self.poll.change_interval(seconds=self.cfg.poll_seconds)

    def cog_unload(self) -> None:
        self.poll.cancel()

    @discord.Cog.listener()
    async def on_ready(self) -> None:
        forum = self.forum()
        if forum is not None:
            await self._ensure_tags(forum)
        if not self.poll.is_running():
            self.poll.start()

    # -- forum plumbing ----------------------------------------------------------------

    def forum(self) -> discord.ForumChannel | None:
        channel = self.bot.channel(self.bot.config.channels.tickets_forum)
        if isinstance(channel, discord.ForumChannel):
            return channel
        if not self._warned_forum:
            self._warned_forum = True
            log.warning("channels.tickets_forum is not set to a forum channel; ticket sync is idle.")
        return None

    async def _ensure_tags(self, forum: discord.ForumChannel) -> None:
        existing = {t.name.lower(): t for t in forum.available_tags}
        missing = [name for name in TAG_NAMES.values() if name.lower() not in existing]
        if missing and forum.permissions_for(forum.guild.me).manage_channels:
            try:
                new = list(forum.available_tags) + [discord.ForumTag(name=n, emoji=TAG_EMOJI[s])
                                                    for s, n in TAG_NAMES.items() if n in missing]
                forum = await forum.edit(available_tags=new[:20]) or forum
                existing = {t.name.lower(): t for t in forum.available_tags}
            except discord.HTTPException as exc:
                log.warning("Could not create ticket tags: %s", exc)
        self._tags = {state: existing[name.lower()] for state, name in TAG_NAMES.items() if name.lower() in existing}

    def _tag(self, state: str) -> list[discord.ForumTag]:
        tag = self._tags.get(state)
        return [tag] if tag else []

    async def _thread(self, thread_id: int) -> discord.Thread | None:
        guild = self.bot.guild
        thread = guild.get_thread(thread_id) if guild else None
        if thread is None:
            try:
                fetched = await self.bot.fetch_channel(thread_id)
                thread = fetched if isinstance(fetched, discord.Thread) else None
            except (discord.NotFound, discord.Forbidden):
                return None
        return thread

    # -- rendering -------------------------------------------------------------------

    def ticket_embed(self, realm: RealmConfig, row: dict[str, Any], record: TicketRecord | None,
                     state: str, response: str | None = None) -> discord.Embed:
        name = row.get("name") or f"character #{row.get('guid')}"
        e = discord.Embed(title=f"Ticket #{row['ticket_id']} · {ui.safe(name)}",
                          description=ui.safe(row.get("ticket_text") or "(empty ticket)", 3800),
                          color=STATE_COLORS[state])
        if row.get("level"):
            e.add_field(name="Character",
                        value=f"Level {row['level']} {wow.race_name(int(row['race']))} {wow.class_name(int(row['class']))}"
                              f" ({wow.faction(int(row['race']))})")
        if len(self.bot.config.realms) > 1:
            e.add_field(name="Realm", value=realm.name)
        changed = row.get("ticket_lastchange")
        if isinstance(changed, datetime):
            tz = self.bot.config.events.timezone
            e.add_field(name="Last updated", value=ui.ts(changed.replace(tzinfo=tz) if tz and changed.tzinfo is None else changed))
        status = {"open": "🟠 Open", "claimed": "🔵 Claimed", "closed": "✅ Closed"}[state]
        if record and record.claimed_by and state != "open":
            status += f" by <@{record.claimed_by}>"
        e.add_field(name="Status", value=status)
        if response:
            e.add_field(name="Response sent", value=ui.safe(response, 1024), inline=False)
        e.set_footer(text="Respond mails the player in game and closes the ticket. Be courteous and professional.")
        return e

    # -- sync ------------------------------------------------------------------------

    @tasks.loop(seconds=60)
    async def poll(self) -> None:
        try:
            await self.sync()
        except Exception:
            log.exception("Ticket sync failed")

    async def sync(self) -> None:
        forum = self.forum()
        if forum is None:
            return
        if not self._tags:
            await self._ensure_tags(forum)
        for realm in self.bot.config.realms:
            try:
                rows = {int(r["ticket_id"]): r for r in await self.bot.mangos.open_tickets(realm)}
            except DatabaseError as exc:
                log.warning("Could not read tickets for %s: %s", realm.name, exc)
                continue
            known = {t.ticket_id: t for t in await self.bot.storage.tickets(realm.id)}
            for ticket_id, row in rows.items():
                record = known.get(ticket_id) or await self.bot.storage.ticket(realm.id, ticket_id)
                if record is None:
                    await self._open_thread(forum, realm, row)
                elif record.state != "closed" and record.text_hash != _hash(row.get("ticket_text")):
                    await self._ticket_updated(realm, row, record)
            for ticket_id, record in known.items():
                if ticket_id not in rows:
                    await self._mark_closed(realm, record, closed_by=None)

    async def _open_thread(self, forum: discord.ForumChannel, realm: RealmConfig, row: dict[str, Any]) -> None:
        ticket_id = int(row["ticket_id"])
        who = row.get("name") or f"#{row.get('guid')}"
        title = ui.truncate(f"#{ticket_id} {who}: {clean_text(row.get('ticket_text') or '', 80)}", 100)
        content, mentions = None, discord.AllowedMentions.none()
        if self.cfg.mention_staff and self.bot.config.roles.staff:
            content = " ".join(f"<@&{r}>" for r in self.bot.config.roles.staff) + " new ticket"
            mentions = discord.AllowedMentions(roles=True)
        try:
            thread = await forum.create_thread(
                name=title, content=content, embed=self.ticket_embed(realm, row, None, "open"),
                view=ticket_buttons(realm.id, ticket_id), applied_tags=self._tag("open"),
                auto_archive_duration=self.cfg.auto_archive_minutes, allowed_mentions=mentions,
            )
        except discord.HTTPException as exc:
            log.warning("Could not create a thread for ticket %s: %s", ticket_id, exc)
            return
        await self.bot.storage.save_ticket(TicketRecord(realm.id, ticket_id, thread.id, "open", None,
                                                        _hash(row.get("ticket_text")), int(time.time())))
        log.info("Ticket %s (%s) -> thread %s", ticket_id, realm.name, thread.id)

    async def _ticket_updated(self, realm: RealmConfig, row: dict[str, Any], record: TicketRecord) -> None:
        thread = await self._thread(record.thread_id)
        updated = TicketRecord(**{**record.__dict__, "text_hash": _hash(row.get("ticket_text"))})
        await self.bot.storage.save_ticket(updated)
        if thread is None:
            return
        try:
            if thread.archived:
                await thread.edit(archived=False)
            await thread.get_partial_message(thread.id).edit(embed=self.ticket_embed(realm, row, updated, record.state))
            await thread.send(embed=ui.embed("✏️ The player updated their ticket",
                                             ui.safe(row.get("ticket_text") or "", 3800), color=STATE_COLORS["open"]))
        except discord.HTTPException as exc:
            log.warning("Could not update thread for ticket %s: %s", record.ticket_id, exc)

    async def _mark_closed(self, realm: RealmConfig, record: TicketRecord, *, closed_by: discord.abc.User | None,
                           response: str | None = None) -> None:
        closed = TicketRecord(**{**record.__dict__, "state": "closed"})
        await self.bot.storage.save_ticket(closed)
        thread = await self._thread(record.thread_id)
        if thread is None:
            return
        try:
            row = await self.bot.mangos.ticket(realm, record.ticket_id)
        except DatabaseError:
            row = None
        row = dict(row or {"ticket_id": record.ticket_id})
        response = response or (row.get("response_text") if row else None)
        try:
            if thread.archived:
                await thread.edit(archived=False)
            full = await self._ticket_row_with_character(realm, row)
            await thread.get_partial_message(thread.id).edit(
                embed=self.ticket_embed(realm, full, closed, "closed", response),
                view=ticket_buttons(realm.id, record.ticket_id, closed=True),
            )
            who = closed_by.mention if closed_by else "in game"
            await thread.send(embed=ui.embed("✅ Ticket closed", f"Closed {who}.", color=STATE_COLORS["closed"]))
            await thread.edit(archived=True, locked=True, applied_tags=self._tag("closed") or thread.applied_tags)
        except discord.HTTPException as exc:
            log.warning("Could not close thread for ticket %s: %s", record.ticket_id, exc)

    async def _ticket_row_with_character(self, realm: RealmConfig, row: dict[str, Any]) -> dict[str, Any]:
        # The closed ticket is no longer in open_tickets(); keep the text we can find.
        if "guid" not in row:
            return row
        try:
            char = await self.bot.mangos.character_by_guid(realm, int(row["guid"]))
        except DatabaseError:
            char = None
        if not char:
            return row
        return {**row, "name": char["name"], "level": char["level"], "race": char["race"], "class": char["class"]}

    # -- buttons -------------------------------------------------------------------

    @discord.Cog.listener()
    async def on_interaction(self, interaction: discord.Interaction) -> None:
        if interaction.type is not discord.InteractionType.component:
            return
        custom_id = (interaction.data or {}).get("custom_id", "")
        if not custom_id.startswith(PREFIX):
            return
        try:
            action, realm_id, ticket_id = custom_id[len(PREFIX):].split(":")
            realm = self.bot.config.realm(int(realm_id))
            ticket = int(ticket_id)
        except (ValueError, KeyError):
            return
        if not ui.is_staff(interaction.user, self.bot.config):
            await ui.reply(interaction, embed=ui.error("Staff only", "Only staff can handle tickets."))
            return
        try:
            if action == "claim":
                await self.claim(interaction, realm, ticket)
            elif action == "respond":
                if not self.bot.soap_for(realm).enabled:
                    await ui.reply(interaction, embed=ui.error("SOAP needed", "Responding needs SOAP enabled for this realm."))
                    return
                await interaction.response.send_modal(RespondModal(self, realm, ticket))
            elif action == "close":
                await ui.reply(interaction, embed=ui.warning(f"Close ticket #{ticket}?", "The player won't get a written response."),
                               view=CloseConfirmView(self, realm, ticket, interaction.user.id))
        except Exception as exc:  # the router runs outside any view, so report here
            await ui.report_failure(interaction, exc, f"ticket {action}")

    async def claim(self, interaction: discord.Interaction, realm: RealmConfig, ticket_id: int) -> None:
        record = await self.bot.storage.ticket(realm.id, ticket_id)
        if record is None or record.state == "closed":
            await ui.reply(interaction, embed=ui.error("Ticket closed", "This ticket is already closed."))
            return
        user = interaction.user
        if record.claimed_by == user.id:
            updated = TicketRecord(**{**record.__dict__, "state": "open", "claimed_by": None})
            note = f"🙌 {user.mention} released this ticket."
        else:
            updated = TicketRecord(**{**record.__dict__, "state": "claimed", "claimed_by": user.id})
            previous = f" (taking over from <@{record.claimed_by}>)" if record.claimed_by else ""
            note = f"🙋 {user.mention} claimed this ticket{previous}."
        await self.bot.storage.save_ticket(updated)
        await interaction.response.defer()
        thread = await self._thread(record.thread_id)
        if thread is not None:
            try:
                rows = {int(r["ticket_id"]): r for r in await self.bot.mangos.open_tickets(realm)}
                row = rows.get(ticket_id)
                if row:
                    await thread.get_partial_message(thread.id).edit(
                        embed=self.ticket_embed(realm, row, updated, updated.state))
                await thread.edit(applied_tags=self._tag(updated.state) or thread.applied_tags)
                await thread.send(note)
            except (discord.HTTPException, DatabaseError) as exc:
                log.warning("Could not update claim on ticket %s: %s", ticket_id, exc)

    async def respond(self, interaction: discord.Interaction, realm: RealmConfig, ticket_id: int, text: str) -> None:
        await interaction.response.defer(ephemeral=True)
        record = await self.bot.storage.ticket(realm.id, ticket_id)
        # In-game mail uses $B for line breaks; anything else that looks like a
        # control character is flattened so it can't become extra command input.
        body = clean_text(text.replace("\r\n", "\n").replace("\n", " $B "), RESPONSE_MAX)
        try:
            await self.bot.soap_for(realm).execute(f"ticket respond {ticket_id} {body}")
        except SoapUnavailable:
            await ui.reply(interaction, embed=ui.error("World server unreachable", "Try again when the server is up."))
            return
        except SoapError as exc:
            await ui.reply(interaction, embed=ui.error("The server refused", ui.safe(str(exc), 1000)))
            return
        await self.bot.audit("Ticket answered", f"Ticket #{ticket_id} on {realm.name}:\n>>> {ui.safe(text, 1500)}",
                             actor=interaction.user)
        await ui.reply(interaction, embed=ui.success("Response sent", "The player gets it as in-game mail."))
        if record:
            await self._mark_closed(realm, record, closed_by=interaction.user, response=text)

    async def close(self, interaction: discord.Interaction, realm: RealmConfig, ticket_id: int) -> None:
        await interaction.response.defer(ephemeral=True)
        record = await self.bot.storage.ticket(realm.id, ticket_id)
        try:
            await self.bot.soap_for(realm).execute(f"ticket close {ticket_id}")
        except SoapUnavailable:
            await ui.reply(interaction, embed=ui.error("World server unreachable", "Try again when the server is up."))
            return
        except SoapError as exc:
            await ui.reply(interaction, embed=ui.error("The server refused", ui.safe(str(exc), 1000)))
            return
        await self.bot.audit("Ticket closed", f"Ticket #{ticket_id} on {realm.name} closed without a response.",
                             actor=interaction.user)
        await ui.reply(interaction, embed=ui.success("Ticket closed"))
        if record:
            await self._mark_closed(realm, record, closed_by=interaction.user)

    # -- staff overview (used by /mod tickets) ---------------------------------------

    async def overview_embed(self) -> discord.Embed:
        records = await self.bot.storage.tickets()
        e = ui.embed(f"🎫 {len(records)} open ticket{'s' if len(records) != 1 else ''}", color=STATE_COLORS["open"])
        if not records:
            e.description = "All quiet. Nice work!"
        lines = []
        for r in records[:30]:
            who = f"claimed by <@{r.claimed_by}>" if r.claimed_by else "unclaimed"
            realm = self.bot.config.realm(r.realm_id).name if len(self.bot.config.realms) > 1 else ""
            lines.append(f"<#{r.thread_id}> · #{r.ticket_id} {realm} · {who} · opened {ui.ts(r.created_at)}")
        if lines:
            e.description = "\n".join(lines)
        return ui.footer(e, self.bot)
