"""Name checks for SoC records.

``is_placeholder(name)`` tells whether a name describes no particular chip
("Nokia mobile platform 2010", "Quad-core 1.2 GHz", "Qualcomm Adreno 305").
``soc_key(name)`` gives the name under which a chip is counted once: two records
with the same key are the same chip.

Standard library only: ``app.validate`` and ``integrity_check.py`` import this,
and TechAPI's PR checks run both without installing the engine.
"""

from __future__ import annotations

import re

_CORES = r"(?:single|dual|triple|quad|hexa|octa|deca)[- ]core"
_CLOCK = r"(?:[\d.]+ ?/ ?)*[\d.]+ ?(?:mhz|ghz)"
_CPU_CORE = (r"(?:co?r?tex[- ]a\d+(?: mpcore)?|arm ?(?:9|11)\w*(?:-s)?|armv\d"
             r"|scorpion|krait(?: \d+)?|kryo(?: \d+)?)")
_GPU = r"(?:adreno|mali|powervr|hd graphics|vivante|ati graphic|geforce)"
# Vendor and family words that name no chip on their own.
_GENERIC = (r"\b(?:qualcomm|snapdragon|mediatek|helio|samsung|exynos|intel|atom|xscale"
            r"|nvidia|tegra|spreadtrum|unisoc|marvell|freescale|ti|arm|dual|processor|or|n/a)\b")
# Two chips written into one name, e.g. "Mediatek MT6750Qualcomm MSM8917 Snapdragon 425".
_GLUED = (r"(?<=[A-Za-z0-9+])(?:Qualcomm|Mediatek|MediaTek|Spreadtrum|Samsung|Exynos|Intel"
          r"|Marvell|Broadcom|HiSilicon|Quad-core|Octa-core|Dual-core)\b"
          r"| / Intel | or Z\d| or MT\d")
# Cyrillic capitals that look like Latin ones turn up in scraped part numbers.
_CYRILLIC = str.maketrans("АВЕМСТ", "ABEMCT")

# Part numbers written next to a marketing name; they are not part of the key.
_PART_NUMBERS = [
    r"sm\d{4}(?:-\w+)*", r"sd[ma]\d{3}\w*", r"qsd\d{4}\w*", r"qm\d{3}",
    r"(?:msm|apq)\d[\dx]{3}\w*(?:-\w+)*(?:[ -](?:pro|lite)\b)?",
    r"mt\d{4}\w*(?:/\w+)?", r"ums\d+\w*", r"hi\d\w{3,}", r"gfcv\d+", r"v\dr\d",
    r"apl\w{4}", r"s5[epl]\w{4,6}", r"s5pc?\d{3,4}", r"gs\d{3}",
]
_MARKETING = r"snapdragon|helio|dimensity|kirin|exynos|tensor|bionic|fusion|tegra|\bapple a\d|k3v2"
_NOISE = (r"\b(?:qualcomm|mediatek|media tek|samsung|apple|hisilicon|honor|huawei|unisoc|spreadtrum"
          r"|google|nvidia|intel|texas instruments|ti|marvell|rockchip|broadcom|allwinner|amlogic"
          r"|st[- ]ericsson|stmicroelectronics|philips|nec|motorola|freescale|xscale|armada"
          r"|5g|4g|lte|soc|chipset|mobile platform|processor|single|dual|quad|hexa|octa|deca|core"
          r"|tiger|for galaxy|32[- ]bit|64[- ]bit|\d(?:st|nd|rd|th) gen)\b")


def is_placeholder(name: str) -> bool:
    """True when the name is a platform label, a clock speed, a CPU core or a GPU, not a chip."""
    low = name.strip().lower()
    if re.search(r"mobile platform \d{4}$", low):
        return True
    if re.match(r"(?:qualcomm |arm |intel |nvidia )?" + _GPU, low):
        return True
    rest = re.sub(r"- [\w+ ]*?model", " ", low)
    for pattern in (_CORES, _CLOCK, _CPU_CORE, _GENERIC, r"\d+\.\d+"):
        rest = re.sub(pattern, " ", rest)
    # Nothing left once the generic words are gone, or only a stray digit ("0").
    rest = re.sub(r"[^a-z0-9]+", "", rest)
    return not rest or (rest.isdigit() and len(rest) < 3 and rest == re.sub(r"[^a-z0-9]+", "", low))


def _drop(pattern: str, text: str) -> str:
    """Blank out every whole-token match of ``pattern``."""
    return re.sub(r"(?<![a-z0-9])(?:" + pattern + r")(?![a-z0-9])", " ", text)


def _reduce(name: str) -> str | None:
    full = name.translate(_CYRILLIC).replace("®", "")
    full = re.sub(r"\((x\d\w?-\d+-\w+)\)", r"\1", full, flags=re.I)   # compute SKU codes stay
    n = re.sub(r"\(.*?\)", " ", full).split(",")[0]
    n = re.sub(r" - .*$", "", n).lower()                               # " - India model"

    # One spelling for old part numbers.
    n = re.sub(r"\bs(?:c3|3c) ?(\d{4})", r"s3c\1", n)                  # SC3 2442, S3C 2442
    n = re.sub(r"\bsc3(\d{4})", r"s3c\1", n)                           # SC32442
    n = re.sub(r"\bsamsung (24\d\d)\b", r"s3c\1", n)
    n = re.sub(r"\bpxa ?(\d{3})", r"pxa\1", n)
    n = re.sub(r"\bomap ?(\d)", r"omap \1", n)
    n = re.sub(r"\b(?:mtk|mt) ?(\d{4})", r"mt\1", n)
    if "spreadtrum" in n:
        n = re.sub(r"\b(\d{4}\w*)\b", r"sc\1", n)
    if "rockchip" in n:
        n = re.sub(r"\b(\d{4})\b", r"rk\1", n)

    n = _drop(_CLOCK, n)
    unisoc = re.search(r"unisoc|tiger|tanggula|spreadtrum", n)
    if unisoc:
        n = _drop(r"ums\d+\w*", n)
    marketing = re.search(_MARKETING, n)
    if marketing:
        patterns = list(_PART_NUMBERS)
        if not unisoc:
            patterns.append(r"sc\d{4}\w*")
        if "apple" in n:
            patterns += [r"t\d{4}\w?", r"s\d{4}\w?"]
        if "tegra" in n:
            patterns += [r"(?:ap|t)\d\d[a-z]?(?: s)?", r"sp3x", r"250"]
        for pattern in patterns:
            n = _drop(pattern, n)
        if "snapdragon" in n:
            n = _drop(r"v\d(?:[ .]\d)?", n)                            # chip revisions v1, v2.0
        n = re.sub(r"\bor\b", " ", n)                                  # "MSM8274 or MSM8974"
    n = re.sub(_NOISE, " ", n)
    n = re.sub(r"\s+", " ", n)
    n = re.sub(r"\bplus\b", "+", n)
    n = re.sub(r"kirin\s*(\d)", r"kirin \1", n)                        # KIRIN710
    n = re.sub(r"\bgen(\d)", r"gen \1", n)                             # Gen3
    n = re.sub(r"\bexynos \d (\d{3,4}\w?)\b", r"exynos \1", n)         # Exynos 7 Octa 7870
    n = re.sub(r"\s*\+", "+", n)
    n = re.sub(r"[^a-z0-9+]+" if marketing else r"[^a-z0-9+/-]+", " ", n).strip(" -/")
    if n == "kirin 990":                                               # the 4G and 5G chips differ
        n += " 5g" if re.search(r"\b5g\b", full.lower()) else " 4g"
    if not re.search(r"\d", n) or not (marketing or re.search(r"[a-z]", n)):
        return None
    return n


def soc_key(name: str) -> str | None:
    """Key shared by every spelling of one chip, or None when the name is not checked.

    Part numbers, clock speeds and vendor words next to a marketing name are left
    out ("Qualcomm SDM660 Snapdragon 660" and "Snapdragon 660" share a key);
    suffixes that name another chip stay (855 and 855+, 778G and 778G+). A chip
    without a marketing name keeps its part number as the key.
    """
    if is_placeholder(name) or re.search(_GLUED, name.translate(_CYRILLIC)[1:]):
        return None
    return _reduce(name)
