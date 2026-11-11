# Allay schematics

The builds Allay offers players: real schematics, each with its author, its source, and a license that lets every server build it. Allay's backend searches this catalog when a player asks for a build (“a medieval windmill”), and the plugin shows the one they pick as a preview and builds it from their own blocks.

Allay takes schematics from here only. A server can't add its own, since nobody would describe them for the search.

## What's in it

- `catalog/<section>/<name>/` is one build: its id is that path, like `houses/medieval-mill`.
  - `source.schem` (Sponge, WorldEdit 7 or newer) or `source.litematic` (Litematica), as its author published it.
  - `meta.json`: who made it and under what license, written by hand.
  - `build.schem`: what Allay builds, made by CI from the source. A Sponge v3 schematic cut down to the blocks, with air in no corner and none of the author's tools: command blocks, barriers, light, and structure blocks go.
- `index.json` is everything the search reads: each build's size, block count, materials, titles in English, Russian, Spanish, Portuguese, and German, a description, and tags. CI makes it.
- `tools/` holds the Python that makes them, with no dependencies, and `tests/` its tests.

## Where the builds come from

- `planetminecraft/`: builds their authors published for download on [Planet Minecraft](https://www.planetminecraft.com), taken from [rom1504's dataset](https://gitlab.com/rom1504/minecraft-schematics-dataset) of them, best rated first, up to 200 000 blocks, then looked over one by one in the game: what didn't stand as a build to live in or to build in survival went out. A build whose page asks not to share it stays out. Each keeps its author and a link to its page; the catalog writes its own descriptions and copies no page's text or pictures. Their license reads `unrestricted`: the author published it without restricting sharing.
- `samples/`: builds of Allay's own, which keep the tools honest.

An author who wants their build out of the catalog opens an issue, and it goes.

## Adding a build

[CONTRIBUTING.md](CONTRIBUTING.md) says how: you bring the schematic, its author, its source, and its license in a pull request, and CI does the rest after the merge.

The description comes from a model (Claude Haiku through OpenRouter), from the build's size, materials, shape, and the source's own words. It lands in `meta.json` under `generated`. Correct it there by hand if it's wrong; a correction stays until the build changes. Up to 20 new builds are described at once; more go to a batch at half the price, and an hourly run picks up what it brought.

## Running the tools

```sh
python3 -I -m unittest discover -s tests
python3 -I tools/build.py            # build.schem and index.json
python3 -I tools/build.py --check    # changes nothing, fails when something would change
OPENROUTER_API_KEY=… python3 -I tools/describe.py
```

## How Allay reads it

The backend reads `index.json` at the newest commit of `master` and links each build by that commit, so a file never changes under a link. The plugin downloads only from this repository, checks the SHA-256 the index gives, and keeps the file only while it builds.
