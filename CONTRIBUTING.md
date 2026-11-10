# Adding a build

You bring the schematic and who made it. CI does the rest: it cuts the build to its blocks, describes it in five languages, and adds it to the search. Once I merge your pull request, players on every Allay server can find it and build it.

## 1. Check that it may be shared

Allay is a paid service, so every build must be fine to share on paid servers too. One of these must hold:

| `license` | When |
|---|---|
| `CC0-1.0`, `CC-BY-4.0`, `CC-BY-SA-4.0`, `MIT` | The author published it under that license |
| `permission` | The author told you it may go here. Add `"permission"` with a link to where they said so |
| `unrestricted` | The author published it for download on a public page and didn't restrict sharing |

Non-commercial licenses (`CC-BY-NC-…`) and pages that ask not to reupload don't fit. Your own build fits under any open license you choose.

## 2. Add a folder

Make a folder under `catalog/`, in a section and with a name of lowercase words joined by `-`: `catalog/houses/medieval-mill/`. Its path is the build's id. Put two files in it:

- **The schematic**, named `source.schem` (WorldEdit 7 or newer), `source.litematic` (Litematica), or `source.schematic` (MCEdit, before 1.13). Just one, as the author published it. Air around the build is fine: CI cuts it away.
- **`meta.json`**:

  ```json
  {
    "title": "Medieval windmill",
    "author": "Their name, as the source shows it",
    "source": "https://the-page-you-took-it-from",
    "license": "CC-BY-4.0"
  }
  ```

  `title` is optional. Don't copy the page's description or pictures: the catalog writes its own words.

Leave out `build.schem` and `index.json`: CI makes them after the merge.

## 3. Open a pull request

The check runs at once. It reads your schematic, checks `meta.json` and the license, and fails with the reason when something's off: a source it can't read, a build over 2 000 000 blocks or 1024 a side, a path that isn't lowercase words.

After the merge, CI on `master` makes `build.schem`, writes the titles, the description, and the tags with a model into `meta.json` under `generated`, and rebuilds `index.json`. Allay's server picks the new index up within the hour. A wrong description can be fixed by hand in `generated` and stays until the build itself changes.

## Taking a build out

An author who wants their build out of the catalog opens an issue, and it goes.
