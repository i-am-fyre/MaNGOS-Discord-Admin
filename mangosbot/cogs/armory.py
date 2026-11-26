"""/armory character cards, item tooltips and /leaderboard."""

from __future__ import annotations

import asyncio
import io
import logging
from typing import TYPE_CHECKING, Any

import discord

from .. import items, ui, wow
from ..config import RealmConfig
from ..database import DatabaseError
from ..render import CardItem, CharacterCard, render_character_card

if TYPE_CHECKING:
    from ..bot import ServerBot

log = logging.getLogger(__name__)

ITEM_PREFIX = "mb:arm:item:"
GEAR_GROUPS = (
    ("Armor", wow.PAPERDOLL_LEFT),
    ("Accessories", wow.PAPERDOLL_RIGHT),
    ("Weapons", wow.PAPERDOLL_BOTTOM),
)
LEADERBOARDS = {
    "level": "Highest level",
    "playtime": "Most time played",
    "gold": "Wealthiest",
    "kills": "Most honorable kills",
}
MEDALS = ("🥇", "🥈", "🥉")


def _realm_option():
    return discord.option(
        "realm", str, description="Realm (defaults to the main realm)", required=False,
        autocomplete=discord.utils.basic_autocomplete(lambda ctx: ctx.bot.realm_names()),
    )


async def _visible_names(ctx: discord.AutocompleteContext) -> list[str]:
    typed = (ctx.value or "").strip()
    if len(typed) < 2:
        return []
    try:
        realm = ctx.bot.config.realm_by_name(ctx.options.get("realm"))
        excluded, only = await ctx.bot.visibility.filters()
        return await ctx.bot.mangos.search_character_names(realm, typed, 25, excluded, only)
    except (KeyError, DatabaseError):
        return []


class ArmoryCog(discord.Cog, name="Armory"):
    def __init__(self, bot: "ServerBot") -> None:
        self.bot = bot
        self.cfg = bot.config.armory

    # -- building blocks -------------------------------------------------------------

    def _gear_field(self, realm: RealmConfig, gear: dict[int, dict[str, Any]], slots: tuple[int, ...]) -> str:
        lines: list[str] = []
        for slot in slots:
            row = gear.get(slot)
            if row is None:
                continue
            _, _, emoji = wow.quality(items.item_quality(row))
            name = ui.safe(items.item_name(row), 60)
            url = items.item_url(realm, int(row["entry"]))
            ilvl = items.item_level(row)
            label = f"`{wow.EQUIPMENT_SLOTS[slot]}`"
            linked = f"[{name}]({url})" if url else name
            lines.append(f"{label} {emoji} {linked}" + (f" · {ilvl}" if ilvl else ""))
        text = "\n".join(lines)
        if len(text) > 1024:  # fall back to plain names if links make it too long
            text = "\n".join(f"`{wow.EQUIPMENT_SLOTS[s]}` {wow.quality(items.item_quality(gear[s]))[2]} "
                             f"{ui.safe(items.item_name(gear[s]), 40)}" for s in slots if s in gear)
        return ui.truncate(text, 1024) or "—"

    def character_embed(self, realm: RealmConfig, char: dict[str, Any], gear: dict[int, dict[str, Any]]) -> discord.Embed:
        race, cls = int(char["race"]), int(char["class"])
        fac = wow.faction(race)
        e = discord.Embed(title=ui.safe(char["name"]), color=wow.CLASS_COLORS.get(cls, ui.INFO))
        lines = [f"Level {char['level']} {wow.race_name(race)} {wow.class_name(cls)}"]
        if char.get("guild_name"):
            lines.append(f"<{ui.safe(char['guild_name'])}>")
        stats = [f"{wow.FACTION_EMOJI[fac]} {fac}"]
        avg = items.average_item_level(gear)
        if avg:
            stats.append(f"Item level **{avg:g}**")
        if char.get("kills"):
            stats.append(f"**{int(char['kills']):,}** honorable kills")
        lines.append(" · ".join(stats))
        e.description = "\n".join(lines)
        if gear:
            for title, slots in GEAR_GROUPS:
                if any(s in gear for s in slots):
                    e.add_field(name=title, value=self._gear_field(realm, gear, slots), inline=False)
        else:
            e.add_field(name="Gear", value="Nothing equipped. Brave!", inline=False)
        e.set_author(name=f"{self.bot.config.identity.name} · Armory · {realm.name}")
        return ui.footer(e, self.bot)

    def item_select(self, realm: RealmConfig, gear: dict[int, dict[str, Any]]) -> discord.ui.View | None:
        options = []
        seen: set[int] = set()
        for slot in (*wow.PAPERDOLL_LEFT, *wow.PAPERDOLL_RIGHT, *wow.PAPERDOLL_BOTTOM):
            row = gear.get(slot)
            if row is None or int(row["entry"]) in seen:
                continue
            seen.add(int(row["entry"]))
            options.append(discord.SelectOption(
                label=ui.truncate(items.item_name(row), 100), value=str(row["entry"]),
                description=wow.EQUIPMENT_SLOTS[slot], emoji=wow.quality(items.item_quality(row))[2],
            ))
        if not options:
            return None
        view = discord.ui.View(timeout=None, store=False)  # handled by on_interaction, survives restarts
        view.add_item(discord.ui.Select(custom_id=f"{ITEM_PREFIX}{realm.id}", placeholder="🔍 Inspect an item…",
                                        options=options[:25]))
        return view

    async def render_card(self, realm: RealmConfig, char: dict[str, Any], gear: dict[int, dict[str, Any]]) -> bytes:
        icon_bytes = await asyncio.gather(*(self.bot.icons.image_for(row) for row in gear.values()))
        card_items = {
            slot: CardItem(slot, items.item_name(row), items.item_quality(row), items.item_level(row), icon)
            for (slot, row), icon in zip(gear.items(), icon_bytes)
        }
        card = CharacterCard(
            name=str(char["name"]), level=int(char["level"]), race=int(char["race"]), cls=int(char["class"]),
            guild=char.get("guild_name"), kills=int(char["kills"]) if char.get("kills") is not None else None,
            average_item_level=items.average_item_level(gear), realm=realm.name,
            footer=f"{self.bot.config.identity.name} · {self.bot.config.server.name}", items=card_items,
        )
        return await asyncio.to_thread(render_character_card, card, self.cfg.font_path)

    # -- commands ----------------------------------------------------------------------

    @discord.slash_command(name="armory", description="Look up a character and their gear")
    @discord.option("character", str, description="Character name", autocomplete=_visible_names)
    @_realm_option()
    async def armory(self, ctx: discord.ApplicationContext, character: str, realm: str | None = None) -> None:
        if not self.cfg.enabled:
            await ctx.respond(embed=ui.error("The armory is turned off"), ephemeral=True)
            return
        try:
            realm_cfg = self.bot.config.realm_by_name(realm)
        except KeyError:
            await ctx.respond(embed=ui.error("Unknown realm", ui.safe(realm)), ephemeral=True)
            return
        await ctx.defer()
        char = await self.bot.mangos.character_by_name(realm_cfg, character.strip())
        # Same answer for hidden and missing characters, so hiding really hides.
        if char is None or not await self.bot.visibility.can_show(int(char["account"])):
            await ctx.respond(embed=ui.error("Not found", f"{self.bot.config.identity.name} has no record of "
                                                          f"**{ui.safe(character, 40)}** on {realm_cfg.name}."))
            return
        equipped = await self.bot.mangos.equipped_items(realm_cfg, int(char["guid"]))
        templates = await self.bot.mangos.item_templates(realm_cfg, equipped.values())
        gear = {slot: templates[entry] for slot, entry in equipped.items() if entry in templates}

        e = self.character_embed(realm_cfg, char, gear)
        kwargs: dict[str, Any] = {"embed": e}
        view = self.item_select(realm_cfg, gear)
        if view is not None:
            kwargs["view"] = view
        if self.cfg.render_image:
            try:
                png = await self.render_card(realm_cfg, char, gear)
                kwargs["file"] = discord.File(io.BytesIO(png), filename="armory.png")
                e.set_image(url="attachment://armory.png")
            except Exception:
                log.exception("Could not render the armory card for %s", char["name"])
        await ctx.respond(**kwargs)

    @discord.Cog.listener()
    async def on_interaction(self, interaction: discord.Interaction) -> None:
        if interaction.type is not discord.InteractionType.component:
            return
        data = interaction.data or {}
        custom_id = data.get("custom_id", "")
        if not custom_id.startswith(ITEM_PREFIX):
            return
        try:
            realm = self.bot.config.realm(int(custom_id[len(ITEM_PREFIX):]))
            entry = int((data.get("values") or ["0"])[0])
        except (ValueError, KeyError):
            return
        try:
            rows = await self.bot.mangos.item_templates(realm, [entry])
            row = rows.get(entry)
            if row is None:
                await ui.reply(interaction, embed=ui.error("Unknown item", f"Item #{entry} isn't in the world database."))
                return
            await ui.reply(interaction, embed=items.tooltip_embed(row, realm, self.bot.icons.url_for(row),
                                                                  self.bot.spells))
        except Exception as exc:
            await ui.report_failure(interaction, exc, "item tooltip")

    @discord.slash_command(name="leaderboard", description="The Hall of Legends: top characters on the realm")
    @discord.option("category", str, description="What to rank by", choices=[
        discord.OptionChoice(label, key) for key, label in LEADERBOARDS.items()])
    @_realm_option()
    async def leaderboard(self, ctx: discord.ApplicationContext, category: str, realm: str | None = None) -> None:
        cfg = self.bot.config.leaderboard
        if not cfg.enabled:
            await ctx.respond(embed=ui.error("Leaderboards are turned off"), ephemeral=True)
            return
        if category == "gold" and not cfg.show_gold:
            await ctx.respond(embed=ui.error("Not available", "The gold leaderboard is turned off on this server."), ephemeral=True)
            return
        try:
            realm_cfg = self.bot.config.realm_by_name(realm)
        except KeyError:
            await ctx.respond(embed=ui.error("Unknown realm", ui.safe(realm)), ephemeral=True)
            return
        await ctx.defer()
        excluded, only = await self.bot.visibility.filters()
        rows = await self.bot.mangos.leaderboard(realm_cfg, category, cfg.size, excluded, only)
        lines = []
        for i, row in enumerate(rows):
            rank = MEDALS[i] if i < len(MEDALS) else f"`#{i + 1}`"
            value = {
                "level": f"level {row['level']}",
                "playtime": wow.format_duration(int(row["totaltime"] or 0), max_units=2),
                "gold": wow.format_money(int(row["money"] or 0)),
                "kills": f"{int(row['kills'] or 0):,} kills",
            }[category]
            who = f"{wow.race_name(int(row['race']))} {wow.class_name(int(row['class']))}"
            lines.append(f"{rank} **{ui.safe(row['name'])}** · {value} · {who}")
        e = ui.embed(f"🏆 Hall of Legends: {LEADERBOARDS[category]}", "\n".join(lines) or "No one yet. Go make history!",
                     color=0xF1C40F)
        e.set_author(name=realm_cfg.name)
        await ctx.respond(embed=ui.footer(e, self.bot))
