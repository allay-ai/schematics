"""Builds the catalog: each schematic as `build.schem` (Sponge v3, its box cut to its blocks) and `index.json`.

Run from the repository's root: `python3 -I tools/build.py`. `--check` changes nothing and fails when a file would
change, for pull requests. Each entry is a folder under `catalog/` with `meta.json` and one `source.schem` or
`source.litematic`; its id is its path there, like `houses/medieval-mill`.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import schematic  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CATALOG = os.path.join(ROOT, "catalog")
INDEX = os.path.join(ROOT, "index.json")

# Only what lets anyone use and share a build, with its author named: the plugin hands it to every server, paid
# ones too, so non-commercial licenses stay out unless the author allowed Allay (`permission`).
LICENSES = {"CC0-1.0", "CC-BY-4.0", "CC-BY-SA-4.0", "MIT", "permission"}
LANGUAGES = ("en", "ru", "es", "pt", "de")
ID = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*(/[a-z0-9]+(-[a-z0-9]+)*)+$")
# The biggest build the catalog takes; a server's own limits are lower.
MAX_BLOCKS = 2_000_000
MAX_SIDE = 1024
MATERIALS = 24


class Problem(Exception):
    pass


def entries():
    """Every entry folder, by id, in a stable order."""
    for folder, _, files in sorted(os.walk(CATALOG)):
        if "meta.json" in files:
            yield os.path.relpath(folder, CATALOG).replace(os.sep, "/"), folder


def source_of(folder: str) -> str:
    found = [name for name in ("source.schem", "source.litematic") if os.path.exists(os.path.join(folder, name))]
    if len(found) != 1:
        raise Problem("needs exactly one source.schem or source.litematic")
    return os.path.join(folder, found[0])


def check_meta(meta: dict) -> None:
    for key in ("author", "source", "license"):
        if not isinstance(meta.get(key), str) or not meta[key].strip():
            raise Problem(f"meta.json needs {key}")
    if meta["license"] not in LICENSES:
        raise Problem(f"license {meta['license']} isn't one the catalog takes: {', '.join(sorted(LICENSES))}")
    if meta["license"] == "permission" and not str(meta.get("permission", "")).strip():
        raise Problem("license permission needs permission: where the author allowed it")
    if not str(meta["source"]).startswith("https://"):
        raise Problem("source is the https page the build comes from")


def silhouette(structure: schematic.Structure, width: int = 48) -> dict:
    """The build from the front and from above as characters, for the describer: `#` a block, `.` air."""
    step = max(1, max(structure.width, structure.length, structure.height) // width)
    front, top = [], []
    for y in range(structure.height - 1, -1, -step):
        front.append("".join("#" if any(structure.at(x, y, z) not in schematic.EMPTY for z in range(structure.length)) else "."
                             for x in range(0, structure.width, step)))
    for z in range(0, structure.length, step):
        row = ""
        for x in range(0, structure.width, step):
            heights = [y for y in range(structure.height) if structure.at(x, y, z) not in schematic.EMPTY]
            row += "." if not heights else "0123456789"[min(9, max(heights) * 10 // max(1, structure.height))]
        top.append(row)
    return {"front": front, "top": top}


def build(entry_id: str, folder: str, check: bool) -> tuple[dict, bool]:
    """The entry's index record, and whether its build.schem changed."""
    if not ID.match(entry_id):
        raise Problem("the folder's path must be lowercase words joined by - under a section, like houses/medieval-mill")
    with open(os.path.join(folder, "meta.json"), encoding="utf-8") as file:
        meta = json.load(file)
    check_meta(meta)
    structure = schematic.compact(schematic.read(source_of(folder)))
    blocks = sum(schematic.counts(structure).values())
    if blocks > MAX_BLOCKS or max(structure.width, structure.height, structure.length) > MAX_SIDE:
        raise Problem(f"{blocks} blocks in {structure.width}×{structure.height}×{structure.length}; the catalog takes {MAX_BLOCKS} and {MAX_SIDE} a side at most")
    if not structure.author:
        structure.author = meta["author"]
    data = schematic.dumps_v3(structure)
    target = os.path.join(folder, "build.schem")
    before = open(target, "rb").read() if os.path.exists(target) else None
    changed = before != data
    if changed and not check:
        with open(target, "wb") as file:
            file.write(data)
    counts = sorted(schematic.counts(structure).items(), key=lambda item: (-item[1], item[0]))
    generated = meta.get("generated", {})
    record = {
        "id": entry_id,
        "path": f"catalog/{entry_id}/build.schem",
        "sha256": hashlib.sha256(data).hexdigest(),
        "bytes": len(data),
        "size": {"x": structure.width, "y": structure.height, "z": structure.length},
        "blocks": blocks,
        "kinds": len(counts),
        "materials": [{"block": block, "count": n} for block, n in counts[:MATERIALS]],
        "data_version": structure.data_version,
        "block_entities": len(structure.block_entities),
        "entities": len(structure.entities),
        "author": meta["author"],
        "source": meta["source"],
        "license": meta["license"],
        "title": {lang: generated.get("title", {}).get(lang) or (meta.get("title") if lang == "en" else None) for lang in LANGUAGES},
        "description": generated.get("description") or meta.get("description") or "",
        "tags": generated.get("tags", {}),
    }
    record["title"] = {lang: title for lang, title in record["title"].items() if title}
    return record, changed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="fail when a file would change, change nothing")
    args = parser.parse_args()
    records, problems, changed = [], [], []
    ids = set()
    for entry_id, folder in entries():
        try:
            record, moved = build(entry_id, folder, args.check)
        except (Problem, ValueError, KeyError) as error:
            problems.append(f"{entry_id}: {error}")
            continue
        if entry_id in ids:
            problems.append(f"{entry_id}: twice")
        ids.add(entry_id)
        records.append(record)
        if moved:
            changed.append(f"catalog/{entry_id}/build.schem")
    for problem in problems:
        print(f"✖ {problem}", file=sys.stderr)
    if problems:
        return 1
    index = json.dumps({"version": 1, "schematics": records}, ensure_ascii=False, indent=1, sort_keys=False) + "\n"
    old = open(INDEX, encoding="utf-8").read() if os.path.exists(INDEX) else None
    if old != index:
        changed.append("index.json")
        if not args.check:
            with open(INDEX, "w", encoding="utf-8") as file:
                file.write(index)
    print(f"{len(records)} schematics; {'would change' if args.check else 'changed'}: {', '.join(changed) or 'nothing'}")
    return 1 if args.check and changed else 0


if __name__ == "__main__":
    sys.exit(main())
