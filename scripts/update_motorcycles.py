#!/usr/bin/env python3
"""
Weekly motorcycle-data updater.

Pulls motorcycle model data from Wikidata's public SPARQL endpoint,
merges it with any hand-curated entries in `manual_additions.json`,
and writes the combined result to `docs/motorcycles.json`.

Wikidata is the structured data backend of Wikipedia. Every motorcycle
model has a stable entity ID, manufacturer relationship, and production
start date, so we can query precisely without HTML scraping.

Safety rails:
- If the Wikidata query fails or returns suspiciously few results,
  we leave motorcycles.json unchanged and exit 1 so the Action fails visibly.
- Manual additions are always preserved.
- We don't delete anything that's in the existing JSON — only add.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests

REPO_ROOT = Path(__file__).resolve().parent.parent
MOTORCYCLES_JSON = REPO_ROOT / "docs" / "motorcycles.json"
MANUAL_ADDITIONS_JSON = REPO_ROOT / "docs" / "manual_additions.json"

WIKIDATA_ENDPOINT = "https://query.wikidata.org/sparql"

# Manufacturers we care about. Wikidata has long tail of obscure brands — we only
# want the ones users will recognize. Matches the curated list in popularMakes/allMakes.
TRACKED_MANUFACTURERS = {
    "Aprilia", "Benelli", "Beta", "Bimota", "BMW", "BMW Motorrad", "BSA",
    "Buell", "CFMoto", "CSC Motorcycles", "Damon", "Ducati", "Energica",
    "GASGAS", "Harley-Davidson", "Honda", "Husqvarna", "Husqvarna Motorcycles",
    "Indian", "Indian Motorcycle", "Kawasaki", "KTM", "KYMCO", "Lambretta",
    "Lifan", "Lightning Motorcycles", "Livewire", "Loncin", "Moto Guzzi",
    "MV Agusta", "Norton", "Piaggio", "Royal Enfield", "Sherco", "Shineray",
    "Suzuki", "SYM", "TM Racing", "Triumph", "TVS", "Vespa", "Yamaha",
    "Zero", "Zero Motorcycles", "Zongshen",
}

# Map Wikidata's verbose manufacturer names to the canonical name used in our JSON.
# e.g. "Zero Motorcycles" → "Zero", "Indian Motorcycle" → "Indian".
MANUFACTURER_ALIASES = {
    "BMW Motorrad": "BMW",
    "CSC Motorcycles": "CSC",
    "Husqvarna Motorcycles": "Husqvarna",
    "Indian Motorcycle": "Indian",
    "Lightning Motorcycles": "Lightning",
    "Zero Motorcycles": "Zero",
    # Corporate / regional variants returned by Wikidata
    "Yamaha Motor Company": "Yamaha",
    "Yamaha Motor": "Yamaha",
    "Suzuki Motor Corporation": "Suzuki",
    "Suzuki Motor": "Suzuki",
    "Kawasaki Heavy Industries": "Kawasaki",
    "Kawasaki Motors": "Kawasaki",
    "Honda Motor Company": "Honda",
    "Honda Motor": "Honda",
    "Norton Motorcycle Company": "Norton",
    "Norton Motorcycles": "Norton",
    "Royal Enfield India": "Royal Enfield",
    "Kwang Yang Motor": "KYMCO",
    "Kwang Yang Motor Co.": "KYMCO",
    "Harley Davidson": "Harley-Davidson",
    "Piaggio & C.": "Piaggio",
    "MV Agusta Motor": "MV Agusta",
}

# Safety thresholds — if the query returns way too few or too many results,
# something is wrong and we should bail rather than overwriting good data.
MIN_EXPECTED_MODELS = 50
MAX_DELTA_PER_RUN = 200   # if >200 new models appear in one week, open a PR instead


SPARQL_QUERY = """
SELECT DISTINCT ?model ?modelLabel ?manufacturerLabel ?startYear WHERE {
  ?model wdt:P31/wdt:P279* wd:Q34493 .       # instance of motorcycle (or subclass)
  ?model wdt:P176 ?manufacturer .            # manufacturer
  OPTIONAL {
    ?model wdt:P571 ?inception .
    BIND(YEAR(?inception) AS ?startYear)
  }
  SERVICE wikibase:label { bd:serviceParam wikibase:language "en". }
}
LIMIT 20000
"""


def fetch_wikidata_models() -> list[dict]:
    """Query Wikidata for all motorcycle models with manufacturers."""
    headers = {
        "Accept": "application/sparql-results+json",
        "User-Agent": "MotoMinderDataUpdater/1.0 (https://github.com/FugginBeenus/motominder-data)",
    }
    response = requests.get(
        WIKIDATA_ENDPOINT,
        params={"query": SPARQL_QUERY, "format": "json"},
        headers=headers,
        timeout=90,
    )
    response.raise_for_status()
    bindings = response.json().get("results", {}).get("bindings", [])

    results = []
    for b in bindings:
        raw_model = b.get("modelLabel", {}).get("value", "").strip()
        raw_maker = b.get("manufacturerLabel", {}).get("value", "").strip()
        year = b.get("startYear", {}).get("value")

        if not raw_model or not raw_maker:
            continue
        # Skip entity IDs (Wikidata returns Q-codes when no English label exists)
        if raw_model.startswith("Q") and raw_model[1:].isdigit():
            continue
        if raw_maker.startswith("Q") and raw_maker[1:].isdigit():
            continue

        canonical_maker = MANUFACTURER_ALIASES.get(raw_maker, raw_maker)
        if canonical_maker not in TRACKED_MANUFACTURERS and canonical_maker not in MANUFACTURER_ALIASES.values():
            continue

        results.append({
            "manufacturer": canonical_maker,
            "model": clean_model_name(raw_model, canonical_maker),
            "year": int(year) if year and year.isdigit() else None,
        })
    return results


def clean_model_name(raw: str, manufacturer: str) -> str:
    """Strip redundant manufacturer prefixes and normalize whitespace."""
    cleaned = raw.strip()
    # Drop leading "Honda CBR600RR" → "CBR600RR" style duplication
    prefix = f"{manufacturer} "
    if cleaned.lower().startswith(prefix.lower()):
        cleaned = cleaned[len(prefix):]
    # Collapse repeated spaces
    return " ".join(cleaned.split())


def load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def merge(existing: dict, wikidata: list[dict], manual: dict) -> tuple[dict, int]:
    """Merge Wikidata + manual additions into the existing JSON.
    Returns (new_json, delta_count)."""
    models = {k: set(v) for k, v in existing.get("models", {}).items()}

    # Wikidata additions
    wd_added = 0
    for entry in wikidata:
        maker = entry["manufacturer"]
        model = entry["model"]
        if len(model) < 2 or len(model) > 60:
            continue
        models.setdefault(maker, set())
        if model not in models[maker]:
            models[maker].add(model)
            wd_added += 1

    # Manual additions — always applied, overrides anything auto-removed
    for maker, extras in manual.get("models", {}).items():
        models.setdefault(maker, set())
        models[maker].update(extras)

    # Sort and freeze
    sorted_models = {k: sorted(v) for k, v in sorted(models.items())}

    popular_makes = list(existing.get("popularMakes", []))
    all_makes = sorted(set(existing.get("allMakes", [])) | set(sorted_models.keys()))

    new_json = {
        "version": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "source": "wikidata + manual",
        "popularMakes": popular_makes,
        "allMakes": all_makes,
        "models": sorted_models,
    }
    return new_json, wd_added


def main() -> int:
    existing = load_json(MOTORCYCLES_JSON)
    manual = load_json(MANUAL_ADDITIONS_JSON)

    print(f"Existing models: {sum(len(v) for v in existing.get('models', {}).values())}")

    try:
        wikidata = fetch_wikidata_models()
    except Exception as exc:
        print(f"ERROR: Wikidata query failed: {exc}", file=sys.stderr)
        return 1

    print(f"Wikidata returned {len(wikidata)} model entries")

    if len(wikidata) < MIN_EXPECTED_MODELS:
        print(f"ERROR: Only {len(wikidata)} results — likely a query failure, aborting",
              file=sys.stderr)
        return 1

    new_json, delta = merge(existing, wikidata, manual)

    if delta == 0:
        print("No new models — JSON unchanged.")
        # Still write to refresh the generatedAt timestamp — keep this
        # commented if you want zero-change weeks to skip the commit.
        return 0

    if delta > MAX_DELTA_PER_RUN:
        print(f"WARNING: {delta} new models this run — flagging for review.")
        print("::warning::Unusually large delta; check for Wikidata schema changes.")

    MOTORCYCLES_JSON.write_text(
        json.dumps(new_json, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(f"Added {delta} new models, wrote {MOTORCYCLES_JSON}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
