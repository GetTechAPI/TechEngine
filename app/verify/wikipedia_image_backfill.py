"""Backfill freely licensed Commons photos from already cited Wikipedia articles.

Run a dry sample first, then use --apply --offset/--limit for sequential batches.
The append-only decision cache is shared between dry runs and apply runs.
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

import httpx
from bs4 import BeautifulSoup

from app.verify.wikipedia_smartphone_backfill import (
    USER_AGENT,
    PoliteWiki,
    append_cache,
    variant_conflict,
)

VERSION = 7
BAD_IMAGE = re.compile(
    r"(?<![A-Za-z0-9])(?:logo|logotype|wordmark|icon|emblem|flag|symbol|diagram|chart|screenshot|placeholder|render|advertisement|battery|headquarters?|building|campus|series|lineup|packaging|시리즈)(?![A-Za-z0-9])",
    re.I,
)
GROUP_IMAGE = re.compile(r"_and_.*(?:Xiaomi|Samsung|Huawei|OnePlus|Oppo|Vivo)", re.I)
NON_PHONE_MODEL = re.compile(r"^(?:Surface \d+|Palm TX)$", re.I)
BAD_LICENSE = re.compile(r"non.free|fair.use|all.rights.reserved|unknown|unclear|copyrighted", re.I)
FREE_LICENSE = re.compile(
    r"^(?:CC[- ]?BY(?:[- ]?SA)?[- ]?[1-4](?:\.0)?|CC0(?:[- ]?1\.0)?|PUBLIC DOMAIN)$", re.I
)


def article_url(record: dict[str, Any]) -> str | None:
    for url in record.get("source_urls") or []:
        if not isinstance(url, str):
            continue
        parsed = urlparse(url)
        if (
            parsed.scheme == "https"
            and parsed.hostname
            and (parsed.hostname == "wikipedia.org" or parsed.hostname.endswith(".wikipedia.org"))
            and parsed.path.startswith("/wiki/")
        ):
            return url
    return None


def eligible(root: Path) -> list[tuple[Path, dict[str, Any], str]]:
    rows = []
    for path in sorted((root / "data" / "smartphone").rglob("*.json")):
        try:
            record = json.loads(path.read_text(encoding="utf-8-sig"))
        except (ValueError, OSError):
            continue
        if isinstance(record, dict) and "image_url" in record and record["image_url"] is None:
            url = article_url(record)
            if url:
                rows.append((path, record, url))
    return rows


def plain(value: object) -> str:
    return BeautifulSoup(str(value or ""), "html.parser").get_text(" ", strip=True)


def metadata_value(metadata: dict[str, Any], key: str) -> str:
    item = metadata.get(key)
    return plain(item.get("value")) if isinstance(item, dict) else ""


def filename_matches_model(name: str, filename: str) -> bool:
    """Require a distinctive model token in the file name; generic brand photos fail."""
    file_words = re.findall(
        r"[a-z]+\d+[a-z]*|\d+[a-z]+|[a-z]+|\d+",
        re.sub(r"([a-z])([A-Z])", r"\1 \2", filename.rsplit(".", 1)[0]).lower(),
    )
    file_tokens = set(file_words)
    file_tokens.update(
        left + right
        for left, right in zip(file_words, file_words[1:], strict=False)
        if left.isalpha() and right.isdigit()
    )
    name_tokens = re.findall(r"[a-z]+\d+[a-z]*|\d+[a-z]+|[a-z]+|\d+", name.lower())
    codes = [
        token
        for token in name_tokens
        if any(char.isdigit() for char in token) and not token.endswith("gb")
    ]
    if codes:
        return any(code in file_tokens for code in codes)
    generic = {
        "apple",
        "samsung",
        "google",
        "honor",
        "huawei",
        "motorola",
        "nokia",
        "xiaomi",
        "oppo",
        "vivo",
        "realme",
        "blackberry",
        "casio",
        "amazon",
        "nothing",
        "jolla",
        "itel",
        "oneplus",
        "microsoft",
        "sony",
        "palm",
        "phone",
        "smartphone",
        "mobile",
        "edition",
        "generation",
        "plus",
        "ultra",
        "pro",
        "gb",
        "htc",
        "galaxy",
        "xperia",
        "lumia",
    }
    distinctive = [token for token in name_tokens if token not in generic and len(token) >= 2]
    return any(token in file_tokens for token in distinctive)


def license_name(metadata: dict[str, Any]) -> str | None:
    short = metadata_value(metadata, "LicenseShortName").upper().replace(" ", "-")
    terms = " ".join(
        metadata_value(metadata, key) for key in ("UsageTerms", "Restrictions", "License")
    )
    if BAD_LICENSE.search(short + " " + terms) or not FREE_LICENSE.fullmatch(
        short.replace("PUBLIC-DOMAIN", "PUBLIC DOMAIN")
    ):
        return None
    if short.startswith("CC0"):
        return "CC0-1.0"
    if short == "PUBLIC-DOMAIN":
        return "Public Domain"
    return short.replace("CC-BY-SA-", "CC-BY-SA-").replace("CC-BY-", "CC-BY-")


def load_decisions(path: Path) -> dict[str, dict[str, Any]]:
    decisions = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                item = json.loads(line)
            except ValueError:
                continue
            if item.get("version") in {6, VERSION} and isinstance(item.get("path"), str):
                decisions[item["path"]] = item
    return decisions


class CommonsFetcher(PoliteWiki):
    def __init__(self, sleep_s: float = 1.0) -> None:
        super().__init__(sleep_s=max(1.0, sleep_s), timeout=20.0)

    def query(self, host: str, params: dict[str, str]) -> dict[str, Any]:
        self._pause()
        if self._client is None:
            self._client = httpx.Client(
                timeout=self.timeout, headers={"User-Agent": USER_AGENT}, follow_redirects=True
            )
        response = self._client.get(
            f"https://{host}/w/api.php", params={"action": "query", "format": "json", **params}
        )
        response.raise_for_status()
        return response.json()


def inspect(url: str, fetcher: CommonsFetcher, name: str = "") -> dict[str, str]:
    parsed = urlparse(url)
    title = unquote(parsed.path.removeprefix("/wiki/")).replace("_", " ")
    try:
        result = fetcher.query(
            parsed.hostname or "en.wikipedia.org",
            {"titles": title, "prop": "pageimages", "piprop": "original", "redirects": "1"},
        )
        pages = result.get("query", {}).get("pages", {})
        page = next(iter(pages.values()))
        original = page.get("original") or {}
        source = original.get("source") if isinstance(original, dict) else None
        filename = page.get("pageimage")
        if not filename and isinstance(source, str):
            filename = unquote(urlparse(source).path.rsplit("/", 1)[-1])
        if not isinstance(filename, str) or not filename:
            return {"reason": "no_image"}
        if (
            BAD_IMAGE.search(filename)
            or GROUP_IMAGE.search(filename)
            or NON_PHONE_MODEL.search(name)
            or not filename.lower().endswith((".jpg", ".jpeg", ".webp"))
            or (
                name
                and (
                    not filename_matches_model(name, filename)
                    or variant_conflict(name, filename.replace("_", " "))
                )
            )
        ):
            return {"reason": "logo_like", "file": filename}
        commons = fetcher.query(
            "commons.wikimedia.org",
            {
                "titles": f"File:{filename}",
                "prop": "imageinfo",
                "iiprop": "url|mime|extmetadata",
                "iiextmetadatafilter": (
                    "LicenseShortName|License|UsageTerms|Restrictions|Artist|Credit|"
                    "ImageDescription|ObjectName"
                ),
            },
        )
        file_page = next(iter(commons.get("query", {}).get("pages", {}).values()))
        info = (file_page.get("imageinfo") or [None])[0]
        if not isinstance(info, dict):
            return {"reason": "bad_license", "file": filename}
        image_url = info.get("url")
        metadata = info.get("extmetadata") or {}
        license_id = license_name(metadata)
        if (
            not license_id
            or not isinstance(image_url, str)
            or urlparse(image_url).hostname not in {"upload.wikimedia.org", "commons.wikimedia.org"}
        ):
            return {
                "reason": "bad_license",
                "file": filename,
                "raw_license": metadata_value(metadata, "LicenseShortName"),
            }
        if info.get("mime") not in {"image/jpeg", "image/webp"}:
            return {"reason": "logo_like", "file": filename}
        description = " ".join(
            metadata_value(metadata, key) for key in ("ObjectName", "ImageDescription")
        )
        if BAD_IMAGE.search(description):
            return {"reason": "logo_like", "file": filename}
        attribution = metadata_value(metadata, "Artist") or metadata_value(metadata, "Credit")
        if not attribution:
            return {"reason": "bad_license", "file": filename, "raw_license": license_id}
        return {
            "reason": "accepted",
            "file": filename,
            "image_url": image_url.split("?", 1)[0],
            "image_license": license_id,
            "image_attribution": attribution,
        }
    except (httpx.HTTPError, ValueError, KeyError, StopIteration, TypeError) as exc:
        return {"reason": "error", "error": str(exc)[:200]}


def write_image(path: Path, result: dict[str, str]) -> None:
    text = path.read_text(encoding="utf-8")
    record = json.loads(text)
    if record.get("image_url") is not None:
        return
    replacement = (
        '"image_url": ' + json.dumps(result["image_url"], ensure_ascii=False) + ",\n"
        '  "image_license": ' + json.dumps(result["image_license"], ensure_ascii=False) + ",\n"
        '  "image_attribution": ' + json.dumps(result["image_attribution"], ensure_ascii=False)
    )
    updated, count = re.subn(r'"image_url"\s*:\s*null', lambda _match: replacement, text, count=1)
    if count != 1:
        raise ValueError(f"missing null image_url in {path}")
    path.write_text(updated, encoding="utf-8")


def run(
    root: Path,
    *,
    offset: int = 0,
    limit: int | None = None,
    apply: bool = False,
    sleep_s: float = 1.0,
    cache_path: Path | None = None,
) -> list[dict[str, Any]]:
    cache_path = cache_path or root / "data" / "_verify" / "state" / "wikipedia_image_cache.jsonl"
    cache = load_decisions(cache_path)
    rows = eligible(root)[offset : None if limit is None else offset + limit]
    fetcher = CommonsFetcher(sleep_s)
    results = []
    for index, (path, record, article) in enumerate(rows, 1):
        rel = path.relative_to(root).as_posix()
        decision = cache.get(rel)
        if (
            decision is not None
            and decision.get("version") == 6
            and decision.get("article") == article
        ):
            decision = dict(decision, version=VERSION)
            if decision.get("reason") == "accepted" and (
                BAD_IMAGE.search(str(decision.get("file") or ""))
                or GROUP_IMAGE.search(str(decision.get("file") or ""))
                or NON_PHONE_MODEL.search(str(record.get("name") or ""))
                or not filename_matches_model(
                    str(record.get("name") or ""), str(decision.get("file") or "")
                )
                or variant_conflict(
                    str(record.get("name") or ""), str(decision.get("file") or "").replace("_", " ")
                )
            ):
                decision["reason"] = "logo_like"
                for key in ("image_url", "image_license", "image_attribution"):
                    decision.pop(key, None)
            append_cache(decision, cache_path)
        if (
            decision is None
            or decision.get("article") != article
            or decision.get("reason") == "error"
        ):
            decision = {
                "version": VERSION,
                "path": rel,
                "name": record.get("name"),
                "article": article,
                **inspect(article, fetcher, str(record.get("name") or "")),
            }
            if decision["reason"] != "error":
                append_cache(decision, cache_path)
        if apply and decision["reason"] == "accepted":
            write_image(path, decision)
        results.append(decision)
        message = (
            f"[{index}/{len(rows)}] {decision['reason']}: "
            f"{record.get('name')} ({decision.get('file', '')})"
        )
        print(message.encode("ascii", "backslashreplace").decode("ascii"), flush=True)
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--sleep", type=float, default=1.0)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    results = run(
        args.data_root, offset=args.offset, limit=args.limit, apply=args.apply, sleep_s=args.sleep
    )
    print(
        json.dumps(
            {
                "checked": len(results),
                "reasons": Counter(row["reason"] for row in results),
                "licenses": Counter(
                    row.get("image_license") for row in results if row["reason"] == "accepted"
                ),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
