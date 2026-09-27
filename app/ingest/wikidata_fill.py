"""Fill absent software/website fields from their own cited Wikidata entities."""
from __future__ import annotations

import argparse
import difflib
import json
import re
import time
from collections import Counter
from collections.abc import Callable, Iterable
from datetime import date
from pathlib import Path
from typing import Any

import httpx

from app.data_root import get_data_root
from app.verify.common import Record, configure_stdout
from app.verify.offline import score_record
from app.verify.wikidata import USER_AGENT, qid_of

MAPPINGS = {
    "software": {"P577": "release_date", "P178": "developers", "P123": "publishers",
                 "P306": "operating_systems", "P275": "licenses", "P136": "genres",
                 "P277": "programming_languages"},
    "website": {"P856": "homepage_url", "P571": "launch_date", "P407": "languages",
                "P127": "owners"},
}
DATES = {"release_date", "launch_date"}
PROPERTIES = {prop for mapping in MAPPINGS.values() for prop in mapping}


def compact(entity: dict[str, Any]) -> dict[str, Any]:
    """Discard unrelated claims, qualifiers and references from the fill cache."""
    result: dict[str, Any] = {
        key: entity[key] for key in ("id", "missing", "redirect", "lastrevid", "labels")
        if key in entity
    }
    result["claims"] = {
        prop: [{"rank": s.get("rank", "normal"), "mainsnak": s.get("mainsnak", {})}
               for s in statements]
        for prop, statements in entity.get("claims", {}).items() if prop in PROPERTIES
    }
    return result


def absent(value: Any) -> bool:
    return value is None or value == "" or value == [] or value == {}


def usable(entity: dict[str, Any], qid: str) -> bool:
    return not ("missing" in entity or "redirect" in entity) and entity.get("id") == qid


class EntityCache:
    """Persistent per-entity JSON cache; failed responses are never cached."""

    def __init__(self, directory: Path, client: httpx.Client,
                 sleep: Callable[[float], None] = time.sleep) -> None:
        self.directory = directory
        self.client = client
        self.sleep = sleep
        self.last_request: float | None = None
        directory.mkdir(parents=True, exist_ok=True)

    def fetch(self, ids: Iterable[str]) -> dict[str, dict[str, Any]]:
        result: dict[str, dict[str, Any]] = {}
        pending = []
        for qid in dict.fromkeys(ids):
            if not re.fullmatch(r"Q[1-9][0-9]*", qid):
                raise ValueError(f"Invalid entity ID: {qid}")
            path = self.directory / f"{qid}.json"
            if path.exists():
                entity = json.loads(path.read_text(encoding="utf-8"))
                if not isinstance(entity, dict):
                    raise ValueError(f"Invalid cache entry: {qid}")
                result[qid] = compact(entity)
            else:
                pending.append(qid)
        for start in range(0, len(pending), 50):
            batch = pending[start:start + 50]
            entities = self._retrieve(batch)
            for qid in batch:
                entity = compact(entities[qid])
                result[qid] = entity
                path = self.directory / f"{qid}.json"
                temporary = path.with_suffix(".tmp")
                temporary.write_text(json.dumps(entity, ensure_ascii=False), encoding="utf-8")
                temporary.replace(path)
            print(f"Fetched {start + len(batch)}/{len(pending)} uncached entities", flush=True)
        return result

    def _retrieve(self, batch: list[str]) -> dict[str, Any]:
        for attempt in range(3):
            if self.last_request is not None:
                self.sleep(max(0.0, 1.0 - (time.monotonic() - self.last_request)))
            self.last_request = time.monotonic()
            try:
                response = self.client.get("https://www.wikidata.org/w/api.php", params={
                    "action": "wbgetentities", "ids": "|".join(batch),
                    "props": "info|claims|labels", "languages": "en",
                    "format": "json", "maxlag": "5",
                }, headers={"User-Agent": USER_AGENT}, timeout=60)
                response.raise_for_status()
                payload = response.json()
                entities = payload.get("entities")
                if "error" in payload or not isinstance(entities, dict):
                    raise ValueError(f"Wikidata API error: {payload.get('error')}")
                missing = [qid for qid in batch if not isinstance(entities.get(qid), dict)]
                if missing and "truncated" not in json.dumps(payload.get("warnings", {})):
                    raise ValueError("Wikidata incomplete entity batch")
                break
            except (httpx.HTTPError, ValueError):
                if attempt == 2:
                    raise
                self.sleep(5.0 * (attempt + 1))
        if missing:
            if len(batch) == 1:
                raise ValueError(f"Wikidata entity exceeds response size limit: {batch[0]}")
            # Only explicit size truncation permits smaller batches. Other
            # incomplete responses remain uncached for retry.
            size = max(1, len(batch) // 2)
            for start in range(0, len(missing), size):
                entities.update(self._retrieve(missing[start:start + size]))
        complete: dict[str, Any] = entities
        return complete


def values(entity: dict[str, Any], prop: str) -> list[Any]:
    statements = [s for s in entity.get("claims", {}).get(prop, [])
                  if s.get("rank") != "deprecated"]
    preferred = [s for s in statements if s.get("rank") == "preferred"]
    return [s["mainsnak"]["datavalue"]["value"] for s in preferred or statements
            if s.get("mainsnak", {}).get("snaktype") == "value"
            and "datavalue" in s["mainsnak"]]


def calendar_date(value: Any) -> str | None:
    # Models/validate accept YYYY-MM-DD only: lesser precision cannot be represented.
    if not isinstance(value, dict) or value.get("precision") != 11:
        return None
    if value.get("calendarmodel") != "http://www.wikidata.org/entity/Q1985727":
        return None
    match = re.fullmatch(r"\+(\d{4}-\d{2}-\d{2})T00:00:00Z", value.get("time", ""))
    if match:
        try:
            return date.fromisoformat(match[1]).isoformat()
        except ValueError:
            pass
    return None


def fill(record: dict[str, Any], category: str, entity: dict[str, Any],
         labels: dict[str, dict[str, Any]]) -> dict[str, Any]:
    updated = record.copy()
    for prop, field in MAPPINGS[category].items():
        if not absent(record.get(field)):
            continue
        candidates = values(entity, prop)
        if field in DATES:
            dates = [parsed for v in candidates if (parsed := calendar_date(v))]
            if dates:
                updated[field] = min(dates)
        elif field == "homepage_url":
            urls = [v for v in candidates if isinstance(v, str)
                    and v.startswith(("https://", "http://"))]
            if urls:
                updated[field] = urls[0]
        else:
            names = []
            for value in candidates:
                qid = value.get("id") if isinstance(value, dict) else None
                target = labels.get(qid, {}) if isinstance(qid, str) else {}
                label = target.get("labels", {}).get("en", {}).get("value")
                if qid and usable(target, qid) and isinstance(label, str) and label:
                    names.append(label)
            if names:
                updated[field] = list(dict.fromkeys(names))
    return updated


def serialize(data: dict[str, Any], original: bytes) -> bytes:
    newline = "\r\n" if b"\r\n" in original else "\n"
    text = (json.dumps(data, ensure_ascii=False, indent=2) + "\n").replace("\n", newline)
    return (b"\xef\xbb\xbf" if original.startswith(b"\xef\xbb\xbf") else b"") + text.encode("utf-8")


def run(category: str, cache: EntityCache, root: Path, *, apply: bool = False,
        maximum: int | None = None) -> tuple[dict[str, Any], list[str]]:
    paths = sorted((root / category).rglob("*.json"))
    if not paths:
        raise ValueError(f"No {category} records in data checkout")
    if maximum is not None:
        paths = paths[:maximum]
    records = [(p, json.loads(p.read_bytes().decode("utf-8-sig"))) for p in paths]
    cited = [(p, r, list(dict.fromkeys(q for u in r.get("source_urls", [])
                                      if (q := qid_of(u))))) for p, r in records]
    entities = cache.fetch(q for _, _, ids in cited for q in ids)
    references: set[str] = set()
    for _, record, ids in cited:
        for qid in ids:
            entity = entities[qid]
            if not usable(entity, qid):
                continue
            for prop, field in MAPPINGS[category].items():
                if field not in DATES | {"homepage_url"} and absent(record.get(field)):
                    references.update(v["id"] for v in values(entity, prop)
                                      if isinstance(v, dict) and isinstance(v.get("id"), str))
    labels = cache.fetch(sorted(references))
    counts: Counter[str] = Counter()
    before_green = after_green = moved = changed = 0
    samples: list[str] = []
    for path, record, ids in cited:
        updated = record.copy()
        for qid in ids:
            if usable(entities[qid], qid):
                updated = fill(updated, category, entities[qid], labels)
        rel = path.relative_to(root).as_posix()
        before = score_record(Record(category, rel, record), date.today().year, {}).band == "green"
        after = score_record(Record(category, rel, updated), date.today().year, {}).band == "green"
        before_green += before
        after_green += after
        moved += after and not before
        if updated == record:
            continue
        changed += 1
        counts.update(field for field in MAPPINGS[category].values()
                      if record.get(field) != updated.get(field))
        original = path.read_bytes()
        rendered = serialize(updated, original)
        if len(samples) < 5:
            samples.append("".join(difflib.unified_diff(
                original.decode("utf-8-sig").splitlines(keepends=True),
                rendered.decode("utf-8-sig").splitlines(keepends=True),
                fromfile=rel, tofile=rel)))
        if apply:
            path.write_bytes(rendered)
    return {"category": category, "records": len(records), "changed_records": changed,
            "fills": {f: counts[f] for f in MAPPINGS[category].values()},
            "green_before": before_green, "green_after": after_green, "moved_to_green": moved,
            "applied": apply}, samples


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--category", choices=list(MAPPINGS), required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--max", type=int, dest="maximum")
    parser.add_argument("--report", type=Path, help="Write dry-run summary and five sample diffs")
    args = parser.parse_args()
    if args.maximum is not None and args.maximum < 0:
        parser.error("--max must be nonnegative")
    configure_stdout()
    root = get_data_root()
    with httpx.Client() as client:
        cache = EntityCache(root / "_verify/state/wikidata_fill", client)
        summary, samples = run(args.category, cache, root, apply=args.apply, maximum=args.maximum)
    print(json.dumps(summary, indent=2))
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(
            f"# Wikidata fill: {args.category}\n\n"
            "Dates require day precision and Gregorian calendar; month/year claims are skipped.\n\n"
            + "```json\n" + json.dumps(summary, indent=2) + "\n```\n\n"
            + "\n\n".join("```diff\n" + sample + "```" for sample in samples), encoding="utf-8")


if __name__ == "__main__":
    main()
