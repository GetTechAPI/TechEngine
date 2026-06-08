"""Vendor name → TechAPI kebab-case slug.

The same normalization runs over both upstream catalog entries and (when needed)
curated names, so equivalent SKUs collapse to the same slug regardless of the
source's punctuation/casing/manufacturer prefix.
"""

from __future__ import annotations

import re
import unicodedata

# Known prefixes per manufacturer slug. Compared lowercased, longest first so
# "Advanced Micro Devices" wins over "AMD" when both could match.
_MANUFACTURER_PREFIXES: dict[str, list[str]] = {
    "intel": ["intel"],
    "amd": ["advanced micro devices", "amd"],
    "nvidia": ["nvidia corporation", "nvidia"],
    "samsung": ["samsung electronics", "samsung"],
    "apple": ["apple inc.", "apple"],
    "qualcomm": ["qualcomm technologies", "qualcomm"],
    "mediatek": ["mediatek"],
    "ibm": ["ibm"],
    "motorola": ["motorola"],
    "google": ["google"],
    "huawei": ["huawei"],
    "xiaomi": ["xiaomi"],
    "oppo": ["oppo"],
    "vivo": ["vivo"],
    "oneplus": ["oneplus"],
    "lg": ["lg electronics", "lg"],
    "sony": ["sony"],
    "asus": ["asustek", "asus"],
    "msi": ["msi"],
    "gigabyte": ["gigabyte"],
}

_SEPARATOR_RE = re.compile(r"[^a-z0-9]+")
_COLLAPSE_RE = re.compile(r"-+")

# Decimal-derived artifacts like "1-25" (from a "1.25" clock cell) — two pure
# numeric groups joined by a hyphen. Real CPU/GPU SKUs are a single numeric run
# ("6276") or carry letters ("core-i9-14900k"), never bare "<digits>-<digits>".
_DECIMAL_ARTIFACT_RE = re.compile(r"^\d+-\d+$")


def is_probable_model_slug(slug: str, *, min_len: int = 4) -> bool:
    """Heuristic: does ``slug`` look like a real CPU/GPU model (vs a stray cell)?

    Rejects too-short slugs, slugs with no digit, and decimal-derived artifacts
    such as ``"1-25"`` that come from non-model cells (clock speeds, footnotes).
    """
    if len(slug) < min_len or not any(c.isdigit() for c in slug):
        return False
    return not _DECIMAL_ARTIFACT_RE.match(slug)


def slugify(name: str, manufacturer: str | None = None) -> str:
    """Normalize a vendor-style name to a kebab-case slug.

    Strips a known manufacturer prefix when ``manufacturer`` is given so that
    "Intel Core i9-14900K" matches the existing TechAPI slug "core-i9-14900k".
    """
    text = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
    text = text.strip().lower()
    if manufacturer:
        for prefix in _MANUFACTURER_PREFIXES.get(manufacturer, [manufacturer]):
            if text.startswith(prefix + " "):
                text = text[len(prefix) + 1 :]
                break
            if text == prefix:
                text = ""
                break
    text = _SEPARATOR_RE.sub("-", text)
    text = _COLLAPSE_RE.sub("-", text).strip("-")
    return text
