"""Item tooltips and icons for the armory."""

from __future__ import annotations

import asyncio
import io
import logging
import re
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

import aiohttp
import discord

from . import wow
from .config import RealmConfig
from .dbc import DbcError, load_item_icons
from .spells import SpellBook

if TYPE_CHECKING:
    from .bot import ServerBot

log = logging.getLogger(__name__)

_ICON_NAME_RE = re.compile(r"^[a-z0-9_\-]{1,96}$")
_MAX_ICON_BYTES = 512 * 1024
_MISSING_TTL = 6 * 3600


def item_url(realm: RealmConfig, entry: int) -> str:
    return realm.item_link.format(id=entry) if realm.item_link else ""


def _int(row: dict[str, Any], key: str) -> int:
    try:
        return int(row.get(key) or 0)
    except (TypeError, ValueError):
        return 0


def _float(row: dict[str, Any], key: str) -> float:
    try:
        return float(row.get(key) or 0)
    except (TypeError, ValueError):
        return 0.0


def item_name(row: dict[str, Any]) -> str:
    return str(row.get("name") or f"Item {row.get('entry')}")


def item_quality(row: dict[str, Any]) -> int:
    return _int(row, "Quality")


def item_level(row: dict[str, Any]) -> int:
    return _int(row, "ItemLevel")


def average_item_level(items: dict[int, dict[str, Any]]) -> float | None:
    levels = [item_level(row) for slot, row in items.items() if slot not in wow.ITEM_LEVEL_EXCLUDED_SLOTS]
    levels = [lvl for lvl in levels if lvl > 0]
    return round(sum(levels) / len(levels), 1) if levels else None


def _slot_and_type(row: dict[str, Any]) -> str:
    inv = wow.INVENTORY_TYPES.get(_int(row, "InventoryType"), "")
    item_class, subclass = _int(row, "class"), _int(row, "subclass")
    kind = ""
    if item_class == wow.ITEM_CLASS_WEAPON:
        kind = wow.WEAPON_SUBCLASSES.get(subclass, "")
    elif item_class == wow.ITEM_CLASS_ARMOR and subclass:
        kind = wow.ARMOR_SUBCLASSES.get(subclass, "")
    return " · ".join(part for part in (inv, kind) if part)


def tooltip_lines(row: dict[str, Any], realm: RealmConfig, spells: SpellBook | None = None) -> list[str]:
    """Approximate the in-game tooltip using whichever columns this core has.

    With ``spells`` (Spell.dbc for this realm's core), Equip/Use/Chance on hit
    lines show the real effect text instead of a spell number.
    """
    if spells is not None and spells.core != realm.core:
        spells = None
    lines: list[str] = []
    bonding = wow.BONDING.get(_int(row, "bonding"))
    if bonding:
        lines.append(bonding)
    if _int(row, "maxcount") == 1:
        lines.append("Unique")
    slot = _slot_and_type(row)
    if slot:
        lines.append(slot)

    # Weapon damage (Zero/One have dmg_*1..5, Two has 1..2, Three/Four none).
    delay = _int(row, "delay") / 1000.0
    total_avg = 0.0
    for i in range(1, 6):
        lo, hi = _float(row, f"dmg_min{i}"), _float(row, f"dmg_max{i}")
        if hi <= 0:
            continue
        school = wow.DAMAGE_SCHOOLS.get(_int(row, f"dmg_type{i}"), "")
        label = f"{school} Damage".strip()
        text = f"{lo:.0f} - {hi:.0f} {label}"
        if i == 1 and delay:
            text += f" · Speed {delay:.2f}"
        elif i > 1:
            text = "+ " + text
        lines.append(text)
        total_avg += (lo + hi) / 2
    if total_avg and delay:
        lines.append(f"({total_avg / delay:.1f} damage per second)")

    armor = _int(row, "armor")
    if armor:
        lines.append(f"{armor} Armor")
    block = _int(row, "block")
    if block:
        lines.append(f"{block} Block")

    primary: list[str] = []
    secondary: list[str] = []
    for i in range(1, 11):
        stat_type, value = _int(row, f"stat_type{i}"), _int(row, f"stat_value{i}")
        if not value:
            continue
        if stat_type == 0 and realm.core in ("three", "four"):
            continue  # ITEM_MOD_NONE on Cataclysm and later
        sign = "+" if value > 0 else "-"
        if stat_type in wow.PRIMARY_STATS:
            primary.append(f"{sign}{abs(value)} {wow.PRIMARY_STATS[stat_type]}")
        else:
            secondary.append(f"Equip: {sign}{abs(value)} {wow.SECONDARY_STATS.get(stat_type, f'stat {stat_type}')}")
    lines.extend(primary)
    for column, school in wow.RESISTANCE_COLUMNS:
        value = _int(row, column)
        if value:
            lines.append(f"+{value} {school} Resistance")

    durability = _int(row, "MaxDurability")
    if durability:
        lines.append(f"Durability {durability} / {durability}")

    mask = _int(row, "AllowableClass")
    if mask > 0:
        classes = [name for cid, name in wow.CLASSES.items() if mask & (1 << (cid - 1))]
        if 0 < len(classes) <= 5:
            lines.append("Classes: " + ", ".join(classes))
    required = _int(row, "RequiredLevel")
    if required > 1:
        lines.append(f"Requires Level {required}")
    ilvl = item_level(row)
    if ilvl:
        lines.append(f"Item Level {ilvl}")

    lines.extend(secondary)
    for i in range(1, 6):
        spell = _int(row, f"spellid_{i}")
        if spell <= 0:
            continue
        trigger = wow.SPELL_TRIGGERS.get(_int(row, f"spelltrigger_{i}"), "Effect")
        link = realm.spell_link.format(id=spell) if realm.spell_link else ""
        effect = spells.describe(spell) if spells else None
        if effect:  # the trigger word links to the spell, the text reads like the game
            label = f"[{trigger}]({link})" if link else trigger
            lines.append(f"{label}: {discord.utils.escape_markdown(effect)}")
            continue
        name = spells.name(spell) if spells else None
        shown = discord.utils.escape_markdown(name) if name else f"spell #{spell}"
        lines.append(f"{trigger}: [{shown}]({link})" if link else f"{trigger}: {shown}")

    itemset = _int(row, "itemset")
    if itemset:
        link = realm.itemset_link.format(id=itemset) if realm.itemset_link else ""
        lines.append(f"Part of [an item set]({link})" if link else "Part of an item set")
    description = str(row.get("description") or "").strip()
    if description:
        lines.append(f'*"{discord.utils.escape_markdown(description)}"*')
    return lines


def tooltip_embed(row: dict[str, Any], realm: RealmConfig, icon_url: str | None,
                  spells: SpellBook | None = None) -> discord.Embed:
    name, color, _ = wow.quality(item_quality(row))
    e = discord.Embed(
        title=item_name(row),
        url=item_url(realm, int(row["entry"])) or None,
        description="\n".join(tooltip_lines(row, realm, spells))[:4000] or None,
        color=color,
    )
    if icon_url:
        e.set_thumbnail(url=icon_url)
    e.set_footer(text=f"{name} · item #{row['entry']} · {realm.name}")
    return e


class IconService:
    """displayid -> icon name (from ItemDisplayInfo.dbc) -> cached image bytes."""

    def __init__(self, bot: "ServerBot") -> None:
        self.bot = bot
        self.cfg = bot.config.armory
        self.names: dict[int, str] = {}
        self.cache_dir = Path(bot.config.bot.data_dir) / "icons"
        self._missing: dict[str, float] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self.load_error: str | None = None

    async def load(self) -> None:
        if not self.cfg.dbc_path:
            self.load_error = "armory.dbc_path is not set"
            return
        try:
            self.names = await asyncio.to_thread(load_item_icons, self.cfg.dbc_path, self.cfg.icon_field)
        except (OSError, DbcError) as exc:
            self.load_error = str(exc)
            log.warning("Item icons disabled: %s", exc)

    def name_for(self, row: dict[str, Any] | None) -> str | None:
        if not row:
            return None
        return self.names.get(_int(row, "displayid"))

    def url_for(self, row: dict[str, Any] | None) -> str | None:
        name = self.name_for(row)
        if not name or not self.cfg.icon_url:
            return None
        return self.cfg.icon_url.format(icon=name)

    async def image_for(self, row: dict[str, Any] | None) -> bytes | None:
        name = self.name_for(row)
        if not name or not self.cfg.icon_url or not _ICON_NAME_RE.match(name):
            return None
        path = self.cache_dir / f"{name}.img"
        if path.is_file():
            return path.read_bytes()
        if time.monotonic() - self._missing.get(name, -_MISSING_TTL) < _MISSING_TTL:
            return None
        lock = self._locks.setdefault(name, asyncio.Lock())
        async with lock:
            if path.is_file():
                return path.read_bytes()
            data = await self._download(self.cfg.icon_url.format(icon=name))
            if data is None:
                self._missing[name] = time.monotonic()
                return None
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            return data

    async def _download(self, url: str) -> bytes | None:
        try:
            async with self.bot.http_session.get(url, timeout=aiohttp.ClientTimeout(total=8)) as resp:
                if resp.status != 200 or not resp.content_type.startswith("image/"):
                    return None
                data = await resp.content.read(_MAX_ICON_BYTES + 1)
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as exc:
            log.debug("Icon download failed for %s: %s", url, exc)
            return None
        if len(data) > _MAX_ICON_BYTES:
            return None
        try:  # make sure it's really an image before caching it
            from PIL import Image

            with Image.open(io.BytesIO(data)) as img:
                img.verify()
        except Exception:
            return None
        return data
