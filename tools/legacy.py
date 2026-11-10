"""MCEdit's `.schematic`, the format before 1.13: blocks by numeric id and a data value, made into 1.13's block states.

The table is minecraft-data's (`data/legacy.json`, MIT). A data value the table lacks falls back to the block's first
state; an id it lacks becomes air, and the count of those comes back so a build missing much can be left out.
"""

from __future__ import annotations

import array
import json
import os

import nbt
from schematic import AIR, Structure

# 1.13's own DataVersion: the names below are 1.13's, and the plugin's game brings them up to date.
DATA_VERSION = 1519

_TABLE: dict | None = None


def _table() -> dict:
    global _TABLE
    if _TABLE is None:
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "legacy.json"), encoding="utf-8") as file:
            _TABLE = json.load(file)["blocks"]
    return _TABLE


def state(block: int, data: int) -> str | None:
    table = _table()
    found = table.get(f"{block}:{data}") or table.get(f"{block}:0")
    return found


def read(root: dict) -> tuple[Structure, int]:
    """The build, and how many of its blocks had an id the table doesn't know."""
    schematic = root.get("Schematic", root)
    width, height, length = (nbt.value(schematic[key]) & 0xFFFF for key in ("Width", "Height", "Length"))
    count = width * height * length
    ids = bytes(nbt.value(schematic["Blocks"]))
    datas = bytes(nbt.value(schematic.get("Data", nbt.Tag(nbt.BYTE_ARRAY, bytes(count)))))
    add = bytes(nbt.value(schematic["AddBlocks"])) if "AddBlocks" in schematic else b""
    if len(ids) != count:
        raise ValueError(f"{len(ids)} blocks for a box of {count}")
    # Every id and data value a byte pair can name, looked up once: the cells then only index into it.
    palette, keys = [AIR], {AIR: 0}
    span = 4096 if not add else 65536
    lookup = [0] * span
    missing = [False] * span
    for key in range(span):
        block, data = key >> 4, key & 0x0F
        if block == 0:
            continue
        name = state(block, data)
        if name is None:
            missing[key] = True
            continue
        lookup[key] = keys.setdefault(name, len(palette))
        if lookup[key] == len(palette):
            palette.append(name)
    if add:
        # AddBlocks holds each id's high four bits, two blocks a byte.
        high = [((add[i >> 1] & 0x0F) if i & 1 else (add[i >> 1] >> 4)) for i in range(count)]
        codes = [((h << 8 | b) << 4) | (d & 0x0F) for h, b, d in zip(high, ids, datas)]
    else:
        codes = [(b << 4) | (d & 0x0F) for b, d in zip(ids, datas)]
    unknown = sum(1 for code in codes if missing[code])
    cells = array.array("I", (lookup[code] for code in codes))
    block_entities = []
    for entry in schematic.get("TileEntities", nbt.List(nbt.COMPOUND)).items:
        pos = [nbt.value(entry.get(axis, 0)) for axis in ("x", "y", "z")]
        block_entities.append({"pos": pos, "id": str(entry.get("id", "")), "data": {k: v for k, v in entry.items() if k not in ("x", "y", "z", "id")}})
    return Structure(width, height, length, palette, cells, DATA_VERSION, block_entities, [], "", ""), unknown
