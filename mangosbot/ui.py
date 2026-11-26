"""Shared embed styling, text helpers, permission checks and base views."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import TYPE_CHECKING, Any

import discord
from discord.ext import commands

if TYPE_CHECKING:
    from .bot import ServerBot
    from .config import Config

log = logging.getLogger(__name__)

SUCCESS = 0x2ECC71
ERROR = 0xE74C3C
WARNING = 0xF1C40F
INFO = 0x5865F2
MUTED = 0x95A5A6


def embed(title: str | None = None, description: str | None = None, *, color: int = INFO, **kwargs: Any) -> discord.Embed:
    return discord.Embed(title=title, description=description, color=color, **kwargs)


def success(title: str, description: str | None = None) -> discord.Embed:
    return embed(f"✅ {title}", description, color=SUCCESS)


def error(title: str, description: str | None = None) -> discord.Embed:
    return embed(f"❌ {title}", description, color=ERROR)


def warning(title: str, description: str | None = None) -> discord.Embed:
    return embed(f"⚠️ {title}", description, color=WARNING)


def footer(e: discord.Embed, bot: "ServerBot", extra: str | None = None) -> discord.Embed:
    text = f"{bot.config.identity.name} · {bot.config.server.name}"
    if extra:
        text = f"{text} · {extra}"
    e.set_footer(text=text)
    return e


def truncate(text: str, limit: int) -> str:
    text = text or ""
    return text if len(text) <= limit else text[: max(0, limit - 1)].rstrip() + "…"


def safe(text: str | None, limit: int | None = None) -> str:
    """Escape Discord markdown/mentions in text that came from players or the DB."""
    cleaned = discord.utils.escape_mentions(discord.utils.escape_markdown(text or ""))
    return truncate(cleaned, limit) if limit else cleaned


def ts(dt: datetime | int | float | None, style: str = "R") -> str:
    """Discord timestamp markup, rendered in each viewer's own time zone."""
    if dt is None:
        return "—"
    if isinstance(dt, (int, float)):
        if dt <= 0:
            return "—"
        return f"<t:{int(dt)}:{style}>"
    return discord.utils.format_dt(dt, style=style)  # type: ignore[arg-type]


def realmlist_line(core: str, host: str) -> str:
    if core in ("three", "four"):
        return f'SET realmlist "{host}"'
    return f"set realmlist {host}"


def realmlist_file(core: str) -> str:
    return {
        "zero": "realmlist.wtf in your WoW folder",
        "one": "realmlist.wtf in your WoW folder",
        "two": "Data/<your language, e.g. enUS>/realmlist.wtf",
        "three": "WTF/Config.wtf",
        "four": "WTF/Config.wtf",
    }.get(core, "realmlist.wtf")


# -- permissions -----------------------------------------------------------------


def is_admin(member: discord.abc.User, config: "Config") -> bool:
    if not isinstance(member, discord.Member):
        return False
    if member.guild_permissions.administrator:
        return True
    return any(role.id in config.roles.admin for role in member.roles)


def is_staff(member: discord.abc.User, config: "Config") -> bool:
    if is_admin(member, config):
        return True
    if not isinstance(member, discord.Member):
        return False
    return any(role.id in config.roles.staff for role in member.roles)


class NotAllowed(commands.CheckFailure):
    pass


def admin_only():
    async def predicate(ctx: discord.ApplicationContext) -> bool:
        if not is_admin(ctx.author, ctx.bot.config):
            raise NotAllowed("This command is for server admins.")
        return True

    return commands.check(predicate)


def staff_only():
    async def predicate(ctx: discord.ApplicationContext) -> bool:
        if not is_staff(ctx.author, ctx.bot.config):
            raise NotAllowed("This command is for staff.")
        return True

    return commands.check(predicate)


async def reply(interaction: discord.Interaction, *, embed: discord.Embed, ephemeral: bool = True, **kwargs: Any) -> None:
    """Respond or follow up, whichever is valid for this interaction."""
    if interaction.response.is_done():
        await interaction.followup.send(embed=embed, ephemeral=ephemeral, **kwargs)
    else:
        await interaction.response.send_message(embed=embed, ephemeral=ephemeral, **kwargs)


async def report_failure(interaction: discord.Interaction, exc: BaseException, where: str) -> None:
    log.exception("Unhandled error in %s", where, exc_info=exc)
    try:
        await reply(interaction, embed=error("Something went wrong", "The error was logged for the admins. Please try again later."))
    except discord.HTTPException:
        pass


class SafeView(discord.ui.View):
    """A view that logs errors and tells the user instead of failing silently."""

    async def on_error(self, error: Exception, item: Any, interaction: discord.Interaction) -> None:
        await report_failure(interaction, error, f"{type(self).__name__}")


class SafeModal(discord.ui.Modal):
    async def on_error(self, error: Exception, interaction: discord.Interaction) -> None:
        await report_failure(interaction, error, f"{type(self).__name__}")


# Default member permissions: Discord hides the commands from everyone else.
# These are only defaults; server owners can override them, which is why the
# role checks above always run as well.
ADMIN_PERMISSIONS = discord.Permissions(manage_guild=True)
STAFF_PERMISSIONS = discord.Permissions(manage_messages=True)
