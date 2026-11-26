import struct

import pytest

from mangosbot.dbc import DbcError, DbcFile, detect_icon_field, load_item_icons


def make_item_display_info(records: list[tuple[int, str, str]]) -> bytes:
    """Build a WDBC shaped like ItemDisplayInfo: ID, model[2], texture[2], icon[2], flags."""
    strings = b"\x00"
    offsets: dict[str, int] = {}

    def add(s: str) -> int:
        nonlocal strings
        if s not in offsets:
            offsets[s] = len(strings)
            strings += s.encode() + b"\x00"
        return offsets[s]

    fields = 8
    body = b""
    for display_id, model, icon in records:
        body += struct.pack("<8I", display_id, add(model), 0, add(model.replace(".mdx", ".blp")), 0, add(icon), 0, 0)
    header = struct.pack("<4s4I", b"WDBC", len(records), fields, fields * 4, len(strings))
    return header + body + strings


def test_load_icons_autodetects_icon_column(tmp_path):
    data = make_item_display_info([
        (1001, "Sword_1H_Long.mdx", "INV_Sword_39"),
        (1002, "Helm_Plate.mdx", "INV_Helmet_01"),
        (1003, "Ring.mdx", "INV_Jewelry_Ring_15"),
    ])
    path = tmp_path / "ItemDisplayInfo.dbc"
    path.write_bytes(data)
    assert detect_icon_field(DbcFile(data)) == 5
    icons = load_item_icons(tmp_path)  # a folder works too
    assert icons == {1001: "inv_sword_39", 1002: "inv_helmet_01", 1003: "inv_jewelry_ring_15"}


def test_explicit_field_and_bad_files(tmp_path):
    data = make_item_display_info([(7, "A.mdx", "INV_Misc_QuestionMark")])
    path = tmp_path / "x.dbc"
    path.write_bytes(data)
    assert load_item_icons(path, field=5) == {7: "inv_misc_questionmark"}
    with pytest.raises(DbcError):
        load_item_icons(path, field=40)
    with pytest.raises(DbcError):
        DbcFile(b"WDB2" + data[4:])
    with pytest.raises(DbcError):
        DbcFile(data[:30])
