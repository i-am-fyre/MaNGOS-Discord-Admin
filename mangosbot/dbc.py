"""Read item icon names from the server's ``ItemDisplayInfo.dbc``.

The MaNGOS map extractor copies every DBC file into the server's ``dbc``
folder, so the bot can map ``item_template.displayid`` to an icon name (for
example ``inv_sword_39``) without any external lookup.

WDBC layout: a 20-byte header (magic, record count, field count, record size,
string block size), fixed-size records of 4-byte fields, then a string block.
String fields store an offset into the string block.
"""

from __future__ import annotations

import logging
import re
import struct
from pathlib import Path

log = logging.getLogger(__name__)

_HEADER = struct.Struct("<4s4I")
_ICON_RE = re.compile(r"^[A-Za-z0-9_\-]{1,96}$")


class DbcError(Exception):
    pass


class DbcFile:
    def __init__(self, data: bytes) -> None:
        if len(data) < _HEADER.size:
            raise DbcError("file is too small to be a DBC")
        magic, records, fields, record_size, string_size = _HEADER.unpack_from(data, 0)
        if magic != b"WDBC":
            raise DbcError(f"unsupported DBC signature {magic!r}")
        expected = _HEADER.size + records * record_size + string_size
        if record_size < fields * 4 or len(data) < expected:
            raise DbcError("DBC header does not match the file size")
        self.records = records
        self.fields = fields
        self.record_size = record_size
        self._data = data
        self._strings_at = _HEADER.size + records * record_size
        self._strings_end = self._strings_at + string_size

    def u32(self, record: int, field: int) -> int:
        offset = _HEADER.size + record * self.record_size + field * 4
        return struct.unpack_from("<I", self._data, offset)[0]

    def string(self, record: int, field: int) -> str:
        offset = self._strings_at + self.u32(record, field)
        if offset >= self._strings_end:
            return ""
        end = self._data.find(b"\x00", offset, self._strings_end)
        raw = self._data[offset : end if end != -1 else self._strings_end]
        return raw.decode("utf-8", errors="replace")


def detect_icon_field(dbc: DbcFile, sample: int = 500) -> int | None:
    """Find the field whose strings look like icon names (most start with INV_)."""
    best_field, best_hits = None, 0
    count = min(dbc.records, sample)
    for field in range(1, min(dbc.fields, 16)):
        hits = 0
        for record in range(count):
            try:
                if dbc.string(record, field).lower().startswith("inv_"):
                    hits += 1
            except struct.error:
                break
        if hits > best_hits:
            best_field, best_hits = field, hits
    if count and best_hits >= max(1, count // 10):
        return best_field
    return None


def load_item_icons(path: str | Path, field: int | None = None) -> dict[int, str]:
    """Return ``{displayid: icon_name}`` (lower-case) from ItemDisplayInfo.dbc."""
    path = Path(path)
    if path.is_dir():
        path = path / "ItemDisplayInfo.dbc"
    dbc = DbcFile(path.read_bytes())
    if field is None:
        field = detect_icon_field(dbc)
        if field is None:
            raise DbcError("could not find the icon column; set armory.icon_field")
    elif not 0 < field < dbc.fields:
        raise DbcError(f"icon field {field} is outside the {dbc.fields} fields in this DBC")
    icons: dict[int, str] = {}
    for record in range(dbc.records):
        name = dbc.string(record, field).strip()
        if name and _ICON_RE.match(name):
            icons[dbc.u32(record, 0)] = name.lower()
    log.info("Loaded %d item icons from %s (field %d)", len(icons), path, field)
    return icons
