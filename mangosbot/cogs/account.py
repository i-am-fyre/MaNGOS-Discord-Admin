"""/account: view, link, change password, privacy, unlink."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import TYPE_CHECKING, Any

import discord

from .. import accounts, ui, wow
from ..database import DatabaseError
from ..soap import SoapError
from ..storage import AccountLink

if TYPE_CHECKING:
    from ..bot import ServerBot

log = logging.getLogger(__name__)

LINK_ATTEMPTS_PER_HOUR = 5
PASSWORD_CHANGES_PER_HOUR = 5


async def _own_accounts(ctx: discord.AutocompleteContext) -> list[str]:
    links = await ctx.bot.storage.links_for(ctx.interaction.user.id)
    typed = (ctx.value or "").upper()
    return [l.username for l in links if l.username.startswith(typed)][:25]


class PasswordModal(ui.SafeModal):
    def __init__(self, cog: "AccountCog", link: AccountLink) -> None:
        super().__init__(title=ui.truncate(f"New password for {link.username}", 45))
        self.cog, self.link = cog, link
        min_len = cog.bot.config.registration.min_password_length
        self.add_item(discord.ui.InputText(label="New password", custom_id="password", min_length=min_len,
                                           max_length=accounts.MAX_PASSWORD_LENGTH, placeholder="No spaces"))
        self.add_item(discord.ui.InputText(label="Confirm new password", custom_id="confirm", min_length=min_len,
                                           max_length=accounts.MAX_PASSWORD_LENGTH))

    async def callback(self, interaction: discord.Interaction) -> None:
        values = {c.custom_id: c.value or "" for c in self.children}
        await self.cog.finish_password(interaction, self.link, values)


class LinkModal(ui.SafeModal):
    def __init__(self, cog: "AccountCog") -> None:
        super().__init__(title="Link an existing game account")
        self.cog = cog
        self.add_item(discord.ui.InputText(label="Account name", custom_id="username",
                                           max_length=accounts.MAX_ACCOUNT_LENGTH))
        self.add_item(discord.ui.InputText(label="Password", custom_id="password",
                                           max_length=accounts.MAX_PASSWORD_LENGTH))

    async def callback(self, interaction: discord.Interaction) -> None:
        values = {c.custom_id: c.value or "" for c in self.children}
        await self.cog.finish_link(interaction, values)


class AccountCog(discord.Cog, name="Account"):
    account = discord.SlashCommandGroup("account", "Your game account")

    def __init__(self, bot: "ServerBot") -> None:
        self.bot = bot

    def _when(self, value: Any) -> str:
        if not isinstance(value, datetime) or value.year < 1971:
            return "—"
        tz = self.bot.config.events.timezone
        if value.tzinfo is None and tz is not None:
            value = value.replace(tzinfo=tz)
        return ui.ts(value, "R")

    async def _is_protected(self, account_id: int) -> bool:
        if self.bot.config.registration.allow_gm_accounts:
            return False
        row = await self.bot.mangos.account_by_id(account_id)
        return bool(row and int(row["gmlevel"] or 0) > 0)

    async def account_embed(self, user: discord.abc.User) -> discord.Embed:
        links = await self.bot.storage.links_for(user.id)
        if not links:
            e = ui.embed("👤 No game account linked yet",
                         "Use **/register** to create one, or **/account link** if you already have one.")
            return ui.footer(e, self.bot)
        e = ui.embed(f"👤 {user.display_name}'s game account{'s' if len(links) > 1 else ''}",
                     color=self.bot.config.identity.color)
        for link in links[:5]:
            try:
                row = await self.bot.mangos.account_by_id(link.account_id)
                ban = await self.bot.mangos.active_ban(link.account_id) if row else None
            except DatabaseError:
                e.add_field(name=link.username, value="Game database unavailable right now.", inline=False)
                continue
            if row is None:
                e.add_field(name=link.username, value="This account no longer exists on the server.", inline=False)
                continue
            lines = [f"Created {self._when(row.get('joindate'))} · last login {self._when(row.get('last_login'))}"]
            if ban:
                if int(ban["unbandate"]) == int(ban["bandate"]):
                    lines.append(f"⛔ **Banned permanently**: {ui.safe(ban['banreason'], 200)}")
                else:
                    lines.append(f"⛔ **Banned** until {ui.ts(int(ban['unbandate']), 'F')}: {ui.safe(ban['banreason'], 200)}")
            for realm in self.bot.config.realms:
                try:
                    chars = await self.bot.mangos.characters_for_account(realm, link.account_id)
                except DatabaseError:
                    continue
                if chars:
                    shown = [f"{ui.safe(c['name'])} · {c['level']} {wow.race_name(int(c['race']))} "
                             f"{wow.class_name(int(c['class']))}" for c in chars[:8]]
                    more = f" (+{len(chars) - 8} more)" if len(chars) > 8 else ""
                    prefix = f"**{realm.name}:** " if len(self.bot.config.realms) > 1 else ""
                    lines.append(prefix + ", ".join(shown) + more)
            e.add_field(name=f"🎮 {link.username}", value=ui.truncate("\n".join(lines), 1024), inline=False)
        visible = await self.bot.storage.visibility(user.id)
        mode = self.bot.config.armory.privacy
        shown = visible if visible is not None else mode == "public"
        e.add_field(name="Privacy",
                    value=("Your characters **appear** in the armory and leaderboards." if shown
                           else "Your characters are **hidden** from the armory and leaderboards.")
                    + " Change with `/account privacy`.", inline=False)
        return ui.footer(e, self.bot)

    async def _pick_link(self, user: discord.abc.User, name: str | None) -> AccountLink | None:
        links = await self.bot.storage.links_for(user.id)
        if name:
            name = name.strip().upper()
            return next((l for l in links if l.username == name), None)
        return links[0] if len(links) == 1 else None

    # -- commands --------------------------------------------------------------------

    @account.command(name="info", description="Show your game account, characters and ban status")
    async def info(self, ctx: discord.ApplicationContext) -> None:
        await ctx.defer(ephemeral=True)
        await ctx.respond(embed=await self.account_embed(ctx.author), ephemeral=True)

    @account.command(name="password", description="Change your game account password")
    @discord.option("account", str, description="Which account (if you have several)", required=False,
                    autocomplete=discord.utils.basic_autocomplete(_own_accounts))
    async def password(self, ctx: discord.ApplicationContext, account: str | None = None) -> None:
        link = await self._pick_link(ctx.author, account)
        if link is None:
            await ctx.respond(embed=ui.error("Which account?", "Link or register an account first, or pick one from the list."), ephemeral=True)
            return
        if await self.bot.storage.count_attempts("password", 3600, ctx.author.id) >= PASSWORD_CHANGES_PER_HOUR:
            await ctx.respond(embed=ui.warning("Slow down", "Too many password changes. Try again in an hour."), ephemeral=True)
            return
        await ctx.send_modal(PasswordModal(self, link))

    async def finish_password(self, interaction: discord.Interaction, link: AccountLink, values: dict[str, str]) -> None:
        await interaction.response.defer(ephemeral=True)
        await self.bot.storage.record_attempt("password", interaction.user.id)
        password = values.get("password", "")
        err = accounts.validate_password(password, min_length=self.bot.config.registration.min_password_length,
                                         username=link.username, for_soap=self.bot.accounts.method == "soap")
        if not err and password != values.get("confirm"):
            err = "The two passwords don't match."
        if err:
            await ui.reply(interaction, embed=ui.error("Let's fix that", err))
            return
        # Re-read the link: it may have been removed since the modal opened.
        current = await self.bot.storage.link_for_account(link.account_id)
        if current is None or current.discord_id != interaction.user.id:
            await ui.reply(interaction, embed=ui.error("Not linked", "That account isn't linked to you any more."))
            return
        try:
            if await self._is_protected(link.account_id):
                await ui.reply(interaction, embed=ui.error("Not from Discord", "Staff accounts can't be managed from Discord."))
                return
            await self.bot.accounts.change_password(link.account_id, link.username, password)
        except (DatabaseError, SoapError) as exc:
            log.error("Password change for %s failed: %s", link.username, exc)
            await ui.reply(interaction, embed=ui.error("Couldn't change it", "The game server didn't respond. Try again later."))
            return
        await self.bot.audit("Password changed", f"{interaction.user.mention} changed the password of **{link.username}**.",
                             actor=interaction.user)
        await ui.reply(interaction, embed=ui.success("Password changed", "Use the new password the next time you log in."))

    @account.command(name="link", description="Link a game account you already have")
    async def link(self, ctx: discord.ApplicationContext) -> None:
        reg = self.bot.config.registration
        if not reg.allow_linking:
            await ctx.respond(embed=ui.error("Not available", "Linking existing accounts is turned off."), ephemeral=True)
            return
        if len(await self.bot.storage.links_for(ctx.author.id)) >= reg.max_accounts_per_user:
            await ctx.respond(embed=ui.error("Limit reached", f"You can link up to {reg.max_accounts_per_user} account(s)."), ephemeral=True)
            return
        if await self.bot.storage.count_attempts("link", 3600, ctx.author.id) >= LINK_ATTEMPTS_PER_HOUR:
            await ctx.respond(embed=ui.warning("Too many attempts", "Please try again in an hour."), ephemeral=True)
            return
        await ctx.send_modal(LinkModal(self))

    async def finish_link(self, interaction: discord.Interaction, values: dict[str, str]) -> None:
        await interaction.response.defer(ephemeral=True)
        user = interaction.user
        await self.bot.storage.record_attempt("link", user.id)
        username = accounts.normalize_username(values.get("username", ""))
        try:
            row = await self.bot.mangos.account_by_name(username) if username else None
        except DatabaseError:
            await ui.reply(interaction, embed=ui.error("Database unavailable", "Try again in a moment."))
            return
        if row is None or not accounts.verify_password(username, values.get("password", ""), row["sha_pass_hash"]):
            await ui.reply(interaction, embed=ui.error("No match", "Wrong account name or password."))
            return
        account_id = int(row["id"])
        if int(row["gmlevel"] or 0) > 0 and not self.bot.config.registration.allow_gm_accounts:
            await ui.reply(interaction, embed=ui.error("Not from Discord", "Staff accounts can't be linked."))
            return
        existing = await self.bot.storage.link_for_account(account_id)
        if existing is not None:
            if existing.discord_id == user.id:
                await ui.reply(interaction, embed=ui.success("Already linked", f"**{username}** is already yours."))
            else:
                await ui.reply(interaction, embed=ui.error("Linked elsewhere", "That account is linked to another Discord user. Please contact staff."))
                await self.bot.audit("Link refused", f"{user.mention} proved the password for **{username}**, which is linked to <@{existing.discord_id}>.",
                                     actor=user, color=ui.WARNING)
            return
        await self.bot.storage.add_link(user.id, account_id, str(row["username"]), "linked")
        reg_cog = self.bot.get_cog("Registration")
        if reg_cog is not None:
            await reg_cog._grant_player_role(user)
        await self.bot.audit("Account linked", f"{user.mention} linked **{row['username']}** (id {account_id}).", actor=user)
        await ui.reply(interaction, embed=ui.success("Linked!", f"**{row['username']}** is now linked to your Discord account."))

    @account.command(name="privacy", description="Choose whether your characters appear in the armory and leaderboards")
    @discord.option("visible", bool, description="True to show your characters, False to hide them")
    async def privacy(self, ctx: discord.ApplicationContext, visible: bool) -> None:
        await self.bot.storage.set_visible(ctx.author.id, visible)
        self.bot.visibility.invalidate()
        msg = ("Your characters will appear in the armory, leaderboards and online list." if visible
               else "Your characters are now hidden from the armory, leaderboards and online list.")
        await ctx.respond(embed=ui.success("Privacy updated", msg), ephemeral=True)

    @account.command(name="unlink", description="Remove a game account from your Discord account")
    @discord.option("account", str, description="Account to unlink",
                    autocomplete=discord.utils.basic_autocomplete(_own_accounts))
    async def unlink(self, ctx: discord.ApplicationContext, account: str) -> None:
        link = await self._pick_link(ctx.author, account)
        if link is None or not await self.bot.storage.remove_link(ctx.author.id, link.account_id):
            await ctx.respond(embed=ui.error("Not found", "That account isn't linked to you."), ephemeral=True)
            return
        self.bot.visibility.invalidate()
        await self.bot.audit("Account unlinked", f"{ctx.author.mention} unlinked **{link.username}**.", actor=ctx.author)
        await ctx.respond(embed=ui.success("Unlinked", f"**{link.username}** is no longer linked. The game account itself is unchanged."), ephemeral=True)
