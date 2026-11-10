"""Imports builds from rom1504's dataset of Planet Minecraft schematics into the catalog.

    python3 -I tools/import_rom1504.py DATASET.json --limit 300 --texts TEXTS.json

DATASET.json is `schematicsWithFinalUrl.json` from https://gitlab.com/rom1504/minecraft-schematics-dataset, whose
`schematics/schematics_K000.tfrecords` hold the files themselves, entries K000 to K999 of that list. Builds go best
first (the site's diamonds, then downloads), structures only (no pixel art, redstone, or music), and never one whose
page asks not to share it. Only the archives that hold them are read, as a stream: nothing reaches Planet Minecraft,
and nothing but the builds stays on disk. Each lands as `catalog/planetminecraft/<name>/build.schem` with `meta.json` naming
its author and page. What its page says goes to TEXTS.json only, for describe.py to read: the catalog writes its own words.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import build  # noqa: E402
import nbt  # noqa: E402
import schematic  # noqa: E402

KINDS = {
    "Land Structure Map", "Air Structure Map", "Water Structure Map", "Complex Map", "Underground Structure Map",
    "Nether Structure Map", "Environment / Landscaping Map",
}
# The author asked not to share it: their word stands, whatever the site allows.
FORBIDS = re.compile(
    r"(do not|don'?t|not allowed to|no|never|please don'?t)\s+(re-?upload|redistribut|re-?post|re-?share|share|claim|sell|steal|copy)"
    r"|all rights reserved|without (my |the author'?s? )?(permission|consent)|not for (redistribution|commercial)|©|copyright",
    re.I,
)
AGENT = "AllayCatalog/1.0 (+https://github.com/allay-ai/schematics)"
# A box this big takes too long to read here, and no server builds it anyway.
MAX_BOX = 16_000_000


def slug(url: str) -> str | None:
    found = re.search(r"/project/([a-z0-9-]+)/?$", url)
    return re.sub(r"-+", "-", found.group(1)).strip("-")[:60].strip("-") if found else None


def known_sources() -> set:
    seen = set()
    for _, folder in build.entries():
        with open(os.path.join(folder, "meta.json"), encoding="utf-8") as file:
            seen.add(json.load(file).get("source"))
    return seen


ARCHIVE = "https://gitlab.com/rom1504/minecraft-schematics-dataset/-/raw/master/schematics/schematics_{}.tfrecords"


def _varint(data: bytes, at: int) -> tuple[int, int]:
    value = shift = 0
    while True:
        byte = data[at]
        at += 1
        value |= (byte & 0x7F) << shift
        if byte < 0x80:
            return value, at
        shift += 7


def _fields(data: bytes):
    """A protobuf message's fields as (number, bytes or int), the few kinds tf.train.Example uses."""
    at = 0
    while at < len(data):
        key, at = _varint(data, at)
        number, kind = key >> 3, key & 7
        if kind == 0:
            value, at = _varint(data, at)
        elif kind == 2:
            size, at = _varint(data, at)
            value, at = data[at:at + size], at + size
        elif kind == 5:
            value, at = data[at:at + 4], at + 4
        elif kind == 1:
            value, at = data[at:at + 8], at + 8
        else:
            raise ValueError(f"protobuf wire type {kind}")
        yield number, value


def _example(data: bytes) -> dict:
    """A tf.train.Example's bytes features, by name."""
    out = {}
    for number, features in _fields(data):
        if number != 1:
            continue
        for entry_number, entry in _fields(features):
            if entry_number != 1:
                continue
            name, feature = b"", b""
            for field, value in _fields(entry):
                if field == 1:
                    name = value
                elif field == 2:
                    feature = value
            for kind, listed in _fields(feature):
                if kind == 1:
                    out[name.decode()] = [value for field, value in _fields(listed) if field == 1]
    return out


def archive(index: int):
    """The (page, file) pairs of one archive, read as it downloads."""
    name = "0" if index == 0 else f"{index}000"
    request = urllib.request.Request(ARCHIVE.format(name), headers={"User-Agent": AGENT})
    with urllib.request.urlopen(request, timeout=120) as stream:
        while True:
            header = stream.read(12)
            if len(header) < 12:
                return
            size = int.from_bytes(header[:8], "little")
            record = stream.read(size)
            stream.read(4)
            example = _example(record)
            url = (example.get("url") or [b""])[0].decode("utf-8", "replace")
            data = (example.get("schematicData") or [b""])[0]
            yield url, data


def convert(data: bytes, extension: str) -> schematic.Structure:
    with tempfile.NamedTemporaryFile(suffix="." + extension, delete=False) as file:
        file.write(data)
        path = file.name
    try:
        root = nbt.loads(data)[1]
        box = root.get("Schematic", root)
        if all(key in box for key in ("Width", "Height", "Length")):
            volume = 1
            for key in ("Width", "Height", "Length"):
                volume *= nbt.value(box[key]) & 0xFFFF
            if volume > MAX_BOX:
                raise ValueError(f"a box of {volume} cells")
        if extension == "schematic":
            import legacy
            structure, unknown = legacy.read(root)
            placed = sum(schematic.counts(structure).values())
            if unknown > max(10, placed * 0.05):
                raise ValueError(f"{unknown} blocks of ids nobody knows")
            return schematic.compact(structure)
        return schematic.compact(schematic.read(path))
    finally:
        os.remove(path)


def write(entry_id: str, entry: dict, structure: schematic.Structure, texts: dict) -> None:
    folder = os.path.join(build.CATALOG, *entry_id.split("/"))
    structure.author = entry.get("user", "")
    structure.name = entry.get("title", "")[:100]
    os.makedirs(folder)
    with open(os.path.join(folder, "build.schem"), "wb") as file:
        file.write(schematic.dumps_v3(structure))
    meta = {
        "title": entry.get("title", "")[:100],
        "author": entry.get("user", ""),
        "source": entry["url"],
        "license": "unrestricted",
        "rating": int(entry.get("diamondCount") or 0),
        "imported": "rom1504/minecraft-schematics-dataset",
    }
    with open(os.path.join(folder, "meta.json"), "w", encoding="utf-8") as file:
        json.dump(meta, file, ensure_ascii=False, indent=2)
        file.write("\n")
    texts[entry_id] = (entry.get("description") or "")[:3000]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("dataset")
    parser.add_argument("--limit", type=int, default=100, help="how many builds to add")
    parser.add_argument("--texts", required=True, help="where what the pages say goes, for describe.py")
    parser.add_argument("--max-blocks", type=int, default=200_000, help="the biggest build to take; a server's own limits are lower")
    parser.add_argument("--pause", type=float, default=4.0, help="seconds between downloads, to go easy on the site")
    args = parser.parse_args()
    with open(args.dataset, encoding="utf-8") as file:
        dataset = json.load(file)
    texts = json.load(open(args.texts, encoding="utf-8")) if os.path.exists(args.texts) else {}
    seen = known_sources()
    candidates = sorted(
        (e for e in dataset if e.get("subtitle") in KINDS and (e.get("finalDownloadLink") or "").lower().rsplit(".", 1)[-1] in ("schematic", "schem")),
        key=lambda e: (-(e.get("diamondCount") or 0), -(e.get("downloads") or 0)),
    )
    position = {id(entry): index for index, entry in enumerate(dataset)}
    chosen = []
    skipped = 0
    for entry in candidates:
        url, name = entry.get("url", ""), slug(entry.get("url", ""))
        if not name or url in seen:
            continue
        if FORBIDS.search(entry.get("description") or "") or FORBIDS.search(entry.get("title") or ""):
            skipped += 1
            continue
        entry_id = f"planetminecraft/{name}"
        if os.path.exists(os.path.join(build.CATALOG, *entry_id.split("/"))):
            continue
        chosen.append((entry_id, entry))
        # A few spare, for files that turn out broken or too big.
        # Spare ones too: the best rated are often far bigger than anyone builds.
        if len(chosen) >= args.limit * 3 + 10:
            break
    wanted = {}
    for entry_id, entry in chosen:
        wanted.setdefault(position[id(entry)] // 1000, {})[entry["url"]] = (entry_id, entry)
    added = 0
    for index in sorted(wanted):
        if added >= args.limit:
            break
        here = wanted[index]
        print(f"… archive {index}: {len(here)} builds", file=sys.stderr)
        for url, data in archive(index):
            if url not in here or added >= args.limit:
                continue
            entry_id, entry = here.pop(url)
            extension = entry["finalDownloadLink"].lower().rsplit(".", 1)[-1]
            try:
                structure = convert(data, extension)
                blocks = sum(schematic.counts(structure).values())
                if blocks < 50:
                    raise ValueError("fewer than 50 blocks")
                if blocks > args.max_blocks or max(structure.width, structure.height, structure.length) > build.MAX_SIDE:
                    raise ValueError(f"{blocks} blocks, more than --max-blocks")
            except Exception as error:  # noqa: BLE001 — one bad file never stops the import
                print(f"✖ {entry_id}: {error}", file=sys.stderr)
                skipped += 1
                continue
            write(entry_id, entry, structure, texts)
            seen.add(url)
            added += 1
            print(f"✔ {entry_id}: {structure.width}×{structure.height}×{structure.length}, {blocks} blocks")
            if not here:
                break
    with open(args.texts, "w", encoding="utf-8") as file:
        json.dump(texts, file, ensure_ascii=False)
    print(f"added {added}, skipped {skipped}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
