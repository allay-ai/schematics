"""Imports builds from rom1504's dataset of Planet Minecraft schematics into the catalog.

    python3 -I tools/import_rom1504.py DATASET.json --limit 300 --texts TEXTS.json

DATASET.json is `schematicsWithFinalUrl.json` from https://gitlab.com/rom1504/minecraft-schematics-dataset. Builds go
best first (the site's diamonds, then downloads), structures only (no pixel art, redstone, or music), and never one
whose page asks not to share it. Each lands as `catalog/<section>/<name>/build.schem` with `meta.json` naming its author
and page. What its page says goes to TEXTS.json only, for describe.py to read: the catalog writes its own words.
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
SECTIONS = [
    ("castles", r"castle|keep|fortress|citadel|fort\b"),
    ("ships", r"ship|boat|galleon|vessel|submarine|airship|frigate|yacht"),
    ("towers", r"tower|lighthouse|spire"),
    ("temples", r"temple|church|cathedral|mosque|shrine|chapel|pagoda"),
    ("bridges", r"bridge"),
    ("statues", r"statue|sculpture"),
    ("towns", r"\bcity\b|town|village|spawn|hub|market"),
    ("houses", r"house|mansion|cottage|villa|home|cabin|hut|manor|bungalow|apartment"),
]
AGENT = "AllayCatalog/1.0 (+https://github.com/allay-ai/schematics)"
MAX_BYTES = 30 * 1024 * 1024
# A box this big takes too long to read here, and no server builds it anyway.
MAX_BOX = 16_000_000


def section(entry: dict) -> str:
    text = " ".join([entry.get("title", ""), *entry.get("tags", [])]).lower()
    return next((name for name, pattern in SECTIONS if re.search(pattern, text)), "structures")


def slug(url: str) -> str | None:
    found = re.search(r"/project/([a-z0-9-]+)/?$", url)
    return re.sub(r"-+", "-", found.group(1)).strip("-")[:60].strip("-") if found else None


def known_sources() -> set:
    seen = set()
    for _, folder in build.entries():
        with open(os.path.join(folder, "meta.json"), encoding="utf-8") as file:
            seen.add(json.load(file).get("source"))
    return seen


def download(url: str) -> bytes:
    """The file, waiting as long as the site asks when it says too many requests, three times at most."""
    request = urllib.request.Request(url.replace(" ", "%20"), headers={"User-Agent": AGENT})
    for attempt in range(4):
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                data = response.read(MAX_BYTES + 1)
            break
        except urllib.error.HTTPError as error:
            if error.code != 429 or attempt == 3:
                raise
            wait = int(error.headers.get("Retry-After") or 30) + 1
            print(f"… the site asks to wait {wait} s", file=sys.stderr)
            time.sleep(wait)
    if len(data) > MAX_BYTES:
        raise ValueError("bigger than 30 MB")
    return data


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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("dataset")
    parser.add_argument("--limit", type=int, default=100, help="how many builds to add")
    parser.add_argument("--texts", required=True, help="where what the pages say goes, for describe.py")
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
    added = skipped = 0
    for entry in candidates:
        if added >= args.limit:
            break
        url, name = entry.get("url", ""), slug(entry.get("url", ""))
        if not name or url in seen:
            continue
        if FORBIDS.search(entry.get("description") or "") or FORBIDS.search(entry.get("title") or ""):
            skipped += 1
            continue
        entry_id = f"{section(entry)}/{name}"
        folder = os.path.join(build.CATALOG, *entry_id.split("/"))
        if os.path.exists(folder):
            continue
        link = entry["finalDownloadLink"]
        try:
            structure = convert(download(link), link.lower().rsplit(".", 1)[-1])
            blocks = sum(schematic.counts(structure).values())
            if blocks < 50:
                raise ValueError("fewer than 50 blocks")
            if blocks > build.MAX_BLOCKS or max(structure.width, structure.height, structure.length) > build.MAX_SIDE:
                raise ValueError("too big for the catalog")
        except Exception as error:  # noqa: BLE001 — one bad file never stops the import
            print(f"✖ {entry_id}: {error}", file=sys.stderr)
            skipped += 1
            time.sleep(args.pause)
            continue
        structure.author = entry.get("user", "")
        structure.name = entry.get("title", "")[:100]
        os.makedirs(folder)
        with open(os.path.join(folder, "build.schem"), "wb") as file:
            file.write(schematic.dumps_v3(structure))
        meta = {
            "title": entry.get("title", "")[:100],
            "author": entry.get("user", ""),
            "source": url,
            "license": "unrestricted",
            "rating": int(entry.get("diamondCount") or 0),
            "imported": "rom1504/minecraft-schematics-dataset",
        }
        with open(os.path.join(folder, "meta.json"), "w", encoding="utf-8") as file:
            json.dump(meta, file, ensure_ascii=False, indent=2)
            file.write("\n")
        texts[entry_id] = (entry.get("description") or "")[:3000]
        seen.add(url)
        added += 1
        print(f"✔ {entry_id}: {structure.width}×{structure.height}×{structure.length}, {blocks} blocks")
        time.sleep(args.pause)
    with open(args.texts, "w", encoding="utf-8") as file:
        json.dump(texts, file, ensure_ascii=False)
    print(f"added {added}, skipped {skipped}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
