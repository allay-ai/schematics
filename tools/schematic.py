"""Schematics as one shape: read Sponge `.schem` (v2, v3) and Litematica `.litematic`, write Sponge v3.

A `Structure` is a box of `width` (x) × `height` (y) × `length` (z) cells, each an index into `palette`, a list of block
states as the game writes them (`minecraft:oak_stairs[facing=north,half=bottom]`). Cells run x fastest, then z, then
y, as Sponge stores them. Block entities and entities keep their own data, positions relative to the box's corner.
"""

from __future__ import annotations

import array
from dataclasses import dataclass, field

import nbt
from nbt import BYTE_ARRAY, COMPOUND, DOUBLE, INT, INT_ARRAY, List, SHORT, STRING, Tag

AIR = "minecraft:air"
# What counts as nothing to place: empty air, and the blank a structure block leaves alone.
EMPTY = {"minecraft:air", "minecraft:cave_air", "minecraft:void_air", "minecraft:structure_void"}


@dataclass
class Structure:
    width: int
    height: int
    length: int
    palette: list
    blocks: array.array
    data_version: int
    block_entities: list = field(default_factory=list)
    entities: list = field(default_factory=list)
    name: str = ""
    author: str = ""

    def index(self, x: int, y: int, z: int) -> int:
        return x + z * self.width + y * self.width * self.length

    def at(self, x: int, y: int, z: int) -> str:
        return self.palette[self.blocks[self.index(x, y, z)]]


def block_id(state: str) -> str:
    """The block without its states: `minecraft:oak_stairs`."""
    return state.split("[", 1)[0]


def state_of(name: str, properties: dict | None) -> str:
    """A block state string, its properties sorted, as one palette key per state."""
    if not properties:
        return name
    return name + "[" + ",".join(f"{key}={nbt.value(properties[key])}" for key in sorted(properties)) + "]"


def _varints(data: bytes, count: int) -> array.array:
    out = array.array("I")
    value = shift = 0
    for byte in data:
        value |= (byte & 0x7F) << shift
        if byte & 0x80:
            shift += 7
            if shift > 35:
                raise ValueError("a block index past 32 bits")
            continue
        out.append(value)
        value = shift = 0
    if len(out) != count:
        raise ValueError(f"{len(out)} blocks for a box of {count}")
    return out


def _encode_varints(values) -> bytes:
    out = bytearray()
    for value in values:
        while value >= 0x80:
            out.append((value & 0x7F) | 0x80)
            value >>= 7
        out.append(value)
    return bytes(out)


def _palette(raw: dict) -> list:
    """A Sponge palette, state to index, as a list by index."""
    palette = [AIR] * (max(nbt.value(v) for v in raw.values()) + 1 if raw else 1)
    for state, index in raw.items():
        palette[nbt.value(index)] = state
    return palette


def _sponge(root: dict) -> Structure:
    schematic = root.get("Schematic", root)
    version = nbt.value(schematic.get("Version", 0))
    width, height, length = (nbt.value(schematic[key]) & 0xFFFF for key in ("Width", "Height", "Length"))
    data_version = nbt.value(schematic.get("DataVersion", 0))
    meta = schematic.get("Metadata", {})
    if version >= 3:
        blocks = schematic.get("Blocks", {})
        palette = _palette(blocks.get("Palette", {}))
        data = nbt.value(blocks.get("Data", Tag(BYTE_ARRAY, b"")))
        raw_entities = blocks.get("BlockEntities", List(COMPOUND)).items
    elif version == 2:
        palette = _palette(schematic.get("Palette", {}))
        data = nbt.value(schematic.get("BlockData", Tag(BYTE_ARRAY, b"")))
        raw_entities = schematic.get("BlockEntities", List(COMPOUND)).items
    else:
        raise ValueError(f"Sponge schematic version {version} isn't supported: re-save it with WorldEdit 7 or newer")
    cells = _varints(data, width * height * length)
    block_entities = []
    for entry in raw_entities:
        pos = nbt.value(entry["Pos"])
        # v2 keeps the entity's own fields beside Pos and Id; v3 under Data.
        data_tag = entry.get("Data") if version >= 3 else {k: v for k, v in entry.items() if k not in ("Pos", "Id")}
        block_entities.append({"pos": list(pos), "id": entry.get("Id", ""), "data": data_tag or {}})
    entities = []
    for entry in schematic.get("Entities", List(COMPOUND)).items:
        pos = [nbt.value(p) for p in entry["Pos"].items]
        entities.append({"pos": pos, "id": entry.get("Id", ""), "data": entry.get("Data", {k: v for k, v in entry.items() if k not in ("Pos", "Id")})})
    return Structure(width, height, length, palette, array.array("I", cells), data_version, block_entities, entities,
                     str(meta.get("Name", "")), str(meta.get("Author", "")))


def _litematic(root: dict) -> Structure:
    """Every region of a Litematica file in one box: regions may overlap, a later one wins where it holds a block."""
    regions = root.get("Regions", {})
    if not regions:
        raise ValueError("the litematic has no regions")
    boxes = []
    for region in regions.values():
        position = region["Position"]
        size = region["Size"]
        px, py, pz = (nbt.value(position[k]) for k in "xyz")
        sx, sy, sz = (nbt.value(size[k]) for k in "xyz")
        # A negative size grows from the position the other way, as Litematica draws it.
        x0, x1 = (px, px + sx - 1) if sx > 0 else (px + sx + 1, px)
        y0, y1 = (py, py + sy - 1) if sy > 0 else (py + sy + 1, py)
        z0, z1 = (pz, pz + sz - 1) if sz > 0 else (pz + sz + 1, pz)
        boxes.append((region, (x0, y0, z0), (abs(sx), abs(sy), abs(sz))))
    lo = [min(box[1][axis] for box in boxes) for axis in range(3)]
    hi = [max(box[1][axis] + box[2][axis] - 1 for box in boxes) for axis in range(3)]
    width, height, length = (hi[axis] - lo[axis] + 1 for axis in range(3))
    palette = [AIR]
    keys = {AIR: 0}
    cells = array.array("I", bytes(4 * width * height * length))
    block_entities, entities = [], []
    for region, origin, (sx, sy, sz) in boxes:
        local = [state_of(entry["Name"], entry.get("Properties")) for entry in region["BlockStatePalette"].items]
        bits = max(2, (len(local) - 1).bit_length())
        longs = [word & 0xFFFFFFFFFFFFFFFF for word in nbt.value(region["BlockStates"])] + [0]
        mask = (1 << bits) - 1
        ox, oy, oz = (origin[axis] - lo[axis] for axis in range(3))
        for i in range(sx * sy * sz):
            # Litematica packs entries tightly: one may start in a long and end in the next.
            start = i * bits
            word, offset = start >> 6, start & 63
            entry = (longs[word] >> offset) | (longs[word + 1] << (64 - offset)) if offset + bits > 64 else longs[word] >> offset
            state = local[entry & mask]
            if state in EMPTY:
                continue
            # Litematica runs x fastest, then z, then y, like Sponge.
            x = i % sx
            z = (i // sx) % sz
            y = i // (sx * sz)
            if state not in keys:
                keys[state] = len(palette)
                palette.append(state)
            cells[(ox + x) + (oz + z) * width + (oy + y) * width * length] = keys[state]
        for entry in region.get("TileEntities", List(COMPOUND)).items:
            pos = [nbt.value(entry["x"]) + ox, nbt.value(entry["y"]) + oy, nbt.value(entry["z"]) + oz]
            block_entities.append({"pos": pos, "id": entry.get("id", ""), "data": {k: v for k, v in entry.items() if k not in ("x", "y", "z")}})
        for entry in region.get("Entities", List(COMPOUND)).items:
            pos = [nbt.value(p) for p in entry["Pos"].items]
            entities.append({"pos": [pos[0] + ox, pos[1] + oy, pos[2] + oz], "id": entry.get("id", ""), "data": {k: v for k, v in entry.items() if k != "Pos"}})
    meta = root.get("Metadata", {})
    return Structure(width, height, length, palette, cells, nbt.value(root.get("MinecraftDataVersion", 0)), block_entities, entities,
                     str(meta.get("Name", "")), str(meta.get("Author", "")))


def read(path: str) -> Structure:
    """A `.schem` or `.litematic` as a [Structure]."""
    _, root = nbt.load(path)
    if path.endswith(".litematic"):
        return _litematic(root)
    if path.endswith(".schem"):
        return _sponge(root)
    raise ValueError(f"{path}: only .schem and .litematic are taken")


def compact(structure: Structure) -> Structure:
    """The same build in the smallest box that holds every block, with only the states it uses, air first."""
    xs, ys, zs = [], [], []
    for y in range(structure.height):
        for z in range(structure.length):
            for x in range(structure.width):
                if structure.at(x, y, z) not in EMPTY:
                    xs.append(x)
                    ys.append(y)
                    zs.append(z)
    if not xs:
        raise ValueError("the schematic holds no blocks")
    lo = (min(xs), min(ys), min(zs))
    size = (max(xs) - lo[0] + 1, max(ys) - lo[1] + 1, max(zs) - lo[2] + 1)
    palette, keys = [AIR], {AIR: 0}
    cells = array.array("I", bytes(4 * size[0] * size[1] * size[2]))
    for y in range(size[1]):
        for z in range(size[2]):
            for x in range(size[0]):
                state = structure.at(x + lo[0], y + lo[1], z + lo[2])
                if state in EMPTY:
                    continue
                if state not in keys:
                    keys[state] = len(palette)
                    palette.append(state)
                cells[x + z * size[0] + y * size[0] * size[2]] = keys[state]
    inside = lambda pos: all(0 <= pos[axis] - lo[axis] < size[axis] for axis in range(3))
    shift = lambda pos: [pos[axis] - lo[axis] for axis in range(3)]
    return Structure(size[0], size[1], size[2], palette, cells, structure.data_version,
                     [dict(e, pos=shift(e["pos"])) for e in structure.block_entities if inside([int(p) for p in e["pos"]])],
                     [dict(e, pos=shift(e["pos"])) for e in structure.entities if inside([int(p // 1) for p in e["pos"]])],
                     structure.name, structure.author)


def dumps_v3(structure: Structure) -> bytes:
    """[structure] as a Sponge v3 `.schem`, as WorldEdit 7.3 writes one."""
    if max(structure.width, structure.height, structure.length) > 0xFFFF:
        raise ValueError("a side past 65535 blocks")
    schematic = {
        "Version": 3,
        "DataVersion": structure.data_version,
        "Width": Tag(SHORT, _short(structure.width)),
        "Height": Tag(SHORT, _short(structure.height)),
        "Length": Tag(SHORT, _short(structure.length)),
        "Offset": Tag(INT_ARRAY, [0, 0, 0]),
        "Metadata": {"Name": structure.name, "Author": structure.author},
        "Blocks": {
            "Palette": {state: index for index, state in enumerate(structure.palette)},
            "Data": Tag(BYTE_ARRAY, _encode_varints(structure.blocks)),
            "BlockEntities": List(COMPOUND, [
                {"Pos": Tag(INT_ARRAY, [int(p) for p in e["pos"]]), "Id": e["id"], "Data": e["data"]} for e in structure.block_entities
            ]),
        },
        "Entities": List(COMPOUND, [
            {"Pos": List(DOUBLE, [float(p) for p in e["pos"]]), "Id": e["id"], "Data": e["data"]} for e in structure.entities
        ]),
    }
    return nbt.dumps({"Schematic": schematic})


def _short(n: int) -> int:
    """A size as Sponge's signed short: sides past 32767 wrap, and readers take them back unsigned."""
    return n - 0x10000 if n > 0x7FFF else n


def counts(structure: Structure) -> dict:
    """How many of each block, without states, air left out."""
    per_state = {}
    for index in structure.blocks:
        per_state[index] = per_state.get(index, 0) + 1
    out = {}
    for index, n in per_state.items():
        state = structure.palette[index]
        if state in EMPTY:
            continue
        key = block_id(state)
        out[key] = out.get(key, 0) + n
    return out
