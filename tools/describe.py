"""Describes the catalog's schematics for search: titles in Allay's languages, a description, and tags, by a model.

Run from the repository's root with `OPENROUTER_API_KEY` set: `python3 -I tools/describe.py`. Entries whose build has
no description yet, or changed since, get one. Up to `--now` of them (20) go as plain requests and are written at once;
more go to OpenRouter's Batch API at half the price, and a later run with `--collect` writes what came back. The
pending batch's id waits in `.describe/pending.json`. What the model writes goes into each `meta.json` under
`generated`, which a person may correct by hand (`"by": "hand"` keeps it for good). Nothing is described twice: a
description stays while the build's size and materials do.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import build  # noqa: E402
import schematic  # noqa: E402

API = "https://openrouter.ai/api/v1"
MODEL = os.environ.get("DESCRIBE_MODEL", "anthropic/claude-haiku-5.5")
PENDING = os.path.join(build.ROOT, ".describe", "pending.json")
TYPES = ["house", "castle", "tower", "farm", "bridge", "ship", "temple", "shop", "village", "wall", "gate", "statue", "garden", "fountain", "redstone", "interior", "decoration", "other"]
STYLES = ["medieval", "fantasy", "modern", "rustic", "japanese", "chinese", "nordic", "desert", "steampunk", "gothic", "classical", "futuristic", "other"]

PROMPT = """You describe a Minecraft build for a search index. Players ask an assistant for builds in their own words, in any language; the assistant searches this index in English. Write what helps it find this build and tell it apart.

Reply with JSON only:
{"title": {"en": "...", "ru": "...", "es": "...", "pt": "...", "de": "..."},
 "description": "2–3 sentences in English: what it is, its look and materials, its size in plain words, what's inside if anything",
 "tags": {"type": ["one or two of TYPES"], "style": ["one or two of STYLES"], "biome": ["where it fits, like plains, desert, snowy, forest, jungle, ocean, nether"], "keywords": ["up to 8 English words a player might search with"]}}

Titles: 2–5 words, natural in each language, like a shop name for the build ("Medieval Windmill", «Средневековая мельница»). Never invent what the data doesn't show; when the source's own words say what it is, trust them.

TYPES: {types}
STYLES: {styles}"""


def fingerprint(structure: schematic.Structure) -> str:
    """What a description rests on: the build's size and what it's made of. Writing the same build again in another
    order, or a fix to the tools, keeps it, so nobody pays to describe the same build twice."""
    counts = sorted(schematic.counts(structure).items())
    text = f"{structure.width}x{structure.height}x{structure.length};" + ";".join(f"{block}={n}" for block, n in counts)
    return hashlib.sha256(text.encode()).hexdigest()


_WORDS = {"type": "array", "items": {"type": "string"}}
SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["title", "description", "tags"],
    "properties": {
        "title": {"type": "object", "additionalProperties": False, "required": list(build.LANGUAGES), "properties": {lang: {"type": "string"} for lang in build.LANGUAGES}},
        "description": {"type": "string"},
        "tags": {"type": "object", "additionalProperties": False, "required": ["type", "style", "biome", "keywords"], "properties": {"type": _WORDS, "style": _WORDS, "biome": _WORDS, "keywords": _WORDS}},
    },
}


def todo():
    """Entries whose build has no description yet, or whose build changed since its description was written."""
    for entry_id, folder in build.entries():
        path = os.path.join(folder, "meta.json")
        with open(path, encoding="utf-8") as file:
            meta = json.load(file)
        target = os.path.join(folder, "build.schem")
        if not os.path.exists(target):
            continue
        generated = meta.get("generated", {})
        # A description someone wrote by hand, or one already paid for, stays.
        if generated.get("by") == "hand":
            continue
        digest = fingerprint(schematic.read(target))
        if generated.get("for") == digest:
            continue
        yield entry_id, folder, meta, digest


def request(entry_id: str, folder: str, meta: dict, texts: dict | None = None) -> dict:
    structure = schematic.read(os.path.join(folder, "build.schem"))
    counts = sorted(schematic.counts(structure).items(), key=lambda item: -item[1])
    facts = {
        "id": entry_id,
        "source_title": meta.get("title"),
        "source_description": ((texts or {}).get(entry_id) or meta.get("description") or "")[:1500],
        "author": meta["author"],
        "size": f"{structure.width} wide × {structure.height} high × {structure.length} deep",
        "blocks": sum(n for _, n in counts),
        "materials": [f"{n} × {block.removeprefix('minecraft:')}" for block, n in counts[:20]],
        "block_entities": sorted({e["id"] for e in structure.block_entities})[:20],
        "seen_from_the_front (# block)": build.silhouette(structure)["front"],
        "seen_from_above (digit = height)": build.silhouette(structure)["top"],
    }
    return {
        "model": MODEL,
        # Haiku thinks before it answers, out of the same budget: room for both, and little thinking for a plain task.
        "max_tokens": 4000,
        "reasoning": {"effort": "low"},
        # A schema the model must answer in, so the answer always reads.
        "response_format": {"type": "json_schema", "json_schema": {"name": "description", "strict": True, "schema": SCHEMA}},
        "messages": [
            {"role": "system", "content": PROMPT.replace("{types}", ", ".join(TYPES)).replace("{styles}", ", ".join(STYLES))},
            {"role": "user", "content": json.dumps(facts, ensure_ascii=False)},
        ],
    }


def call(method: str, path: str, body: dict | None = None) -> dict:
    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        raise SystemExit("OPENROUTER_API_KEY isn't set")
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(API + path, data=data, method=method, headers={
        "Authorization": f"Bearer {key}", "Content-Type": "application/json", "X-Title": "Allay schematics",
    })
    try:
        with urllib.request.urlopen(req, timeout=120) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        raise SystemExit(f"OpenRouter answered {error.code}: {error.read()[:500].decode(errors='replace')}")


def parse(answer: str) -> dict:
    """The model's JSON, held to the shape the index needs; anything else is dropped."""
    if not answer:
        raise ValueError("an empty answer")
    text = answer.strip()
    if text.startswith("```"):
        text = text.strip("`").split("\n", 1)[-1]
    data = json.loads(text[text.find("{"): text.rfind("}") + 1])
    titles = {lang: str(data.get("title", {}).get(lang, "")).strip()[:60] for lang in build.LANGUAGES}
    if not titles["en"]:
        raise ValueError("no English title")
    tags = data.get("tags", {})
    pick = lambda name, allowed=None, limit=4: [str(t).strip().lower()[:32] for t in tags.get(name, []) if str(t).strip() and (allowed is None or str(t).strip().lower() in allowed)][:limit]
    return {
        "title": {lang: title for lang, title in titles.items() if title},
        "description": str(data.get("description", "")).strip()[:600],
        "tags": {"type": pick("type", TYPES, 2), "style": pick("style", STYLES, 2), "biome": pick("biome"), "keywords": pick("keywords", None, 8)},
    }


def save(folder: str, meta: dict, digest: str, generated: dict) -> None:
    meta["generated"] = {**generated, "for": digest, "model": MODEL}
    with open(os.path.join(folder, "meta.json"), "w", encoding="utf-8") as file:
        json.dump(meta, file, ensure_ascii=False, indent=2)
        file.write("\n")


def now(entries, texts=None) -> int:
    written = 0
    for entry_id, folder, meta, digest in entries:
        # A model may still slip once; the second try is a new answer.
        for attempt in range(2):
            answer = call("POST", "/chat/completions", request(entry_id, folder, meta, texts))
            try:
                save(folder, meta, digest, parse(answer["choices"][0]["message"]["content"]))
                written += 1
                print(f"✔ {entry_id}")
                break
            except (ValueError, KeyError, IndexError) as error:
                if attempt:
                    print(f"✖ {entry_id}: {error}", file=sys.stderr)
    return written


def submit(entries, texts=None) -> None:
    # A batch's ids are short and plain (64 of letters, digits, - and _), so each goes by its place in the list.
    entries = list(entries)
    requests = [{"custom_id": f"b{n}", "body": request(entry_id, folder, meta, texts)} for n, (entry_id, folder, meta, _) in enumerate(entries)]
    # requests goes last: OpenRouter reads the body as a stream.
    batch = call("POST", "/batches", {"endpoint": "/v1/chat/completions", "model": MODEL, "requests": requests})
    os.makedirs(os.path.dirname(PENDING), exist_ok=True)
    with open(PENDING, "w", encoding="utf-8") as file:
        json.dump({"id": batch["id"], "entries": [entry_id for entry_id, _, _, _ in entries]}, file, indent=2)
        file.write("\n")
    print(f"submitted batch {batch['id']} with {len(requests)} schematics")


def collect() -> int:
    if not os.path.exists(PENDING):
        print("no batch waiting")
        return 0
    pending = json.load(open(PENDING, encoding="utf-8"))
    batch = call("GET", f"/batches/{pending['id']}")
    status = batch.get("status")
    if status not in ("completed", "failed", "expired", "cancelled"):
        print(f"batch {pending['id']} is {status}")
        return 0
    found = {entry_id: (folder, meta, digest) for entry_id, folder, meta, digest in todo()}
    written = 0
    for result in batch.get("results", []):
        at = str(result.get("custom_id", ""))[1:]
        entry = found.get(pending["entries"][int(at)]) if at.isdigit() and int(at) < len(pending["entries"]) else None
        body = (result.get("response") or {}).get("body") or {}
        if not entry or result.get("error"):
            continue
        try:
            save(entry[0], entry[1], entry[2], parse(body["choices"][0]["message"]["content"]))
            written += 1
        except (ValueError, KeyError, IndexError) as error:
            print(f"✖ {result.get('custom_id')}: {error}", file=sys.stderr)
    os.remove(PENDING)
    print(f"batch {pending['id']} {status}: {written} described")
    return written


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--collect", action="store_true", help="write what a pending batch brought, then stop")
    parser.add_argument("--now", type=int, default=20, help="up to this many go as plain requests; more go as a batch")
    parser.add_argument("--max", type=int, help="describe at most this many as plain requests, the best rated first, and leave the rest for the next run")
    parser.add_argument("--texts", help="a JSON file of what each build's source page says, by id, read but never kept in the catalog")
    args = parser.parse_args()
    if args.collect:
        collect()
        return 0
    if os.path.exists(PENDING):
        print("a batch is still pending: run with --collect")
        return 0
    entries = list(todo())
    texts = json.load(open(args.texts, encoding="utf-8")) if args.texts else None
    if not entries:
        print("every schematic has its description")
    elif args.max is not None:
        # The builds players like most get found first.
        entries.sort(key=lambda entry: -(entry[2].get("rating") or 0))
        now(entries[: args.max], texts)
        print(f"{max(0, len(entries) - args.max)} left for the next run")
    elif len(entries) <= args.now:
        now(entries, texts)
    else:
        submit(entries, texts)
    return 0


if __name__ == "__main__":
    sys.exit(main())
