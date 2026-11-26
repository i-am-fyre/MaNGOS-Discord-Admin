"""Item effect text ("Equip: Increases ... by up to 20.") from Spell.dbc.

Spell descriptions are templates: ``$s1`` is effect 1's value, ``$d`` the
duration, ``$12345s1`` a value from another spell, ``${$m1*5}`` a formula, and
so on. We fill in the common tokens the same way the game client does and fall
back to the spell's name for anything we can't resolve, so a tooltip never
shows raw ``$`` codes.

Supported for MaNGOS Zero, One and Two, whose Spell.dbc layouts are fixed in
DBCStructure.h. Cataclysm and later split spells across several files.
"""

from __future__ import annotations

import ast
import logging
import operator
import re
from dataclasses import dataclass
from pathlib import Path

from .dbc import DbcError, DbcFile

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class SpellLayout:
    fields: int  # expected column count, used to catch a DBC from the wrong client
    lang_slots: int
    proc_chance: int
    proc_charges: int
    duration_index: int
    stack_amount: int
    die_sides: int
    base_dice: int | None  # removed in WotLK
    base_points: int
    radius_index: int
    aura_period: int
    amplitude: int
    chain_targets: int
    name: int
    description: int


# Column positions from each core's SpellEntry in DBCStructure.h. Per-effect
# columns are the first of three (effects 1-3).
LAYOUTS: dict[str, SpellLayout] = {
    "zero": SpellLayout(173, 8, 25, 26, 30, 39, 64, 67, 76, 88, 94, 97, 100, 120, 138),
    "one": SpellLayout(216, 16, 29, 30, 34, 43, 68, 71, 80, 92, 98, 101, 104, 127, 161),
    "two": SpellLayout(234, 16, 35, 36, 40, 49, 74, None, 80, 92, 98, 101, 104, 136, 170),
}

_FORMULA = re.compile(r"\$\{([^{}$]*(?:\$[^{}$]*)*)\}(?:\.(\d))?")
_TOKEN = re.compile(
    r"\$(?:"
    r"(?P<op>[/*])(?P<factor>\d+(?:\.\d+)?);(?P<ref1>\d*)(?P<var1>[A-Za-z])(?P<idx1>[1-3])?"  # $/10;s1
    r"|(?P<choice>[lLgG])(?P<a>[^:;$]*):(?P<b>[^;$]*);"  # $lpoint:points;  $ghis:her;
    r"|(?P<ref>\d*)(?P<var>[A-Za-z])(?P<idx>[1-3])?"  # $s1  $d  $12345s1
    r")"
)
_LAST_NUMBER = re.compile(r"(\d+(?:\.\d+)?)\D*$")
_OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv}


class Unresolved(Exception):
    """A template token we can't fill in; the caller falls back to the spell name."""


def _fmt(number: float) -> str:
    if abs(number - round(number)) < 1e-6:
        return str(int(round(number)))
    return f"{number:.1f}".rstrip("0").rstrip(".")


def _duration_text(ms: int) -> str:
    if ms < 0:
        return "until cancelled"
    seconds = ms / 1000
    if seconds < 60:
        return f"{_fmt(seconds)} sec"
    if seconds < 3600:
        return f"{_fmt(seconds / 60)} min"
    hours = seconds / 3600
    return f"{_fmt(hours)} hour{'s' if hours != 1 else ''}"


def _safe_eval(expression: str) -> float:
    """Evaluate +, -, *, / and parentheses on numbers; anything else is refused."""
    if len(expression) > 200:
        raise Unresolved
    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError:
        raise Unresolved from None

    def ev(node: ast.AST) -> float:
        if isinstance(node, ast.Expression):
            return ev(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
            return float(node.value)
        if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
            left, right = ev(node.left), ev(node.right)
            if isinstance(node.op, ast.Div) and right == 0:
                raise Unresolved
            return _OPS[type(node.op)](left, right)
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
            value = ev(node.operand)
            return -value if isinstance(node.op, ast.USub) else value
        raise Unresolved

    return ev(tree)


class SpellBook:
    def __init__(self, dbc: DbcFile, layout: SpellLayout, core: str,
                 durations: dict[int, int], radii: dict[int, float]) -> None:
        self._dbc = dbc
        self.layout = layout
        self.core = core
        self._durations = durations
        self._radii = radii
        self._index = {dbc.u32(r, 0): r for r in range(dbc.records)}

    def __len__(self) -> int:
        return len(self._index)

    @classmethod
    def load(cls, path: str | Path, core: str) -> "SpellBook":
        layout = LAYOUTS.get(core)
        if layout is None:
            raise DbcError(f"item effect text isn't supported for MaNGOS {core.capitalize()} yet")
        folder = Path(path)
        if folder.is_file():
            folder = folder.parent
        dbc = DbcFile((folder / "Spell.dbc").read_bytes())
        if dbc.fields != layout.fields:
            raise DbcError(f"Spell.dbc has {dbc.fields} columns, but MaNGOS {core.capitalize()} expects "
                           f"{layout.fields}. Is armory.dbc_path from the right game version?")
        durations: dict[int, int] = {}
        radii: dict[int, float] = {}
        try:
            d = DbcFile((folder / "SpellDuration.dbc").read_bytes())
            durations = {d.u32(r, 0): d.i32(r, 1) for r in range(d.records)}
        except (OSError, DbcError):
            log.info("SpellDuration.dbc not found; $d durations won't be shown")
        try:
            d = DbcFile((folder / "SpellRadius.dbc").read_bytes())
            radii = {d.u32(r, 0): d.f32(r, 1) for r in range(d.records)}
        except (OSError, DbcError):
            pass
        book = cls(dbc, layout, core, durations, radii)
        log.info("Loaded %d spells from %s", len(book), folder / "Spell.dbc")
        return book

    # -- lookups ---------------------------------------------------------------------

    def name(self, spell_id: int) -> str | None:
        record = self._index.get(spell_id)
        if record is None:
            return None
        return self._dbc.first_string(record, self.layout.name, self.layout.lang_slots) or None

    def describe(self, spell_id: int) -> str | None:
        """The spell's description with values filled in, or None if we can't."""
        record = self._index.get(spell_id)
        if record is None:
            return None
        template = self._dbc.first_string(record, self.layout.description, self.layout.lang_slots)
        if not template:
            return None
        try:
            return self._render(record, template)
        except Unresolved:
            return None

    # -- values ------------------------------------------------------------------------

    def _record(self, ref: str, default: int) -> int:
        if not ref:
            return default
        record = self._index.get(int(ref))
        if record is None:
            raise Unresolved
        return record

    def _points(self, record: int, i: int) -> tuple[int, int]:
        """(min, max) of effect i, like the server's base points + dice roll."""
        layout, dbc = self.layout, self._dbc
        base = dbc.i32(record, layout.base_points + i)
        sides = dbc.i32(record, layout.die_sides + i)
        dice = dbc.i32(record, layout.base_dice + i) if layout.base_dice is not None else 1
        low = base + dice
        high = base + max(sides, dice)
        return abs(low), abs(high)

    def _duration_ms(self, record: int) -> int:
        index = self._dbc.u32(record, self.layout.duration_index)
        if index not in self._durations:
            raise Unresolved
        return self._durations[index]

    def _value(self, record: int, var: str, idx: str | None) -> tuple[str, float]:
        """Return (display text, number) for one token."""
        i = (int(idx) - 1) if idx else 0
        layout, dbc = self.layout, self._dbc
        key = var if var == "M" else var.lower()
        if key == "s":
            low, high = self._points(record, i)
            return (_fmt(low) if low == high else f"{_fmt(low)} to {_fmt(high)}"), low
        if key == "m":
            low, _ = self._points(record, i)
            return _fmt(low), low
        if key == "M":
            _, high = self._points(record, i)
            return _fmt(high), high
        if key == "o":
            low, _ = self._points(record, i)
            period = dbc.u32(record, layout.aura_period + i)
            duration = self._duration_ms(record)
            ticks = duration // period if period and duration > 0 else 1
            return _fmt(low * ticks), low * ticks
        if key == "t":
            seconds = dbc.u32(record, layout.aura_period + i) / 1000
            return _fmt(seconds), seconds
        if key == "d":
            ms = self._duration_ms(record)
            return _duration_text(ms), ms / 1000
        if key == "h":
            value = dbc.u32(record, layout.proc_chance)
            return _fmt(value), value
        if key == "n":
            value = dbc.u32(record, layout.proc_charges)
            return _fmt(value), value
        if key == "u":
            value = dbc.u32(record, layout.stack_amount)
            return _fmt(value), value
        if key == "x":
            value = dbc.u32(record, layout.chain_targets + i)
            return _fmt(value), value
        if key == "a":
            radius = self._radii.get(dbc.u32(record, layout.radius_index + i))
            if radius is None:
                raise Unresolved
            return _fmt(radius), radius
        if key == "e":
            value = dbc.f32(record, layout.amplitude + i)
            return _fmt(value), value
        raise Unresolved

    # -- template rendering ------------------------------------------------------------

    def _render(self, record: int, template: str) -> str:
        def formula(match: re.Match[str]) -> str:
            inner = _TOKEN.sub(lambda m: _fmt(self._token_number(record, m)), match.group(1))
            value = _safe_eval(inner)
            decimals = match.group(2)
            return f"{value:.{decimals}f}" if decimals else _fmt(value)

        text = _FORMULA.sub(formula, template)
        out: list[str] = []
        pos = 0
        for match in _TOKEN.finditer(text):
            out.append(text[pos:match.start()])
            pos = match.end()
            if match.group("choice"):
                previous = _LAST_NUMBER.search("".join(out))
                if match.group("choice") in "lL":
                    singular = previous is not None and float(previous.group(1)) == 1
                    out.append(match.group("a") if singular else match.group("b"))
                else:  # $g<male>:<female>; we don't know who's reading
                    out.append(match.group("a"))
                continue
            if match.group("op"):
                number = self._token_number(record, match)
                out.append(_fmt(number))
                continue
            display, _ = self._value(self._record(match.group("ref"), record), match.group("var"), match.group("idx"))
            out.append(display)
        out.append(text[pos:])
        result = " ".join("".join(out).split())  # newlines and double spaces
        if "$" in result:
            raise Unresolved
        return result

    def _token_number(self, record: int, match: re.Match[str]) -> float:
        if match.group("choice"):
            raise Unresolved
        if match.group("op"):
            _, number = self._value(self._record(match.group("ref1"), record), match.group("var1"), match.group("idx1"))
            factor = float(match.group("factor"))
            if match.group("op") == "/":
                if factor == 0:
                    raise Unresolved
                return number / factor
            return number * factor
        _, number = self._value(self._record(match.group("ref"), record), match.group("var"), match.group("idx"))
        return number
