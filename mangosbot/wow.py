"""Game constants shared by every MaNGOS core (Zero through Four).

Values were checked against the MaNGOS sources (SharedDefines.h,
ItemPrototype.h). Enumerations only ever grew between expansions, so a single
table covers all cores; entries that don't exist on older cores are simply
never seen there.
"""

from __future__ import annotations

RACES: dict[int, str] = {
    1: "Human", 2: "Orc", 3: "Dwarf", 4: "Night Elf", 5: "Undead", 6: "Tauren",
    7: "Gnome", 8: "Troll", 9: "Goblin", 10: "Blood Elf", 11: "Draenei",
    22: "Worgen", 24: "Pandaren", 25: "Pandaren", 26: "Pandaren",
}
ALLIANCE_RACES = frozenset({1, 3, 4, 7, 11, 22, 25})
HORDE_RACES = frozenset({2, 5, 6, 8, 9, 10, 26})

CLASSES: dict[int, str] = {
    1: "Warrior", 2: "Paladin", 3: "Hunter", 4: "Rogue", 5: "Priest", 6: "Death Knight",
    7: "Shaman", 8: "Mage", 9: "Warlock", 10: "Monk", 11: "Druid",
}
CLASS_COLORS: dict[int, int] = {
    1: 0xC69B6D, 2: 0xF48CBA, 3: 0xAAD372, 4: 0xFFF468, 5: 0xFFFFFF, 6: 0xC41E3A,
    7: 0x0070DD, 8: 0x3FC7EB, 9: 0x8788EE, 10: 0x00FF98, 11: 0xFF7C0A,
}

FACTION_COLORS = {"Alliance": 0x2F6FD6, "Horde": 0xC0282D, "Neutral": 0xB8A16A}
FACTION_EMOJI = {"Alliance": "🛡️", "Horde": "⚔️", "Neutral": "☯️"}


def faction(race: int) -> str:
    if race in ALLIANCE_RACES:
        return "Alliance"
    if race in HORDE_RACES:
        return "Horde"
    return "Neutral"


def race_name(race: int) -> str:
    return RACES.get(race, f"Race {race}")


def class_name(cls: int) -> str:
    return CLASSES.get(cls, f"Class {cls}")


# --- Items -------------------------------------------------------------------

# quality -> (name, colour, emoji)
QUALITIES: dict[int, tuple[str, int, str]] = {
    0: ("Poor", 0x9D9D9D, "⬛"),
    1: ("Common", 0xFFFFFF, "⬜"),
    2: ("Uncommon", 0x1EFF00, "🟩"),
    3: ("Rare", 0x0070DD, "🟦"),
    4: ("Epic", 0xA335EE, "🟪"),
    5: ("Legendary", 0xFF8000, "🟧"),
    6: ("Artifact", 0xE6CC80, "🟨"),
    7: ("Heirloom", 0x00CCFF, "🟦"),
}


def quality(q: int) -> tuple[str, int, str]:
    return QUALITIES.get(q, QUALITIES[1])


# Equipment slots (character_inventory.slot where bag = 0).
EQUIPMENT_SLOTS: dict[int, str] = {
    0: "Head", 1: "Neck", 2: "Shoulder", 3: "Shirt", 4: "Chest", 5: "Waist", 6: "Legs",
    7: "Feet", 8: "Wrist", 9: "Hands", 10: "Finger", 11: "Finger", 12: "Trinket",
    13: "Trinket", 14: "Back", 15: "Main Hand", 16: "Off Hand", 17: "Ranged", 18: "Tabard",
}
EQUIPMENT_SLOT_COUNT = 19

# Layout that mirrors the in-game character pane.
PAPERDOLL_LEFT = (0, 1, 2, 14, 4, 3, 18, 8)
PAPERDOLL_RIGHT = (9, 5, 6, 7, 10, 11, 12, 13)
PAPERDOLL_BOTTOM = (15, 16, 17)
# Shirts and tabards don't count towards average item level.
ITEM_LEVEL_EXCLUDED_SLOTS = frozenset({3, 18})

INVENTORY_TYPES: dict[int, str] = {
    1: "Head", 2: "Neck", 3: "Shoulder", 4: "Shirt", 5: "Chest", 6: "Waist", 7: "Legs",
    8: "Feet", 9: "Wrist", 10: "Hands", 11: "Finger", 12: "Trinket", 13: "One-Hand",
    14: "Off Hand", 15: "Ranged", 16: "Back", 17: "Two-Hand", 18: "Bag", 19: "Tabard",
    20: "Chest", 21: "Main Hand", 22: "Off Hand", 23: "Held In Off-hand", 24: "Projectile",
    25: "Thrown", 26: "Ranged", 28: "Relic",
}

WEAPON_SUBCLASSES: dict[int, str] = {
    0: "Axe", 1: "Axe", 2: "Bow", 3: "Gun", 4: "Mace", 5: "Mace", 6: "Polearm", 7: "Sword",
    8: "Sword", 10: "Staff", 13: "Fist Weapon", 14: "Miscellaneous", 15: "Dagger",
    16: "Thrown", 17: "Spear", 18: "Crossbow", 19: "Wand", 20: "Fishing Pole",
}
ARMOR_SUBCLASSES: dict[int, str] = {
    0: "Miscellaneous", 1: "Cloth", 2: "Leather", 3: "Mail", 4: "Plate", 5: "Buckler",
    6: "Shield", 7: "Libram", 8: "Idol", 9: "Totem", 10: "Sigil", 11: "Relic",
}
ITEM_CLASS_WEAPON = 2
ITEM_CLASS_ARMOR = 4

DAMAGE_SCHOOLS: dict[int, str] = {
    0: "", 1: "Holy", 2: "Fire", 3: "Nature", 4: "Frost", 5: "Shadow", 6: "Arcane",
}

BONDING: dict[int, str] = {
    1: "Binds when picked up",
    2: "Binds when equipped",
    3: "Binds when used",
    4: "Quest Item",
    5: "Quest Item",
}

SPELL_TRIGGERS: dict[int, str] = {0: "Use", 1: "Equip", 2: "Chance on hit", 5: "Use", 6: "Learn"}

# ItemModType (stat_typeN). Primary stats are shown in white in game,
# everything else as green "Equip:" lines.
PRIMARY_STATS: dict[int, str] = {
    0: "Mana", 1: "Health", 3: "Agility", 4: "Strength", 5: "Intellect", 6: "Spirit", 7: "Stamina",
}
SECONDARY_STATS: dict[int, str] = {
    12: "Defense Rating", 13: "Dodge Rating", 14: "Parry Rating", 15: "Block Rating",
    16: "Melee Hit Rating", 17: "Ranged Hit Rating", 18: "Spell Hit Rating",
    19: "Melee Critical Strike Rating", 20: "Ranged Critical Strike Rating",
    21: "Spell Critical Strike Rating", 22: "Melee Hit Avoidance Rating",
    23: "Ranged Hit Avoidance Rating", 24: "Spell Hit Avoidance Rating",
    25: "Melee Critical Avoidance Rating", 26: "Ranged Critical Avoidance Rating",
    27: "Spell Critical Avoidance Rating", 28: "Melee Haste Rating", 29: "Ranged Haste Rating",
    30: "Spell Haste Rating", 31: "Hit Rating", 32: "Critical Strike Rating",
    33: "Hit Avoidance Rating", 34: "Critical Avoidance Rating", 35: "Resilience Rating",
    36: "Haste Rating", 37: "Expertise Rating", 38: "Attack Power", 39: "Ranged Attack Power",
    40: "Feral Attack Power", 41: "Healing", 42: "Spell Damage", 43: "Mana per 5 sec.",
    44: "Armor Penetration Rating", 45: "Spell Power", 46: "Health per 5 sec.",
    47: "Spell Penetration", 48: "Block Value", 49: "Mastery Rating", 50: "Bonus Armor",
    51: "Fire Resistance", 52: "Frost Resistance", 53: "Holy Resistance", 54: "Shadow Resistance",
    55: "Nature Resistance", 56: "Arcane Resistance", 57: "PvP Power",
}

RESISTANCE_COLUMNS = (
    ("holy_res", "Holy"), ("fire_res", "Fire"), ("nature_res", "Nature"),
    ("frost_res", "Frost"), ("shadow_res", "Shadow"), ("arcane_res", "Arcane"),
)


# --- Formatting helpers --------------------------------------------------------


def format_money(copper: int) -> str:
    copper = max(0, int(copper or 0))
    gold, rest = divmod(copper, 10000)
    silver, copper = divmod(rest, 100)
    parts = []
    if gold:
        parts.append(f"{gold:,}g")
    if silver or gold:
        parts.append(f"{silver}s")
    parts.append(f"{copper}c")
    return " ".join(parts)


def format_duration(seconds: int | float, *, max_units: int = 3) -> str:
    seconds = int(max(0, seconds or 0))
    units = (("d", 86400), ("h", 3600), ("m", 60), ("s", 1))
    out: list[str] = []
    for suffix, size in units:
        value, seconds = divmod(seconds, size)
        if value:
            out.append(f"{value}{suffix}")
        if len(out) >= max_units:
            break
    return " ".join(out) or "0s"
