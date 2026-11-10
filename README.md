# Allay schematics

The builds Allay offers players: real schematics, each with its author, its source, and a license that lets every server build it. Allay's backend searches this catalog when a player asks for a build (“a medieval windmill”), and the plugin shows the one they pick as a preview and builds it from their own blocks.

Allay takes schematics from here only. A server can't add its own, since nobody would describe them for the search.

## What's in it

- `catalog/<section>/<name>/` is one build: its id is that path, like `houses/medieval-mill`.
  - `source.schem` (Sponge, WorldEdit 7 or newer) or `source.litematic` (Litematica), as its author published it.
  - `meta.json`: who made it and under what license, written by hand.
  - `build.schem`: what Allay builds, made by CI from the source. A Sponge v3 schematic cut down to the blocks, with air in no corner.
- `index.json` is everything the search reads: each build's size, block count, materials, titles in English, Russian, Spanish, Portuguese, and German, a description, and tags. CI makes it.
- `tools/` holds the Python that makes them, with no dependencies, and `tests/` its tests.

## Adding a build

1. Make sure its license lets anyone share it, paid servers included: `CC0-1.0`, `CC-BY-4.0`, `CC-BY-SA-4.0`, or `MIT`. Non-commercial licenses don't fit, since Allay is a paid service. For anything else, ask the author, and write where they allowed it as `permission`.
2. Put the file into a new folder under `catalog/`, as `source.schem` or `source.litematic`.
3. Write `meta.json`:

   ```json
   {
     "title": "Medieval windmill",
     "description": "What the source page says about it, in its own words",
     "author": "Their name, as the source shows it",
     "source": "https://the page you took it from",
     "license": "CC-BY-4.0"
   }
   ```

   `title` and `description` are optional; a page with nothing to say leaves them out. With `"license": "permission"`, add `"permission"`: a link to where the author agreed.
4. Open a pull request. Once it's merged, CI makes `build.schem`, describes the build, and updates `index.json`.

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
