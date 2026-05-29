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
