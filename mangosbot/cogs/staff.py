"""/mod (staff) and /admin (admin) command groups."""

from __future__ import annotations

import ipaddress
import logging
import re
import time
from typing import TYPE_CHECKING

import discord

from .. import accounts, gameevents, ui
from ..config import RealmConfig
from ..database import DatabaseError
from ..soap import SoapAuthError, SoapError, SoapUnavailable, clean_text, quote_arg

if TYPE_CHECKING:
    from ..bot import ServerBot

log = logging.getLogger(__name__)

# mangos' TimeStringToSecs silently turns anything it can't parse (e.g. "7"
# with no unit) into 0, and 0 means *permanent*. Only accept unambiguous input.
_DURATION_RE = re.compile(r"^(\d+[dhms])+$")
_PERMANENT = {"perm", "permanent", "forever", "-1"}


def parse_ban_duration(text: str) -> str | None:
    """Return the mangos time string ("-1" = permanent), or None if invalid."""
    text = text.strip().lower().replace(" ", "")
    if text in _PERMANENT:
        return "-1"
    return text if _DURATION_RE.match(text) else None


def _realm_option():
    return discord.option(
        "realm", str, description="Realm (defaults to the main realm)", required=False,
        autocomplete=discord.utils.basic_autocomplete(lambda ctx: ctx.bot.realm_names()),
    )


async def _character_names(ctx: discord.AutocompleteContext) -> list[str]:
    typed = (ctx.value or "").strip()
    if len(typed) < 2:
        return []
    realm_name = ctx.options.get("realm")
    try:
        realm = ctx.bot.config.realm_by_name(realm_name)
        return await ctx.bot.mangos.search_character_names(realm, typed, 25, set(), None)
    except (KeyError, DatabaseError):
        return []


class StaffCog(discord.Cog, name="Staff"):
    mod = discord.SlashCommandGroup("mod", "Staff tools", default_member_permissions=ui.STAFF_PERMISSIONS)
    admin = discord.SlashCommandGroup("admin", "Server admin tools", default_member_permissions=ui.ADMIN_PERMISSIONS)

    def __init__(self, bot: "ServerBot") -> None:
        self.bot = bot

    def _realm(self, name: str | None) -> RealmConfig:
        try:
            return self.bot.config.realm_by_name(name)
        except KeyError:
            raise discord.CheckFailure(f"Unknown realm {name!r}.") from None

    async def _soap(self, realm: RealmConfig, command: str) -> str:
        return await self.bot.soap_for(realm).execute(command)

    # ================================================================ /mod

    @mod.command(name="tickets", description="Open GM tickets and who has claimed them")
    @ui.staff_only()
    async def mod_tickets(self, ctx: discord.ApplicationContext) -> None:
        cog = self.bot.get_cog("Tickets")
        if cog is None:
            await ctx.respond(embed=ui.error("Tickets are turned off"), ephemeral=True)
            return
        await ctx.respond(embed=await cog.overview_embed(), ephemeral=True)

    @mod.command(name="whois", description="Which game accounts a Discord member has linked")
    @ui.staff_only()
    @discord.option("member", discord.Member, description="Discord member")
    async def mod_whois(self, ctx: discord.ApplicationContext, member: discord.Member) -> None:
        await ctx.defer(ephemeral=True)
        links = await self.bot.storage.links_for(member.id)
        e = ui.embed(f"🔎 {member.display_name}", color=ui.INFO)
        e.set_thumbnail(url=member.display_avatar.url)
        e.add_field(name="Discord", value=f"{member.mention} · joined {ui.ts(member.joined_at)} · "
                                          f"account created {ui.ts(member.created_at)}", inline=False)
        if not links:
            e.add_field(name="Game accounts", value="None linked.", inline=False)
        for link in links:
            detail = f"id {link.account_id} · {link.origin} {ui.ts(link.created_at)}"
            try:
                row = await self.bot.mangos.account_by_id(link.account_id)
                ban = await self.bot.mangos.active_ban(link.account_id)
                if row is None:
                    detail += " · **deleted**"
                else:
                    detail += f" · gmlevel {row['gmlevel']}"
                if ban:
                    detail += f" · ⛔ banned: {ui.safe(ban['banreason'], 100)}"
            except DatabaseError:
                detail += " · (database unavailable)"
            e.add_field(name=link.username, value=detail, inline=False)
        await ctx.respond(embed=e, ephemeral=True)

    @mod.command(name="kick", description="Kick a character from the game")
    @ui.staff_only()
    @discord.option("character", str, description="Character name", autocomplete=_character_names)
    @_realm_option()
    async def mod_kick(self, ctx: discord.ApplicationContext, character: str, realm: str | None = None) -> None:
        await ctx.defer(ephemeral=True)
        realm_cfg = self._realm(realm)
        row = await self.bot.mangos.character_by_name(realm_cfg, character.strip())
        if row is None:
            await ctx.respond(embed=ui.error("Not found", f"No character named {ui.safe(character)}."), ephemeral=True)
            return
        output = await self._soap(realm_cfg, f"kick {row['name']}")
        await self.bot.audit("Character kicked", f"**{row['name']}** on {realm_cfg.name}.", actor=ctx.author, color=ui.WARNING)
        await ctx.respond(embed=ui.success(f"Kicked {row['name']}", ui.safe(output, 1000) or None), ephemeral=True)

    @mod.command(name="serverinfo", description="Show the world server's own status report")
    @ui.staff_only()
    @_realm_option()
    async def mod_serverinfo(self, ctx: discord.ApplicationContext, realm: str | None = None) -> None:
        await ctx.defer(ephemeral=True)
        realm_cfg = self._realm(realm)
        output = await self._soap(realm_cfg, "server info")
        await ctx.respond(embed=ui.embed(f"🖥️ {realm_cfg.name}", f"```\n{ui.truncate(output, 3900)}\n```"), ephemeral=True)

    # ================================================================ /admin

    @admin.command(name="panel", description="Post (or refresh) the registration panel")
    @ui.admin_only()
    @discord.option("channel", discord.TextChannel, description="Where to post it (default: here)", required=False)
    async def admin_panel(self, ctx: discord.ApplicationContext, channel: discord.TextChannel | None = None) -> None:
        target = channel or ctx.channel
        cog = self.bot.get_cog("Registration")
        await ctx.defer(ephemeral=True)
        msg = await cog.post_panel(target)
        await self.bot.audit("Panel posted", f"Registration panel in {target.mention}.", actor=ctx.author)
        await ctx.respond(embed=ui.success("Panel ready", f"[Jump to it]({msg.jump_url})"), ephemeral=True)

    @admin.command(name="announce", description="Announce something on Discord and/or in game")
    @ui.admin_only()
    @discord.option("message", str, description="What to announce", max_length=1500)
    @discord.option("in_game", bool, description="Broadcast in game too (max 255 characters)", default=True)
    @discord.option("on_discord", bool, description="Post in the announcements channel", default=True)
    @discord.option("ping", discord.Role, description="Role to ping on Discord", required=False)
    async def admin_announce(self, ctx: discord.ApplicationContext, message: str, in_game: bool = True,
                             on_discord: bool = True, ping: discord.Role | None = None) -> None:
        await ctx.defer(ephemeral=True)
        results: list[str] = []
        if in_game:
            text = clean_text(message, 1000)
            if len(text) > 255:
                await ctx.respond(embed=ui.error("Too long for in-game", "In-game announcements are limited to 255 characters."), ephemeral=True)
                return
            for realm in self.bot.config.realms:
                if not realm.soap.enabled:
                    continue
                try:
                    await self._soap(realm, f"announce {text}")
                    results.append(f"🎮 {realm.name}: sent")
                except SoapError as exc:
                    results.append(f"🎮 {realm.name}: failed ({ui.safe(str(exc), 100)})")
        if on_discord:
            channel = self.bot.channel(self.bot.config.channels.announcements)
            if channel is None:
                results.append("💬 Discord: no announcements channel configured")
            else:
                e = ui.embed("📣 Announcement", message, color=self.bot.config.identity.color)
                e.set_author(name=ctx.author.display_name, icon_url=ctx.author.display_avatar.url)
                e.timestamp = discord.utils.utcnow()
                content = ping.mention if ping else None
                mentions = discord.AllowedMentions(roles=[ping], everyone=ping.is_default()) if ping else discord.AllowedMentions.none()
                msg = await channel.send(content=content, embed=ui.footer(e, self.bot), allowed_mentions=mentions)
                results.append(f"💬 Discord: [posted]({msg.jump_url})")
        await self.bot.audit("Announcement", ui.truncate(message, 1500), actor=ctx.author)
        await ctx.respond(embed=ui.success("Announced", "\n".join(results) or "Nothing selected."), ephemeral=True)

    async def _shutdown(self, ctx: discord.ApplicationContext, kind: str, minutes: int, reason: str, realm: str | None) -> None:
        await ctx.defer(ephemeral=True)
        realm_cfg = self._realm(realm)
        seconds = minutes * 60
        await self._soap(realm_cfg, f"server {kind} {seconds}")
        when = int(time.time()) + seconds
        reason = clean_text(reason, 200)
        if reason:
            try:
                await self._soap(realm_cfg, f"announce {kind.capitalize()} in {minutes} minute(s): {reason}")
            except SoapError:
                pass
        channel = self.bot.channel(self.bot.config.channels.status) or self.bot.channel(self.bot.config.channels.announcements)
        verb = "restarts" if kind == "restart" else "shuts down"
        if channel is not None:
            e = ui.embed(f"🔄 {realm_cfg.name} {verb} {ui.ts(when, 'R')}", ui.safe(reason) or None, color=ui.WARNING)
            e.add_field(name="At", value=ui.ts(when, "F"))
            await channel.send(embed=ui.footer(e, self.bot))
        await self.bot.audit(f"Server {kind}", f"{realm_cfg.name} in {minutes} min. {ui.safe(reason)}", actor=ctx.author, color=ui.WARNING)
        await ctx.respond(embed=ui.success(f"{kind.capitalize()} scheduled", f"{realm_cfg.name} {verb} {ui.ts(when, 'R')}."), ephemeral=True)

    @admin.command(name="restart", description="Restart the world server after a countdown")
    @ui.admin_only()
    @discord.option("minutes", int, description="Countdown in minutes", min_value=0, max_value=1440)
    @discord.option("reason", str, description="Shown in game and on Discord", required=False, default="")
    @_realm_option()
    async def admin_restart(self, ctx: discord.ApplicationContext, minutes: int, reason: str = "", realm: str | None = None) -> None:
        await self._shutdown(ctx, "restart", minutes, reason, realm)

    @admin.command(name="shutdown", description="Shut the world server down after a countdown")
    @ui.admin_only()
    @discord.option("minutes", int, description="Countdown in minutes", min_value=0, max_value=1440)
    @discord.option("reason", str, description="Shown in game and on Discord", required=False, default="")
    @_realm_option()
    async def admin_shutdown(self, ctx: discord.ApplicationContext, minutes: int, reason: str = "", realm: str | None = None) -> None:
        await self._shutdown(ctx, "shutdown", minutes, reason, realm)

    @admin.command(name="cancel", description="Cancel a pending restart or shutdown")
    @ui.admin_only()
    @_realm_option()
    async def admin_cancel(self, ctx: discord.ApplicationContext, realm: str | None = None) -> None:
        await ctx.defer(ephemeral=True)
        realm_cfg = self._realm(realm)
        await self._soap(realm_cfg, "server shutdown cancel")
        channel = self.bot.channel(self.bot.config.channels.status) or self.bot.channel(self.bot.config.channels.announcements)
        if channel is not None:
            await channel.send(embed=ui.footer(ui.embed(f"✅ {realm_cfg.name}: restart cancelled", color=ui.SUCCESS), self.bot))
        await self.bot.audit("Restart cancelled", realm_cfg.name, actor=ctx.author)
        await ctx.respond(embed=ui.success("Cancelled"), ephemeral=True)

    @admin.command(name="ban", description="Ban an account, character or IP")
    @ui.admin_only()
    @discord.option("kind", str, description="What to ban", choices=["account", "character", "ip"])
    @discord.option("target", str, description="Account name, character name or IP address")
    @discord.option("duration", str, description="e.g. 30m, 12h, 7d, 1d12h, or perm")
    @discord.option("reason", str, description="Reason (stored with the ban)", max_length=200)
    @_realm_option()
    async def admin_ban(self, ctx: discord.ApplicationContext, kind: str, target: str, duration: str, reason: str,
                        realm: str | None = None) -> None:
        await ctx.defer(ephemeral=True)
        realm_cfg = self._realm(realm)
        time_str = parse_ban_duration(duration)
        if time_str is None:
            await ctx.respond(embed=ui.error("Unclear duration", "Use units, like `30m`, `12h`, `7d` or `1d12h`, or `perm` for permanent."), ephemeral=True)
            return
        name = await self._ban_target(realm_cfg, kind, target)
        if name is None:
            await ctx.respond(embed=ui.error("Not found", f"No {kind} matching {ui.safe(target)}."), ephemeral=True)
            return
        output = await self._soap(realm_cfg, f"ban {kind} {name} {time_str} {quote_arg(reason, 200)}")
        shown = "permanently" if time_str == "-1" else f"for {time_str}"
        await self.bot.audit("Ban", f"{kind} **{name}** {shown}: {ui.safe(reason)}", actor=ctx.author, color=ui.ERROR)
        await ctx.respond(embed=ui.success(f"Banned {name} {shown}", ui.safe(output, 1000) or None), ephemeral=True)

    @admin.command(name="unban", description="Lift a ban")
    @ui.admin_only()
    @discord.option("kind", str, description="What to unban", choices=["account", "character", "ip"])
    @discord.option("target", str, description="Account name, character name or IP address")
    @_realm_option()
    async def admin_unban(self, ctx: discord.ApplicationContext, kind: str, target: str, realm: str | None = None) -> None:
        await ctx.defer(ephemeral=True)
        realm_cfg = self._realm(realm)
        name = await self._ban_target(realm_cfg, kind, target)
        if name is None:
            await ctx.respond(embed=ui.error("Not found", f"No {kind} matching {ui.safe(target)}."), ephemeral=True)
            return
        output = await self._soap(realm_cfg, f"unban {kind} {name}")
        await self.bot.audit("Unban", f"{kind} **{name}**", actor=ctx.author, color=ui.SUCCESS)
        await ctx.respond(embed=ui.success(f"Unbanned {name}", ui.safe(output, 1000) or None), ephemeral=True)

    async def _ban_target(self, realm: RealmConfig, kind: str, target: str) -> str | None:
        """Resolve user input to a canonical, safe command argument."""
        target = target.strip()
        if kind == "ip":
            try:
                return str(ipaddress.ip_address(target))
            except ValueError:
                return None
        if kind == "account":
            name = accounts.normalize_username(target)
            if accounts.validate_username(name) is not None:
                return None
            row = await self.bot.mangos.account_by_name(name)
            return str(row["username"]) if row else None
        row = await self.bot.mangos.character_by_name(realm, target)
        return str(row["name"]) if row else None

    @admin.command(name="unlink", description="Remove a game account link from a Discord member")
    @ui.admin_only()
    @discord.option("member", discord.Member, description="Discord member")
    @discord.option("account", str, description="Game account name")
    async def admin_unlink(self, ctx: discord.ApplicationContext, member: discord.Member, account: str) -> None:
        name = accounts.normalize_username(account)
        link = next((l for l in await self.bot.storage.links_for(member.id) if l.username == name), None)
        if link is None or not await self.bot.storage.remove_link(member.id, link.account_id):
            await ctx.respond(embed=ui.error("Not linked", f"{member.mention} has no link to **{ui.safe(name)}**."), ephemeral=True)
            return
        self.bot.visibility.invalidate()
        await self.bot.audit("Link removed", f"{member.mention} ✂️ **{name}**", actor=ctx.author)
        await ctx.respond(embed=ui.success("Unlinked", f"{member.mention} is no longer linked to **{name}**."), ephemeral=True)

    @admin.command(name="event-announce", description="Announce a world event now")
    @ui.admin_only()
    @discord.option("event", str, description="Event name", autocomplete=discord.utils.basic_autocomplete(
        lambda ctx: sorted({e.description for evs in getattr(ctx.bot.get_cog("Events"), "events", {}).values() for e in evs})[:200]))
    @_realm_option()
    async def admin_event_announce(self, ctx: discord.ApplicationContext, event: str, realm: str | None = None) -> None:
        cog = self.bot.get_cog("Events")
        if cog is None:
            await ctx.respond(embed=ui.error("Events are turned off"), ephemeral=True)
            return
        await ctx.defer(ephemeral=True)
        realm_cfg = self._realm(realm)
        await cog.reload()
        ev = cog.find(realm_cfg, event)
        if ev is None:
            await ctx.respond(embed=ui.error("Unknown event", ui.safe(event)), ephemeral=True)
            return
        now = gameevents.utc_now()
        occ = gameevents.occurrence_at(ev, now)
        if occ is None:
            await ctx.respond(embed=ui.warning("Not running", f"**{ui.safe(ev.description)}** isn't active right now."), ephemeral=True)
            return
        posted = await cog.announce(realm_cfg, occ, "start")
        await self.bot.storage.upsert_occurrence(realm_cfg.id, ev.entry, occ.key, int(occ.end.timestamp()), announced_start=True)
        await ctx.respond(embed=ui.success("Announced") if posted else ui.error("No events channel configured"), ephemeral=True)

    @admin.command(name="sync", description="Refresh status, tickets and events now")
    @ui.admin_only()
    async def admin_sync(self, ctx: discord.ApplicationContext) -> None:
        await ctx.defer(ephemeral=True)
        done = []
        for name, call in (("Status", "update"), ("Tickets", "sync"), ("Events", "reload")):
            cog = self.bot.get_cog(name)
            if cog is None:
                continue
            if name == "Events":
                await cog.reload(force=True)
            else:
                await getattr(cog, call)()
            done.append(name)
        await ctx.respond(embed=ui.success("Synced", ", ".join(done) or "Nothing enabled."), ephemeral=True)

    @admin.command(name="check", description="Check database, SOAP, channels and permissions")
    @ui.admin_only()
    async def admin_check(self, ctx: discord.ApplicationContext) -> None:
        await ctx.defer(ephemeral=True)
        await ctx.respond(embed=await self.health_embed(), ephemeral=True)

    async def health_embed(self) -> discord.Embed:
        cfg = self.bot.config
        ok, warn, bad = "✅", "⚠️", "❌"
        sections: dict[str, list[str]] = {"Databases": [], "SOAP": [], "Discord": [], "Features": []}

        try:
            sections["Databases"].append(f"{ok} realmd `{cfg.realmd_db}`: {await self.bot.mangos.ping_realmd()} accounts")
        except DatabaseError as exc:
            sections["Databases"].append(f"{bad} realmd `{cfg.realmd_db}`: {ui.safe(str(exc), 150)}")
        for realm in cfg.realms:
            try:
                n = await self.bot.mangos.ping_characters(realm)
                sections["Databases"].append(f"{ok} {realm.name} characters `{realm.characters_db}`: {n} characters")
            except DatabaseError as exc:
                sections["Databases"].append(f"{bad} {realm.name} characters: {ui.safe(str(exc), 150)}")
            try:
                n = await self.bot.mangos.ping_world(realm)
                sections["Databases"].append(f"{ok} {realm.name} world `{realm.world_db}`: {n} world events")
            except DatabaseError as exc:
                sections["Databases"].append(f"{bad} {realm.name} world: {ui.safe(str(exc), 150)}")

            soap = self.bot.soap_for(realm)
            if not soap.enabled:
                sections["SOAP"].append(f"{warn} {realm.name}: disabled (no ticket replies, in-game announcements, kicks or bans)")
                continue
            try:
                out = await soap.execute("server info")
                sections["SOAP"].append(f"{ok} {realm.name}: {ui.safe(out.splitlines()[0] if out else 'connected', 120)}")
            except SoapAuthError as exc:
                sections["SOAP"].append(f"{bad} {realm.name}: {exc}")
            except SoapUnavailable:
                sections["SOAP"].append(f"{bad} {realm.name}: not reachable at {ui.safe(realm.soap.url)}")
            except SoapError as exc:
                sections["SOAP"].append(f"{warn} {realm.name}: {ui.safe(str(exc), 120)}")

        guild = self.bot.guild
        me = guild.me if guild else None
        if guild is None or me is None:
            sections["Discord"].append(f"{bad} The bot isn't in guild {cfg.bot.guild_id}.")
        else:
            def check_channel(label: str, channel_id: int, needs: dict[str, str], kind: type | tuple | None = None) -> None:
                if not channel_id:
                    sections["Discord"].append(f"{warn} {label}: not set")
                    return
                channel = guild.get_channel(channel_id)
                if channel is None:
                    sections["Discord"].append(f"{bad} {label}: channel {channel_id} not found")
                    return
                if kind and not isinstance(channel, kind):
                    sections["Discord"].append(f"{bad} {label}: {channel.mention} is the wrong channel type")
                    return
                perms = channel.permissions_for(me)
                missing = [human for attr, human in needs.items() if not getattr(perms, attr)]
                mark = bad if missing else ok
                sections["Discord"].append(f"{mark} {label}: {channel.mention}" + (f" (missing {', '.join(missing)})" if missing else ""))

            post = {"view_channel": "View Channel", "send_messages": "Send Messages", "embed_links": "Embed Links"}
            check_channel("Announcements", cfg.channels.announcements, post)
            check_channel("Status alerts", cfg.channels.status, post)
            check_channel("Audit log", cfg.channels.audit_log, post)
            check_channel("Events", cfg.channels.events, post)
            if cfg.tickets.enabled:
                check_channel("Tickets forum", cfg.channels.tickets_forum,
                              {**post, "manage_threads": "Manage Threads", "send_messages_in_threads": "Send in Threads"},
                              discord.ForumChannel)
            if cfg.status.voice_counter_minutes:
                check_channel("Player counter", cfg.channels.player_count_voice,
                              {"view_channel": "View Channel", "manage_channels": "Manage Channel"},
                              (discord.VoiceChannel, discord.StageChannel))
            if cfg.roles.player:
                role = guild.get_role(cfg.roles.player)
                if role is None:
                    sections["Discord"].append(f"{bad} Player role {cfg.roles.player} not found")
                elif not me.guild_permissions.manage_roles or role >= me.top_role:
                    sections["Discord"].append(f"{bad} Player role {role.mention}: needs Manage Roles and the bot's role above it")
                else:
                    sections["Discord"].append(f"{ok} Player role {role.mention}")
            if cfg.events.enabled and cfg.events.scheduled_events:
                mark = ok if me.guild_permissions.manage_events else bad
                sections["Discord"].append(f"{mark} Manage Events (for Discord scheduled events)")
            if cfg.identity.set_nickname:
                if me.nick == cfg.identity.name:
                    sections["Discord"].append(f"{ok} Identity: shown as **{ui.safe(cfg.identity.name)}**")
                elif not me.guild_permissions.change_nickname:
                    sections["Discord"].append(f"{bad} Identity: needs Change Nickname to appear as **{ui.safe(cfg.identity.name)}**")
                else:
                    sections["Discord"].append(f"{warn} Identity: nickname will be set to **{ui.safe(cfg.identity.name)}** on next start")
            if not cfg.roles.admin and not cfg.roles.staff:
                sections["Discord"].append(f"{warn} No staff/admin roles configured: only Discord administrators can use staff tools")

        reg = cfg.registration
        sections["Features"].append(
            f"{ok if reg.enabled else warn} Registration {'on' if reg.enabled else 'off'} via {reg.method}, "
            f"passwords {reg.password_mode}, expansion {reg.expansion}")
        if reg.method == "soap":
            sections["Features"].append(f"{warn} SOAP registration writes passwords to the mangosd log")
        if cfg.armory.enabled:
            if self.bot.icons.names:
                sections["Features"].append(f"{ok} Armory icons: {len(self.bot.icons.names):,} from ItemDisplayInfo.dbc")
            else:
                sections["Features"].append(f"{warn} Armory icons off: {ui.safe(self.bot.icons.load_error or 'unknown')}")
            if self.bot.spells is not None:
                sections["Features"].append(f"{ok} Item effect text: {len(self.bot.spells):,} spells from Spell.dbc")
            else:
                sections["Features"].append(f"{warn} Item effect text off: {ui.safe(self.bot.spells_error or 'unknown')}")
        sections["Features"].append(f"{ok} Panels: {len(await self.bot.storage.panels())}")

        e = ui.embed("🩺 Health check", color=ui.INFO)
        for name, lines in sections.items():
            if lines:
                e.add_field(name=name, value=ui.truncate("\n".join(lines), 1024), inline=False)
        return ui.footer(e, self.bot)
