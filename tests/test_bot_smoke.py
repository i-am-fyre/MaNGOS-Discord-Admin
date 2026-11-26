"""Build the whole bot (no network) and check every command registers."""

import discord

from mangosbot.bot import ServerBot


def _walk(commands):
    for cmd in commands:
        yield cmd
        if isinstance(cmd, discord.SlashCommandGroup):
            yield from _walk(cmd.subcommands)


async def test_bot_builds_with_all_cogs(example_config):
    bot = ServerBot(example_config)
    try:
        assert set(bot.cogs) == {"Registration", "Account", "Staff", "Status", "Tickets", "Events", "Armory"}
        names = {c.qualified_name for c in _walk(bot.pending_application_commands)}
        expected = {
            "register", "connect", "status", "online", "events", "armory", "leaderboard",
            "account info", "account password", "account link", "account privacy", "account unlink",
            "mod tickets", "mod whois", "mod kick", "mod serverinfo",
            "admin panel", "admin announce", "admin restart", "admin shutdown", "admin cancel", "admin ban",
            "admin unban", "admin unlink", "admin event-announce", "admin sync", "admin check",
        }
        assert expected <= names, expected - names

        groups = {c.name: c for c in bot.pending_application_commands if isinstance(c, discord.SlashCommandGroup)}
        assert groups["admin"].default_member_permissions.manage_guild
        assert groups["mod"].default_member_permissions.manage_messages
        # Every staff/admin subcommand also carries a runtime role check.
        for group in ("admin", "mod"):
            for sub in groups[group].subcommands:
                assert sub.checks, f"/{group} {sub.name} has no permission check"

        # The registration panel is persistent (works after restarts).
        reg = bot.get_cog("Registration")
        assert reg.view.is_persistent()
        assert {i.custom_id for i in reg.view.children if getattr(i, "custom_id", None)} >= {
            "mb:reg:register", "mb:reg:connect", "mb:reg:account"}
    finally:
        await bot.close()
