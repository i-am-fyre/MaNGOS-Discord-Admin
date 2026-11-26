"""The Discord client: wires configuration, services and cogs together."""

from __future__ import annotations

import hashlib
import logging
import traceback
from typing import Any

import aiohttp
import discord

from . import __version__, ui
from .accounts import AccountService
from .config import Config, RealmConfig
from .database import Database, DatabaseError
from .items import IconService
from .mangos import Mangos
from .privacy import Visibility
from .soap import SoapAuthError, SoapClient, SoapError, SoapUnavailable
from .storage import Storage

log = logging.getLogger(__name__)


class ServerBot(discord.Bot):
    def __init__(self, config: Config) -> None:
        super().__init__(
            # No privileged intents needed: no message content, members or presences.
            intents=discord.Intents.default(),
            # Register commands on the one guild we serve (instant updates).
            debug_guilds=[config.bot.guild_id],
            # Nothing the bot sends pings anyone unless a call opts in explicitly.
            allowed_mentions=discord.AllowedMentions.none(),
        )
        self.config = config
        self.db = Database()
        self.mangos = Mangos(self.db, config)
        self.storage = Storage(config.bot.data_dir / "bot.sqlite3")
        self._http: aiohttp.ClientSession | None = None
        self.soap: dict[int, SoapClient] = {
            realm.id: SoapClient(realm.soap, lambda: self.http_session) for realm in config.realms
        }
        registration_soap = next((self.soap[r.id] for r in config.realms if r.soap.enabled), None)
        self.accounts = AccountService(
            self.mangos,
            registration_soap,
            method=config.registration.method,
            expansion=config.registration.expansion,
        )
        self.visibility = Visibility(self)
        self.icons = IconService(self)
        self._identity_applied = False
        self._add_cogs()

    # -- lifecycle -----------------------------------------------------------------

    def _add_cogs(self) -> None:
        from .cogs.account import AccountCog
        from .cogs.armory import ArmoryCog
        from .cogs.events import EventsCog
        from .cogs.registration import RegistrationCog
        from .cogs.staff import StaffCog
        from .cogs.status import StatusCog
        from .cogs.tickets import TicketsCog

        self.add_cog(RegistrationCog(self))
        self.add_cog(AccountCog(self))
        self.add_cog(StaffCog(self))
        if self.config.status.enabled:
            self.add_cog(StatusCog(self))
        if self.config.tickets.enabled:
            self.add_cog(TicketsCog(self))
        if self.config.events.enabled:
            self.add_cog(EventsCog(self))
        if self.config.armory.enabled or self.config.leaderboard.enabled:
            self.add_cog(ArmoryCog(self))

    async def start_services(self) -> None:
        await self.storage.open()
        self._http = aiohttp.ClientSession(headers={"User-Agent": f"MaNGOS-Discord-Bot/{__version__}"})
        if self.config.armory.enabled:
            await self.icons.load()

    async def close(self) -> None:
        await super().close()
        if self._http is not None and not self._http.closed:
            await self._http.close()
        await self.db.close()
        await self.storage.close()

    @property
    def http_session(self) -> aiohttp.ClientSession:
        if self._http is None:
            raise RuntimeError("HTTP session not started")
        return self._http

    async def on_ready(self) -> None:
        log.info("Logged in as %s (%s); serving guild %s", self.user, getattr(self.user, "id", "?"),
                 self.config.bot.guild_id)
        if self.get_guild(self.config.bot.guild_id) is None:
            log.error("The bot is not in guild %s. Invite it, or fix bot.guild_id.", self.config.bot.guild_id)
            return
        if not self._identity_applied:
            self._identity_applied = True
            await self.apply_identity()

    async def apply_identity(self) -> None:
        """Present the bot as configured in [identity]: server nickname and (optionally) avatar."""
        identity = self.config.identity
        guild = self.guild
        if identity.set_nickname and guild is not None and guild.me is not None and guild.me.nick != identity.name:
            try:
                await guild.me.edit(nick=identity.name, reason="Bot identity from config.toml")
                log.info("Server nickname set to %r", identity.name)
            except discord.HTTPException as exc:
                log.warning("Could not set the bot's nickname (needs Change Nickname): %s", exc)
        if identity.avatar is not None and self.user is not None:
            data = identity.avatar.read_bytes()
            digest = hashlib.sha256(data).hexdigest()
            # The avatar is global to the bot account and rate-limited, so only
            # upload when the configured image actually changed.
            if await self.storage.get("identity:avatar_sha256") != digest:
                try:
                    await self.user.edit(avatar=data)
                    await self.storage.set("identity:avatar_sha256", digest)
                    log.info("Bot avatar updated from %s", identity.avatar)
                except discord.HTTPException as exc:
                    log.warning("Could not update the bot's avatar: %s", exc)

    # -- helpers ---------------------------------------------------------------------

    def soap_for(self, realm: RealmConfig) -> SoapClient:
        return self.soap[realm.id]

    @property
    def guild(self) -> discord.Guild | None:
        return self.get_guild(self.config.bot.guild_id)

    def channel(self, channel_id: int) -> Any:
        return self.get_channel(channel_id) if channel_id else None

    def realm_names(self) -> list[str]:
        return [r.name for r in self.config.realms]

    async def audit(self, title: str, description: str, *, actor: discord.abc.User | None = None,
                    color: int = ui.INFO) -> None:
        """Record a staff/security action in the log and the audit channel."""
        log.info("AUDIT %s: %s (by %s)", title, description, getattr(actor, "id", "system"))
        channel = self.channel(self.config.channels.audit_log)
        if channel is None:
            return
        e = ui.embed(title, description, color=color)
        if actor is not None:
            e.set_author(name=str(actor), icon_url=actor.display_avatar.url)
            e.add_field(name="By", value=f"{actor.mention} (`{actor.id}`)")
        e.timestamp = discord.utils.utcnow()
        try:
            await channel.send(embed=e)
        except discord.HTTPException as exc:
            log.warning("Could not write to the audit channel: %s", exc)

    # -- error handling --------------------------------------------------------------

    async def on_application_command_error(self, ctx: discord.ApplicationContext, exception: discord.DiscordException) -> None:
        original = getattr(exception, "original", exception)
        if isinstance(exception, discord.CheckFailure):
            e = ui.error("Not allowed", str(exception) or "You can't use this command.")
        elif isinstance(original, discord.CheckFailure):  # raised inside a command, e.g. unknown realm
            e = ui.error("Can't do that", str(original) or "That isn't possible.")
        elif isinstance(original, SoapAuthError):
            log.error("SOAP authentication failed: %s", original)
            e = ui.error("World server refused the bot", "The bot's SOAP login was rejected. An admin needs to check it.")
        elif isinstance(original, SoapUnavailable):
            e = ui.error("World server unreachable", "The world server isn't responding right now. Try again shortly.")
        elif isinstance(original, SoapError):
            e = ui.error("The server said no", ui.safe(str(original), 1500))
        elif isinstance(original, DatabaseError):
            log.error("Database error in /%s: %s", ctx.command.qualified_name if ctx.command else "?", original)
            e = ui.error("Database unavailable", "The game database didn't answer. Try again in a moment.")
        else:
            log.error("Error in /%s", ctx.command.qualified_name if ctx.command else "?",
                      exc_info=(type(original), original, original.__traceback__))
            e = ui.error("Something went wrong", "The error was logged for the admins.")
        try:
            await ctx.respond(embed=e, ephemeral=True)
        except discord.HTTPException:
            pass

    async def on_error(self, event_method: str, *args: Any, **kwargs: Any) -> None:
        log.error("Unhandled error in %s:\n%s", event_method, traceback.format_exc())
