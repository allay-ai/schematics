"""The catalog's tools against schematics built here: NBT, Sponge v2 and v3, Litematica, the index, the describer."""

from __future__ import annotations

import array
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))

import build  # noqa: E402
import describe  # noqa: E402
import nbt  # noqa: E402
import schematic  # noqa: E402
from nbt import BYTE_ARRAY, COMPOUND, INT, LONG_ARRAY, List, SHORT, Tag  # noqa: E402


def hut() -> schematic.Structure:
    """A 5×4×5 hut in a 7×6×7 box of air: a stone floor, plank walls with a doorway, glass, a slab roof."""
    w, h, l = 7, 6, 7
    palette = ["minecraft:air", "minecraft:cobblestone", "minecraft:oak_planks", "minecraft:glass_pane[east=true,west=true]", "minecraft:oak_slab[type=bottom]"]
    cells = array.array("I", bytes(4 * w * h * l))
    put = lambda x, y, z, i: cells.__setitem__(x + z * w + y * w * l, i)
    for x in range(1, 6):
        for z in range(1, 6):
            put(x, 1, z, 1)
            put(x, 5, z, 4)
            if x in (1, 5) or z in (1, 5):
                for y in (2, 3, 4):
                    put(x, y, z, 2)
    put(3, 2, 1, 0)
    put(3, 3, 1, 0)
    put(1, 3, 3, 3)
    return schematic.Structure(w, h, l, palette, cells, 4440, [], [], "Hut", "Allay")


class NbtTest(unittest.TestCase):
    def test_a_file_reads_back_as_written(self):
        root = {"Name": "x", "Small": Tag(SHORT, -3), "Bytes": Tag(BYTE_ARRAY, b"\x01\x02"), "Longs": Tag(LONG_ARRAY, [1, -1]), "List": List(INT, [1, 2]), "Empty": List(COMPOUND)}
        name, read = nbt.loads(nbt.dumps(root, "Root"))
        self.assertEqual(name, "Root")
        self.assertEqual(read, root)

    def test_the_same_content_writes_the_same_bytes(self):
        self.assertEqual(nbt.dumps({"a": 1}), nbt.dumps({"a": 1}))


class SchematicTest(unittest.TestCase):
    def write(self, folder: str, name: str, data: bytes) -> str:
        path = os.path.join(folder, name)
        with open(path, "wb") as file:
            file.write(data)
        return path

    def test_sponge_v3_reads_back_and_compacts_to_its_blocks(self):
        with tempfile.TemporaryDirectory() as folder:
            structure = schematic.read(self.write(folder, "hut.schem", schematic.dumps_v3(hut())))
            self.assertEqual((structure.width, structure.height, structure.length), (7, 6, 7))
            small = schematic.compact(structure)
            self.assertEqual((small.width, small.height, small.length), (5, 5, 5))
            self.assertEqual(small.at(0, 0, 0), "minecraft:cobblestone")
            self.assertEqual(small.at(2, 1, 0), "minecraft:air", "the doorway")
            self.assertEqual(small.palette[0], "minecraft:air")
            self.assertEqual(schematic.counts(small)["minecraft:oak_planks"], 16 * 3 - 2 - 1)

    def test_sponge_v2_reads_its_palette_and_block_entities(self):
        palette = {"minecraft:air": 0, "minecraft:chest[facing=north]": 1}
        root = {"Version": 2, "DataVersion": 3465, "Width": Tag(SHORT, 2), "Height": Tag(SHORT, 1), "Length": Tag(SHORT, 1),
                "Palette": palette, "BlockData": Tag(BYTE_ARRAY, bytes([0, 1])),
                "BlockEntities": List(COMPOUND, [{"Pos": Tag(nbt.INT_ARRAY, [1, 0, 0]), "Id": "minecraft:chest", "CustomName": "Loot"}])}
        with tempfile.TemporaryDirectory() as folder:
            structure = schematic.read(self.write(folder, "old.schem", nbt.dumps(root, "Schematic")))
        self.assertEqual(structure.at(1, 0, 0), "minecraft:chest[facing=north]")
        self.assertEqual(structure.block_entities[0]["data"], {"CustomName": "Loot"})

    def test_a_litematic_with_entries_across_longs_reads_as_one_box(self):
        names = ["minecraft:air", "minecraft:stone", "minecraft:dirt", "minecraft:oak_log", "minecraft:glass"]
        sx, sy, sz = 3, 4, 5
        wanted = [(i * 7) % 5 for i in range(sx * sy * sz)]
        bits, packed = 3, 0
        for i, entry in enumerate(wanted):
            packed |= entry << (i * bits)
        longs = []
        for i in range((len(wanted) * bits + 63) // 64):
            word = (packed >> (64 * i)) & 0xFFFFFFFFFFFFFFFF
            longs.append(word - (1 << 64) if word >= 1 << 63 else word)
        region = {
            "Position": {"x": 10, "y": 64, "z": -3}, "Size": {"x": sx, "y": sy, "z": sz},
            "BlockStatePalette": List(COMPOUND, [{"Name": n, **({"Properties": {"axis": "y"}} if n.endswith("log") else {})} for n in names]),
            "BlockStates": Tag(LONG_ARRAY, longs),
        }
        root = {"MinecraftDataVersion": 4440, "Version": 6, "Metadata": {"Name": "Test", "Author": "Someone"}, "Regions": {"main": region}}
        with tempfile.TemporaryDirectory() as folder:
            structure = schematic.read(self.write(folder, "test.litematic", nbt.dumps(root)))
        self.assertEqual((structure.width, structure.height, structure.length), (sx, sy, sz))
        for i, entry in enumerate(wanted):
            x, z, y = i % sx, (i // sx) % sz, i // (sx * sz)
            expected = names[entry] + ("[axis=y]" if names[entry].endswith("log") else "")
            self.assertEqual(structure.at(x, y, z), expected, f"cell {i}")
        self.assertEqual(structure.author, "Someone")


class CatalogTest(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.root = self.folder.name
        self.old = build.CATALOG, build.INDEX
        build.CATALOG = os.path.join(self.root, "catalog")
        build.INDEX = os.path.join(self.root, "index.json")

    def tearDown(self):
        build.CATALOG, build.INDEX = self.old
        self.folder.cleanup()

    def entry(self, entry_id: str, meta: dict) -> str:
        folder = os.path.join(build.CATALOG, *entry_id.split("/"))
        os.makedirs(folder)
        with open(os.path.join(folder, "meta.json"), "w") as file:
            json.dump(meta, file)
        with open(os.path.join(folder, "source.schem"), "wb") as file:
            file.write(schematic.dumps_v3(hut()))
        return folder

    def test_an_entry_becomes_a_build_and_a_record_in_the_index(self):
        self.entry("houses/starter-hut", {"author": "Allay", "source": "https://github.com/allay-ai/schematics", "license": "CC0-1.0", "title": "Starter hut"})
        sys.argv = ["build.py"]
        self.assertEqual(build.main(), 0)
        index = json.load(open(build.INDEX))
        record = index["schematics"][0]
        self.assertEqual(record["id"], "houses/starter-hut")
        self.assertEqual(record["size"], {"x": 5, "y": 5, "z": 5})
        self.assertEqual(record["title"], {"en": "Starter hut"})
        self.assertEqual(record["materials"][0]["block"], "minecraft:oak_planks")
        # Nothing changes on a second run, and --check passes.
        sys.argv = ["build.py", "--check"]
        self.assertEqual(build.main(), 0)

    def test_a_build_without_a_license_that_lets_allay_share_it_is_refused(self):
        self.entry("houses/borrowed", {"author": "Someone", "source": "https://example.com/build", "license": "CC-BY-NC-4.0"})
        sys.argv = ["build.py"]
        self.assertEqual(build.main(), 1)
        self.assertFalse(os.path.exists(build.INDEX))


class DescribeTest(unittest.TestCase):
    def test_the_models_answer_is_held_to_the_index_shape(self):
        answer = '```json\n{"title": {"en": "Starter Hut", "ru": "Домик новичка", "fr": "Cabane"}, "description": "A small plank hut.", "tags": {"type": ["house", "spaceship"], "style": ["rustic"], "keywords": ["hut", "starter"]}}\n```'
        parsed = describe.parse(answer)
        self.assertEqual(parsed["title"], {"en": "Starter Hut", "ru": "Домик новичка"})
        self.assertEqual(parsed["tags"]["type"], ["house"])
        self.assertEqual(parsed["tags"]["keywords"], ["hut", "starter"])

    def test_the_same_build_written_another_way_is_never_described_twice(self):
        first = schematic.compact(hut())
        again = schematic.compact(hut())
        again.palette = [again.palette[0]] + list(reversed(again.palette[1:]))
        flip = {0: 0, **{i: len(first.palette) - i for i in range(1, len(first.palette))}}
        again.blocks = array.array("I", (flip[i] for i in first.blocks))
        self.assertEqual(describe.fingerprint(first), describe.fingerprint(again))
        changed = schematic.compact(hut())
        changed.blocks[0] = changed.palette.index("minecraft:oak_planks")
        self.assertNotEqual(describe.fingerprint(first), describe.fingerprint(changed))

    def test_a_request_carries_the_facts_and_never_the_key(self):
        with tempfile.TemporaryDirectory() as folder:
            with open(os.path.join(folder, "build.schem"), "wb") as file:
                file.write(schematic.dumps_v3(schematic.compact(hut())))
            body = describe.request("houses/starter-hut", folder, {"author": "Allay", "title": "Starter hut"})
        facts = json.loads(body["messages"][1]["content"])
        self.assertEqual(facts["size"], "5 wide × 5 high × 5 deep")
        self.assertIn("oak_planks", facts["materials"][0])
        self.assertNotIn("Authorization", json.dumps(body))


if __name__ == "__main__":
    unittest.main()
