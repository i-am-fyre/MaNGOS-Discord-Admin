"""Registration panel, /register and /connect."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from typing import TYPE_CHECKING

import discord

from .. import accounts, ui
from ..accounts import AccountError, AccountExistsError
from ..database import DatabaseError
from ..soap import SoapError
from .status import status_icon, status_summary

if TYPE_CHECKING:
    from ..bot import ServerBot

log = logging.getLogger(__name__)

REGISTER_TRIES_PER_HOUR = 10  # attempts that reach the database, per Discord user

PANEL_REGISTER = "mb:reg:register"
PANEL_CONNECT = "mb:reg:connect"
PANEL_ACCOUNT = "mb:reg:account"


class PanelView(ui.SafeView):
    """The persistent panel buttons. Fixed custom_ids keep them working after restarts."""

    def __init__(self, cog: "RegistrationCog") -> None:
        super().__init__(timeout=None)
        self.cog = cog
        register = discord.ui.Button(label="Register", emoji="📝", style=discord.ButtonStyle.success, custom_id=PANEL_REGISTER)
        register.callback = cog.start_registration
        connect = discord.ui.Button(label="How to connect", emoji="🔌", style=discord.ButtonStyle.secondary, custom_id=PANEL_CONNECT)
        connect.callback = cog.send_connect_guide
        account = discord.ui.Button(label="My account", emoji="👤", style=discord.ButtonStyle.secondary, custom_id=PANEL_ACCOUNT)
        account.callback = cog.send_my_account
        for item in (register, connect, account):
            self.add_item(item)
        website = cog.bot.config.server.website_url
        if website:
            self.add_item(discord.ui.Button(label="Website", emoji="🌐", url=website))


class RegisterModal(ui.SafeModal):
    def __init__(self, cog: "RegistrationCog") -> None:
        cfg = cog.bot.config
        title = ui.truncate(f"Create your {cfg.server.name} account", 45)
        super().__init__(title=title)
        self.cog = cog
        self.generated = cfg.registration.password_mode == "generated"
        self.add_item(discord.ui.InputText(
            label="Account name", custom_id="username",
            placeholder="Letters and numbers, 3–16 characters",
            min_length=accounts.MIN_ACCOUNT_LENGTH, max_length=accounts.MAX_ACCOUNT_LENGTH,
        ))
        if not self.generated:
            min_len = cfg.registration.min_password_length
            self.add_item(discord.ui.InputText(
                label="Password", custom_id="password",
                placeholder=f"{min_len}–16 characters, no spaces",
                min_length=min_len, max_length=accounts.MAX_PASSWORD_LENGTH,
            ))
            self.add_item(discord.ui.InputText(
                label="Confirm password", custom_id="confirm",
                placeholder="Type it again", min_length=min_len, max_length=accounts.MAX_PASSWORD_LENGTH,
            ))

    async def callback(self, interaction: discord.Interaction) -> None:
        values = {child.custom_id: (child.value or "") for child in self.children}
        await self.cog.finish_registration(interaction, values)


class RegistrationCog(discord.Cog, name="Registration"):
    def __init__(self, bot: "ServerBot") -> None:
        self.bot = bot
        self.cfg = bot.config.registration
        self._locks: dict[int, asyncio.Lock] = {}
        self._panel_hash: dict[int, str] = {}
        self._panel_edit_at: dict[int, float] = {}
        self._paused_alerted = 0.0
        self.view = PanelView(self)
        bot.add_view(self.view)

    # -- panel ---------------------------------------------------------------------

    def panel_embed(self) -> discord.Embed:
        cfg = self.bot.config
        e = ui.embed(
            f"Welcome to {cfg.server.name}!",
            "Press **Register** to create your free game account, then **How to connect** to start playing.",
            color=cfg.identity.color,
        )
        statuses = getattr(self.bot, "realm_status", {})
        for realm in cfg.realms:
            if cfg.status.enabled:
                st = statuses.get(realm.id)
                e.add_field(name=f"{status_icon(st)} {realm.name}", value=status_summary(st), inline=False)
            else:
                e.add_field(name=f"🌍 {realm.name}", value="Realm", inline=False)
        if cfg.server.realmlist:
            core = cfg.realms[0].core
            e.add_field(name="Realmlist", value=f"```{ui.realmlist_line(core, cfg.server.realmlist)}```", inline=True)
        if cfg.server.client_version:
            e.add_field(name="Game client", value=ui.safe(cfg.server.client_version), inline=True)
        if cfg.server.banner_url:
            e.set_image(url=cfg.server.banner_url)
        if self.bot.user:
            e.set_author(name=cfg.identity.name, icon_url=self.bot.user.display_avatar.url)
        return ui.footer(e, self.bot, "status refreshes automatically")

    async def post_panel(self, channel: discord.abc.Messageable) -> discord.Message:
        """Post a panel in ``channel`` (or update the one already there)."""
        for channel_id, message_id in await self.bot.storage.panels():
            if channel_id == getattr(channel, "id", None):
                try:
                    msg = await channel.fetch_message(message_id)
                    await msg.edit(embed=self.panel_embed(), view=self.view)
                    return msg
                except discord.NotFound:
                    break
        msg = await channel.send(embed=self.panel_embed(), view=self.view)
        await self.bot.storage.set_panel(msg.channel.id, msg.id)
        return msg

    @discord.Cog.listener()
    async def on_mangos_status(self, statuses: dict) -> None:
        """Keep every panel's status block current (only edits when it changed)."""
        embed = self.panel_embed()
        digest = hashlib.sha1(repr([f.value for f in embed.fields]).encode()).hexdigest()
        for channel_id, message_id in await self.bot.storage.panels():
            if self._panel_hash.get(message_id) == digest and time.monotonic() - self._panel_edit_at.get(message_id, 0) < 3600:
                continue
            channel = self.bot.get_channel(channel_id)
            if channel is None:
                continue
            try:
                await channel.get_partial_message(message_id).edit(embed=embed, view=self.view)
                self._panel_hash[message_id] = digest
                self._panel_edit_at[message_id] = time.monotonic()
            except discord.NotFound:
                await self.bot.storage.remove_panel(channel_id)
            except discord.HTTPException as exc:
                log.warning("Could not refresh panel %s: %s", message_id, exc)

    # -- eligibility ---------------------------------------------------------------

    async def eligibility_problem(self, member: discord.abc.User) -> str | None:
        cfg = self.cfg
        if not cfg.enabled:
            return "Registration is closed at the moment."
        if not isinstance(member, discord.Member):
            return "Please register from inside the Discord server."
        if cfg.required_role and not any(r.id == cfg.required_role for r in member.roles):
            return f"You need the <@&{cfg.required_role}> role before you can register."
        now = discord.utils.utcnow()
        if cfg.min_account_age_days and (now - member.created_at).days < cfg.min_account_age_days:
            return f"Your Discord account must be at least {cfg.min_account_age_days} days old to register."
        if cfg.min_member_age_minutes and member.joined_at:
            waited = (now - member.joined_at).total_seconds() / 60
            if waited < cfg.min_member_age_minutes:
                remaining = int(cfg.min_member_age_minutes - waited) + 1
                return f"Welcome! Please look around for {remaining} more minute(s), then try again."
        links = await self.bot.storage.links_for(member.id)
        if len(links) >= cfg.max_accounts_per_user:
            names = ", ".join(f"**{ui.safe(l.username)}**" for l in links)
            return f"You already have an account ({names}). Use **My account** to manage it."
        # Only attempts that reached the database count here (typos are free), so
        # a player can retry a taken name a few times but can't probe names in bulk.
        if await self.bot.storage.count_attempts("register", 3600, member.id) >= REGISTER_TRIES_PER_HOUR:
            return "Too many registration attempts. Please try again in an hour."
        last_ok = await self.bot.storage.last_attempt("register_ok", member.id)
        if cfg.cooldown_minutes and last_ok and time.time() - last_ok < cfg.cooldown_minutes * 60:
            wait = int((cfg.cooldown_minutes * 60 - (time.time() - last_ok)) // 60) + 1
            return f"You just created an account. Please wait {wait} minute(s) before creating another."
        recent = await self.bot.storage.count_attempts("register_ok", 3600)
        if recent >= cfg.global_limit_per_hour:
            if time.monotonic() - self._paused_alerted > 3600:
                self._paused_alerted = time.monotonic()
                await self.bot.audit("Registrations paused",
                                     f"{recent} accounts were created in the last hour (limit {cfg.global_limit_per_hour}).",
                                     color=ui.WARNING)
            return "Lots of people are signing up right now. Please try again in a little while."
        return None

    # -- flows -------------------------------------------------------------------------

    async def start_registration(self, interaction: discord.Interaction) -> None:
        problem = await self.eligibility_problem(interaction.user)
        if problem:
            await ui.reply(interaction, embed=ui.warning("Can't register yet", problem))
            return
        await interaction.response.send_modal(RegisterModal(self))

    async def finish_registration(self, interaction: discord.Interaction, values: dict[str, str]) -> None:
        await interaction.response.defer(ephemeral=True)
        member = interaction.user
        lock = self._locks.setdefault(member.id, asyncio.Lock())
        async with lock:  # a double-submitted modal can't create two accounts
            problem = await self.eligibility_problem(member)
            if problem:
                await ui.reply(interaction, embed=ui.warning("Can't register yet", problem))
                return

            username = accounts.normalize_username(values.get("username", ""))
            err = accounts.validate_username(username, self.cfg.reserved_names)
            generated = self.cfg.password_mode == "generated"
            if generated:
                password = accounts.generate_password()
            else:
                password = values.get("password", "")
                err = err or accounts.validate_password(
                    password, min_length=self.cfg.min_password_length, username=username,
                    for_soap=self.cfg.method == "soap",
                )
                if not err and password != values.get("confirm", ""):
                    err = "The two passwords don't match."
            if err:
                await ui.reply(interaction, embed=ui.error("Let's fix that", err))
                return  # input mistakes don't count against the player

            await self.bot.storage.record_attempt("register", member.id)
            try:
                account_id = await self.bot.accounts.create(username, password)
            except AccountExistsError:
                await ui.reply(interaction, embed=ui.error("Name taken", f"**{username}** is already taken. Try another name."))
                return
            except (AccountError, SoapError, DatabaseError) as exc:
                log.error("Registration of %s failed: %s", username, exc)
                await ui.reply(interaction, embed=ui.error(
                    "Registration failed", "The game server couldn't create the account right now. Please try again later."))
                return

            await self.bot.storage.add_link(member.id, account_id, username, "registered")
            await self.bot.storage.record_attempt("register_ok", member.id)
            await self._grant_player_role(member)
            await self.bot.audit("Account registered", f"{member.mention} created **{username}** (id {account_id}).", actor=member, color=ui.SUCCESS)

        e = ui.success("Your account is ready!", f"Account name: **{username}**")
        if generated:
            e.add_field(
                name="Your password",
                value=f"||`{password}`||\nCopy it now; it won't be shown again. Change it in game with "
                      f"`.account password {password} <new> <new>`.",
                inline=False,
            )
        e.add_field(name="Next step", value="Press **How to connect** on the panel, or use `/connect`.", inline=False)
        e.add_field(name="Good to know", value="Passwords aren't case-sensitive in WoW. Log in with your *account name*, not your Discord name.", inline=False)
        await ui.reply(interaction, embed=ui.footer(e, self.bot))

    async def _grant_player_role(self, member: discord.abc.User) -> None:
        role_id = self.bot.config.roles.player
        if not role_id or not isinstance(member, discord.Member):
            return
        role = member.guild.get_role(role_id)
        if role is None or role in member.roles:
            return
        try:
            await member.add_roles(role, reason="Registered a game account")
        except discord.HTTPException as exc:
            log.warning("Could not give %s the player role: %s", member.id, exc)

    def connect_embed(self) -> discord.Embed:
        cfg = self.bot.config
        core = cfg.realms[0].core
        e = ui.embed(f"🔌 How to play on {cfg.server.name}", color=cfg.identity.color)
        client = ui.safe(cfg.server.client_version) or "the right game client"
        step1 = f"Install **{client}**."
        if cfg.server.client_download_url:
            step1 += f" [Download it here]({cfg.server.client_download_url})."
        steps = [step1]
        if cfg.server.realmlist:
            steps.append(f"Open `{ui.realmlist_file(core)}` and replace its contents with:\n"
                         f"```{ui.realmlist_line(core, cfg.server.realmlist)}```")
        steps.append("Start the game (run `WoW.exe` directly, not a launcher) and log in with your **account name** and password.")
        steps.append("Need help in game? Open a GM ticket from the Help menu; our staff see it here on Discord.")
        e.description = "\n\n".join(f"**{i}.** {s}" for i, s in enumerate(steps, 1))
        return ui.footer(e, self.bot)

    async def send_connect_guide(self, interaction: discord.Interaction) -> None:
        await ui.reply(interaction, embed=self.connect_embed())

    async def send_my_account(self, interaction: discord.Interaction) -> None:
        cog = self.bot.get_cog("Account")
        if cog is None:
            await ui.reply(interaction, embed=ui.error("Unavailable", "Account management is turned off."))
            return
        await interaction.response.defer(ephemeral=True)
        await ui.reply(interaction, embed=await cog.account_embed(interaction.user))

    # -- commands ------------------------------------------------------------------

    @discord.slash_command(name="register", description="Create your game account")
    async def register_command(self, ctx: discord.ApplicationContext) -> None:
        await self.start_registration(ctx.interaction)

    @discord.slash_command(name="connect", description="How to set up the game and connect to the server")
    async def connect_command(self, ctx: discord.ApplicationContext) -> None:
        await ctx.respond(embed=self.connect_embed(), ephemeral=True)
