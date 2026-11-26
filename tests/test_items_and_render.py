import io
import os
from pathlib import Path

from PIL import Image

from mangosbot import items
from mangosbot.cogs.staff import parse_ban_duration
from mangosbot.config import parse_config
from mangosbot.render import CardItem, CharacterCard, render_character_card


def realm(core, env):
    cfg = parse_config({
        "bot": {"token": "t", "guild_id": 1},
        "database": {"user": "u"},
        "realm": [{"id": 1, "name": "Azeroth", "core": core}],
    })
    return cfg.realms[0]


def test_zero_weapon_tooltip(env):
    # Thunderfury-like row in mangoszero's item_template shape.
    row = {"entry": 19019, "name": "Thunderfury", "Quality": 5, "class": 2, "subclass": 7, "InventoryType": 13,
           "bonding": 1, "maxcount": 1, "dmg_min1": 44, "dmg_max1": 115, "dmg_type1": 0,
           "dmg_min2": 16, "dmg_max2": 30, "dmg_type2": 3, "delay": 1900, "armor": 0,
           "stat_type1": 3, "stat_value1": 5, "stat_type2": 7, "stat_value2": 8, "fire_res": 8, "nature_res": 9,
           "MaxDurability": 125, "RequiredLevel": 60, "ItemLevel": 80, "spellid_1": 21992, "spelltrigger_1": 2}
    lines = items.tooltip_lines(row, realm("zero", env))
    assert "Binds when picked up" in lines and "Unique" in lines
    assert "One-Hand · Sword" in lines
    assert "44 - 115 Damage · Speed 1.90" in lines
    assert "+ 16 - 30 Nature Damage" in lines
    assert "(53.9 damage per second)" in lines  # (79.5 + 23) / 1.9
    assert "+5 Agility" in lines and "+8 Stamina" in lines
    assert "+8 Fire Resistance" in lines
    assert "Requires Level 60" in lines and "Item Level 80" in lines
    assert any(l.startswith("Chance on hit: [spell #21992](https://www.wowhead.com/classic/spell=21992)") for l in lines)


def test_four_tooltip_without_damage_columns(env):
    row = {"entry": 1, "name": "Helm", "Quality": 4, "class": 4, "subclass": 4, "InventoryType": 1,
           "stat_type1": 0, "stat_value1": 0, "stat_type2": 4, "stat_value2": 300, "stat_type3": 49, "stat_value3": 150,
           "ItemLevel": 496}
    lines = items.tooltip_lines(row, realm("four", env))
    assert lines[0] == "Head · Plate"
    assert "+300 Strength" in lines
    assert "Equip: +150 Mastery Rating" in lines
    assert not any("Damage" in l for l in lines)


def test_average_item_level_skips_shirt_and_tabard():
    gear = {0: {"ItemLevel": 70}, 3: {"ItemLevel": 1}, 18: {"ItemLevel": 1}, 15: {"ItemLevel": 80}}
    assert items.average_item_level(gear) == 75.0


def test_ban_durations():
    assert parse_ban_duration("7d") == "7d"
    assert parse_ban_duration("1d 12h") == "1d12h"
    assert parse_ban_duration("perm") == "-1"
    assert parse_ban_duration("7") is None  # mangos would read this as permanent!
    assert parse_ban_duration("-5") is None
    assert parse_ban_duration("1w") is None


def _icon(color):
    img = Image.new("RGB", (56, 56), color)
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


def test_render_card(tmp_path):
    card = CharacterCard(
        name="Thrallson", level=60, race=2, cls=7, guild="Earthen Ring", kills=1234, average_item_level=66.5,
        realm="Azeroth", footer="Ask MaNGOS · My MaNGOS Server",
        items={
            0: CardItem(0, "Helm of Ten Storms", 4, 76, _icon((40, 90, 200))),
            1: CardItem(1, "Onyxia Tooth Pendant", 4, 74, None),
            15: CardItem(15, "Thunderfury, Blessed Blade of the Windseeker", 5, 80, _icon((200, 140, 20))),
            9: CardItem(9, "Gauntlets of Ten Storms", 4, 76, b"not an image"),
        },
    )
    png = render_character_card(card)
    with Image.open(io.BytesIO(png)) as img:
        assert img.size == (960, 640)
    out = os.environ.get("ARMORY_PREVIEW")
    if out:  # handy for eyeballing the layout: ARMORY_PREVIEW=card.png pytest -k render
        Path(out).write_bytes(png)
