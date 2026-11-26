"""Render an armory "paper doll" card as a PNG with Pillow.

Laid out like the in-game character pane: armour down the left, jewellery and
the rest down the right, weapons along the bottom, the character in the middle.
Everything is drawn here; the only images used are item icons (optional).
"""

from __future__ import annotations

import io
from dataclasses import dataclass, field
from functools import lru_cache

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from . import wow

WIDTH, HEIGHT = 960, 640
ICON = 48
ROW = 60
MARGIN = 24
TOP = 34

BG_TOP = (22, 24, 31)
BG_BOTTOM = (9, 10, 14)
TEXT = (230, 230, 235)
MUTED = (150, 152, 162)
FAINT = (70, 72, 82)
GOLD = (241, 196, 15)
SLOT_BG = (30, 32, 40)


@dataclass
class CardItem:
    slot: int
    name: str
    quality: int
    item_level: int
    icon: bytes | None = None


@dataclass
class CharacterCard:
    name: str
    level: int
    race: int
    cls: int
    guild: str | None
    kills: int | None
    average_item_level: float | None
    realm: str
    footer: str
    items: dict[int, CardItem] = field(default_factory=dict)


def _rgb(color: int) -> tuple[int, int, int]:
    return (color >> 16) & 0xFF, (color >> 8) & 0xFF, color & 0xFF


@lru_cache(maxsize=32)
def _font(path: str, size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    if path:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            pass
    return ImageFont.load_default(size=size)


def _fit(draw: ImageDraw.ImageDraw, text: str, font, width: int) -> str:
    if draw.textlength(text, font=font) <= width:
        return text
    while text and draw.textlength(text + "…", font=font) > width:
        text = text[:-1]
    return text.rstrip() + "…"


def _background(faction: str) -> Image.Image:
    img = Image.new("RGB", (WIDTH, HEIGHT), BG_BOTTOM)
    draw = ImageDraw.Draw(img)
    for y in range(HEIGHT):
        t = y / HEIGHT
        draw.line(
            [(0, y), (WIDTH, y)],
            fill=tuple(int(BG_TOP[i] * (1 - t) + BG_BOTTOM[i] * t) for i in range(3)),
        )
    # Soft faction-coloured glow behind the character.
    glow = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    gdraw = ImageDraw.Draw(glow)
    r, g, b = _rgb(wow.FACTION_COLORS.get(faction, 0x888888))
    gdraw.ellipse([WIDTH // 2 - 240, 40, WIDTH // 2 + 240, 460], fill=(r, g, b, 70))
    glow = glow.filter(ImageFilter.GaussianBlur(70))
    img.paste(glow, (0, 0), glow)
    return img


def _icon_box(img: Image.Image, x: int, y: int, item: CardItem | None, mark_font) -> None:
    draw = ImageDraw.Draw(img)
    border = _rgb(wow.quality(item.quality)[1]) if item else FAINT
    draw.rounded_rectangle([x - 2, y - 2, x + ICON + 1, y + ICON + 1], radius=6, fill=border)
    draw.rounded_rectangle([x, y, x + ICON - 1, y + ICON - 1], radius=5, fill=SLOT_BG)
    if item and item.icon:
        try:
            with Image.open(io.BytesIO(item.icon)) as raw:
                icon = raw.convert("RGBA").resize((ICON - 4, ICON - 4), Image.LANCZOS)
            mask = Image.new("L", icon.size, 0)
            ImageDraw.Draw(mask).rounded_rectangle([0, 0, icon.size[0] - 1, icon.size[1] - 1], radius=4, fill=255)
            img.paste(icon, (x + 2, y + 2), mask)
            return
        except Exception:
            pass
    if item:  # no icon available: a quality-coloured "?" like the game's placeholder icon
        tw = draw.textlength("?", font=mark_font)
        draw.text((x + (ICON - tw) / 2, y + ICON / 2 - 15), "?", font=mark_font, fill=border)


def _slot_row(
    img: Image.Image, x: int, y: int, slot: int, item: CardItem | None, fonts, *, mirror: bool, text_width: int = 250
) -> None:
    draw = ImageDraw.Draw(img)
    name_font, small_font, mark_font = fonts
    label = wow.EQUIPMENT_SLOTS[slot]
    if mirror:  # icon on the right, text right-aligned to its left
        _icon_box(img, x - ICON, y, item, mark_font)
        right = x - ICON - 12
        if item:
            name = _fit(draw, item.name, name_font, text_width)
            draw.text((right - draw.textlength(name, font=name_font), y + 4), name, font=name_font,
                      fill=_rgb(wow.quality(item.quality)[1]))
            sub = f"{label} · ilvl {item.item_level}" if item.item_level else label
        else:
            sub = label
        draw.text((right - draw.textlength(sub, font=small_font), y + (28 if item else 16)), sub, font=small_font,
                  fill=MUTED if item else FAINT)
    else:
        _icon_box(img, x, y, item, mark_font)
        left = x + ICON + 12
        if item:
            name = _fit(draw, item.name, name_font, text_width)
            draw.text((left, y + 4), name, font=name_font, fill=_rgb(wow.quality(item.quality)[1]))
            sub = f"{label} · ilvl {item.item_level}" if item.item_level else label
        else:
            sub = label
        draw.text((left, y + (28 if item else 16)), sub, font=small_font, fill=MUTED if item else FAINT)


def render_character_card(card: CharacterCard, font_path: str = "") -> bytes:
    faction = wow.faction(card.race)
    img = _background(faction)
    draw = ImageDraw.Draw(img)
    title_font = _font(font_path, 38)
    sub_font = _font(font_path, 19)
    name_font = _font(font_path, 16)
    small_font = _font(font_path, 13)
    fonts = (name_font, small_font, _font(font_path, 26))

    for i, slot in enumerate(wow.PAPERDOLL_LEFT):
        _slot_row(img, MARGIN, TOP + i * ROW, slot, card.items.get(slot), fonts, mirror=False)
    for i, slot in enumerate(wow.PAPERDOLL_RIGHT):
        _slot_row(img, WIDTH - MARGIN, TOP + i * ROW, slot, card.items.get(slot), fonts, mirror=True)

    # Weapons, centred along the bottom.
    bottom_y = TOP + len(wow.PAPERDOLL_LEFT) * ROW + 26
    span = 300
    start_x = (WIDTH - span * len(wow.PAPERDOLL_BOTTOM)) // 2 + 10
    for i, slot in enumerate(wow.PAPERDOLL_BOTTOM):
        _slot_row(img, start_x + i * span, bottom_y, slot, card.items.get(slot), fonts, mirror=False, text_width=225)

    # Centre column: name, level/race/class, guild, stats.
    cx = WIDTH // 2
    class_color = _rgb(wow.CLASS_COLORS.get(card.cls, 0xFFFFFF))
    name = _fit(draw, card.name, title_font, 280)
    draw.text((cx - draw.textlength(name, font=title_font) / 2, 120), name, font=title_font, fill=class_color)
    line = f"Level {card.level} {wow.race_name(card.race)} {wow.class_name(card.cls)}"
    line = _fit(draw, line, sub_font, 280)
    draw.text((cx - draw.textlength(line, font=sub_font) / 2, 172), line, font=sub_font, fill=TEXT)
    y = 204
    if card.guild:
        guild = _fit(draw, f"<{card.guild}>", sub_font, 280)
        draw.text((cx - draw.textlength(guild, font=sub_font) / 2, y), guild, font=sub_font, fill=(130, 220, 140))
        y += 30
    faction_text = faction
    draw.text((cx - draw.textlength(faction_text, font=name_font) / 2, y), faction_text, font=name_font,
              fill=_rgb(wow.FACTION_COLORS.get(faction, 0xAAAAAA)))

    stats: list[tuple[str, str]] = []
    if card.average_item_level:
        stats.append(("Item Level", f"{card.average_item_level:g}"))
    if card.kills:
        stats.append(("Honorable Kills", f"{card.kills:,}"))
    sy = 300
    for label, value in stats:
        draw.text((cx - draw.textlength(value, font=sub_font) / 2, sy), value, font=sub_font, fill=GOLD)
        draw.text((cx - draw.textlength(label, font=small_font) / 2, sy + 26), label, font=small_font, fill=MUTED)
        sy += 62

    draw.line([(MARGIN, HEIGHT - 34), (WIDTH - MARGIN, HEIGHT - 34)], fill=FAINT, width=1)
    foot = _fit(draw, card.footer, small_font, WIDTH - 2 * MARGIN)
    draw.text((WIDTH - MARGIN - draw.textlength(foot, font=small_font), HEIGHT - 26), foot, font=small_font, fill=MUTED)
    draw.text((MARGIN, HEIGHT - 26), card.realm, font=small_font, fill=MUTED)

    out = io.BytesIO()
    img.save(out, format="PNG", optimize=True)
    return out.getvalue()
