# motominder-data

Motorcycle make/model data for the MotoMinder / Riderr iOS app.

Served via GitHub Pages at:
**`https://fugginbeenus.github.io/motominder-data/motorcycles.json`**

The iOS app fetches this JSON on launch, caches it for 24 hours, and falls back to a bundled copy when offline.

---

## Structure

```
docs/
├── motorcycles.json       ← served by GitHub Pages (auto-updated weekly)
└── manual_additions.json  ← hand-curated models the updater should always include
scripts/
├── update_motorcycles.py  ← Wikidata SPARQL fetcher + JSON merger
└── requirements.txt
.github/workflows/
└── update-motorcycles.yml ← runs the script every Sunday at 06:00 UTC
```

---

## How to add a model the automation missed

Edit `docs/manual_additions.json` directly on GitHub (pencil icon on the file page). The next weekly run — or the next manual trigger — will merge it into `motorcycles.json`.

Example:

```json
{
  "models": {
    "Triumph": ["Street Triple 765 RX"],
    "Ducati": ["Panigale V4 SP3"]
  }
}
```

## How to trigger an update now

1. Go to the **Actions** tab
2. Pick **Update motorcycle data** from the left sidebar
3. Click **Run workflow** → **Run workflow**

It runs in ~30 seconds and commits any new models it finds.

---

## Data source

[Wikidata](https://query.wikidata.org/) — the structured data backend of Wikipedia. Every motorcycle model on Wikipedia has a corresponding Wikidata entity with stable properties (manufacturer, production start, displacement, etc.). Querying it via SPARQL is free, rate-limit friendly, and doesn't break when websites redesign.

The updater:
1. Queries all entities that are instances of "motorcycle model" (`wd:Q15056993`)
2. Filters to the tracked manufacturer list in `update_motorcycles.py`
3. Cleans model names (strips redundant manufacturer prefixes)
4. Merges with existing JSON and `manual_additions.json`
5. Commits only if something actually changed
