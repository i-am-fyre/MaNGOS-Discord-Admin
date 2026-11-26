"""Spell.dbc effect text, using a synthetic DBC in MaNGOS Zero's 173-column layout."""

import struct

import pytest

from mangosbot import items
from mangosbot.config import parse_config
from mangosbot.dbc import DbcError
from mangosbot.spells import LAYOUTS, SpellBook

ZERO = LAYOUTS["zero"]


class DbcBuilder:
    def __init__(self, fields: int):
        self.fields = fields
        self.rows: list[list[int]] = []
        self.strings = b"\x00"
        self._offsets: dict[str, int] = {}

    def text(self, s: str) -> int:
        if s not in self._offsets:
            self._offsets[s] = len(self.strings)
            self.strings += s.encode() + b"\x00"
        return self._offsets[s]

    def row(self, values: dict[int, int | float]) -> None:
        row = [0] * self.fields
        for field, value in values.items():
            row[field] = struct.unpack("<I", struct.pack("<f", value))[0] if isinstance(value, float) else value & 0xFFFFFFFF
        self.rows.append(row)

    def bytes(self) -> bytes:
        header = struct.pack("<4s4I", b"WDBC", len(self.rows), self.fields, self.fields * 4, len(self.strings))
        body = b"".join(struct.pack(f"<{self.fields}I", *r) for r in self.rows)
        return header + body + self.strings


def spell(b: DbcBuilder, spell_id: int, name: str, desc: str, *, points=(0, 0, 0), dice=(1, 1, 1), sides=(1, 1, 1),
          duration=0, proc=0, period=(0, 0, 0), chain=(0, 0, 0), slot: int = 0) -> None:
    values: dict[int, int | float] = {0: spell_id, ZERO.name + slot: b.text(name), ZERO.description + slot: b.text(desc),
                                      ZERO.duration_index: duration, ZERO.proc_chance: proc}
    for i in range(3):
        values[ZERO.base_points + i] = points[i]
        values[ZERO.base_dice + i] = dice[i]
        values[ZERO.die_sides + i] = sides[i]
        values[ZERO.aura_period + i] = period[i]
        values[ZERO.chain_targets + i] = chain[i]
    b.row(values)


@pytest.fixture
def book(tmp_path):
    b = DbcBuilder(ZERO.fields)
    # Real Zero data: Lavastone Hammer's equip spell and Thunderfury.
    spell(b, 14799, "Increase Spell Dam 20",
          "Increases damage and healing done by magical spells and effects by up to $s1.", points=(19, 19, 0))
    spell(b, 21992, "Thunderfury",
          "Blasts your enemy with lightning, dealing $s2 Nature damage and then jumping to additional nearby "
          "enemies.  Each jump reduces that victim's Nature resistance by $s1. Affects $x1 targets. Your primary "
          "target is also consumed by a cyclone, slowing its attack speed by $27648s1% for $d.",
          points=(-26, 299, 0), duration=29, chain=(5, 0, 0))
    spell(b, 27648, "Thunderfury", "Slows attack speed.", points=(-21, 0, 0))
    spell(b, 133, "Fireball", "Hurls a fiery ball that causes $s1 Fire damage and an additional $o2 Fire damage over $d.",
          points=(13, 1, 0), sides=(9, 1, 0), duration=8, period=(0, 2000, 0))
    spell(b, 500, "Formula", "Gives ${$s1*2} points, $/10;s1 per tick, and $s1 $lpoint:points;.", points=(9, 0, 0))
    spell(b, 501, "One", "Grants $s1 $lpoint:points;.", points=(0, 0, 0))
    spell(b, 600, "Hearthstone", "Returns you to $z.", points=(0, 0, 0))
    spell(b, 700, "Proc", "When struck, $h% chance to heal you for $s1.", points=(49, 0, 0), proc=5)
    spell(b, 800, "Localised", "Erhöht die Abklingzeit um $s1 Sek.", points=(2, 0, 0), slot=3)  # deDE client slot
    (tmp_path / "Spell.dbc").write_bytes(b.bytes())

    dur = DbcBuilder(4)
    for index, ms in ((21, -1), (29, 12000), (8, 8000)):
        dur.row({0: index, 1: ms, 3: ms})
    (tmp_path / "SpellDuration.dbc").write_bytes(dur.bytes())
    return SpellBook.load(tmp_path, "zero")


def test_item_effects_match_the_game(book):
    assert book.describe(14799) == "Increases damage and healing done by magical spells and effects by up to 20."
    assert book.describe(21992) == (
        "Blasts your enemy with lightning, dealing 300 Nature damage and then jumping to additional nearby enemies. "
        "Each jump reduces that victim's Nature resistance by 25. Affects 5 targets. Your primary target is also "
        "consumed by a cyclone, slowing its attack speed by 20% for 12 sec."
    )


def test_ranges_periodic_formulas_and_plurals(book):
    assert book.describe(133) == "Hurls a fiery ball that causes 14 to 22 Fire damage and an additional 8 Fire damage over 8 sec."
    assert book.describe(500) == "Gives 20 points, 1 per tick, and 10 points."
    assert book.describe(501) == "Grants 1 point."
    assert book.describe(700) == "When struck, 5% chance to heal you for 50."
    assert book.describe(800) == "Erhöht die Abklingzeit um 3 Sek."


def test_unresolvable_falls_back_to_the_name(book):
    assert book.describe(600) is None
    assert book.name(600) == "Hearthstone"
    assert book.describe(99999) is None and book.name(99999) is None


def test_wrong_client_version_is_reported(tmp_path):
    (tmp_path / "Spell.dbc").write_bytes(DbcBuilder(216).bytes())  # a TBC-sized file
    with pytest.raises(DbcError, match="216 columns"):
        SpellBook.load(tmp_path, "zero")
    with pytest.raises(DbcError, match="isn't supported"):
        SpellBook.load(tmp_path, "four")


def test_tooltip_uses_spell_text(book, env):
    realm = parse_config({"bot": {"token": "t", "guild_id": 1}, "database": {"user": "u"},
                          "realm": [{"id": 1, "name": "Azeroth", "core": "zero"}]}).realms[0]
    row = {"entry": 22208, "name": "Lavastone Hammer", "Quality": 3, "spellid_1": 14799, "spelltrigger_1": 1,
           "spellid_2": 600, "spelltrigger_2": 0}
    lines = items.tooltip_lines(row, realm, book)
    assert ("[Equip](https://www.wowhead.com/classic/spell=14799): Increases damage and healing done by magical "
            "spells and effects by up to 20.") in lines
    assert "Use: [Hearthstone](https://www.wowhead.com/classic/spell=600)" in lines  # name fallback
    # A book for another core is ignored rather than misread.
    two = parse_config({"bot": {"token": "t", "guild_id": 1}, "database": {"user": "u"},
                        "realm": [{"id": 1, "name": "N", "core": "two"}]}).realms[0]
    assert any("spell #14799" in l for l in items.tooltip_lines(row, two, book))
