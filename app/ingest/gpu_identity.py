"""Conservative GPU identity checks for additions-only ingest.

One board is often listed under several names: ``GeForce3 Ti500`` and
``GeForce3 Ti 500``, ``RTX 6000 Ada Generation`` and ``RTX 6000 Ada``, a laptop
part as ``Mobile`` or ``(Notebook)``, one card per memory size, an OEM card,
``Tesla C2070`` and ``C2070 GPU Computing Module``. A laptop part is never the
same board as the desktop card of the same name. A candidate that
matches a curated board under any of those spellings is skipped, never added
as a second record: a missed addition is cheap, a duplicate is not.
"""

from __future__ import annotations

import re

# Words that name a variant of a board rather than a different board.
_QUALIFIERS = frozenset({
    "agp", "pci", "pcie",  # bus
    "generation", "edition",  # "Ada Generation", "Workstation Edition"
    "engineering", "sample",
    "oem",
    "mxm", "mcm",  # embedded module packages: "E9172 MXM"
    "cooling",  # "Vega 64 Liquid Cooling"
})
# Laptop markers: kept in the family key as one tier word (Max-Q stays as is).
_LAPTOP_TIER = frozenset({"mobile", "laptop", "notebook"})
# "H100 GPU accelerator", "C2070 GPU Computing Module": data-center board names
# that the curated records file as "Tesla C2070" and "H100".
_DATACENTER_SUFFIX = frozenset({"gpu", "accelerator", "computing", "module"})
# Form-factor suffixes of data-center boards: Tesla K20c/K20m/K20X, H200 NVL/SXM.
_FORM_FACTORS = frozenset({"c", "d", "m", "s", "t", "x", "xm", "st", "nvl", "sxm", "pcie"})
_MEMORY_RE = re.compile(r"\d+(?:gb|mb)")
# Boards that dropped the Quadro brand: Turing and later ("Quadro T500" is "T500"),
# and the NVS line ("Quadro NVS 50" is "NVS 50").
_QUADRO_RENAMED_RE = re.compile(r"(?:t|a|rtx)\d*|nvs")


def _tokens(slug: str) -> list[str]:
    tokens = [token for token in slug.lower().split("-") if token]
    joined: list[str] = []
    for token in tokens:
        # Rejoin pairs the slugifier splits: "max-q", "e-s" (engineering sample),
        # "pci-e", "rev-2", "3-gb".
        if joined and (joined[-1], token) == ("e", "s"):
            joined[-1] = "engineering"
            continue
        if joined and (
            (joined[-1], token) in {("max", "q"), ("pci", "e")}
            or (joined[-1] == "rev" and token.isdigit())
            or (joined[-1].isdigit() and token in {"gb", "mb"})
        ):
            joined[-1] += token
            continue
        joined.append(token)
    return joined


def gpu_key(slug: str) -> str:
    """Spelling-insensitive identity: ``geforce3-ti500`` == ``geforce3-ti-500``."""
    return "".join(_tokens(slug))


def gpu_family_key(slug: str) -> str:
    """Identity with variant words dropped: bus, memory size, revision, OEM,
    "Generation"/"(Workstation) Edition", the retired Quadro, Tesla and Radeon
    Instinct brands and data-center suffixes. The laptop tier stays (``-notebook``
    is ``-mobile``) except on a Tesla, where it names the MXM module."""
    tokens = _tokens(slug)
    if len(tokens) > 1 and tokens[0] == "quadro" and _QUADRO_RENAMED_RE.fullmatch(tokens[1]):
        tokens = tokens[1:]
    # "Quadro4 200 NVS" is "NVS 200".
    if tokens[:1] == ["quadro4"] and tokens[-1:] == ["nvs"]:
        tokens = ["nvs", *tokens[1:-1]]
    # "X800 XT PE" is the X800 XT Platinum Edition.
    tokens = [
        "platinum" if token == "pe" and tokens[i - 1 : i] == ["xt"] else token
        for i, token in enumerate(tokens)
    ]
    # "Radeon Instinct MI100" became "Instinct MI100".
    if tokens[:2] == ["radeon", "instinct"]:
        tokens = tokens[1:]
    tesla = len(tokens) > 1 and tokens[0] == "tesla"
    if tesla:
        tokens = tokens[1:]
    while len(tokens) > 1 and tokens[-1] in _DATACENTER_SUFFIX:
        tokens.pop()
    kept = [
        token
        for i, token in enumerate(tokens)
        if token not in _QUALIFIERS
        and token not in _LAPTOP_TIER
        and not _MEMORY_RE.fullmatch(token)
        and not re.fullmatch(r"rev\d*", token)
        # "RTX PRO 6000 Blackwell Workstation (Edition)" is the plain board.
        and not (token == "workstation" and tokens[i + 1 : i + 2] in ([], ["edition"]))
    ]
    # The tier goes last: "RTX 500 Mobile Ada Generation" is "RTX 500 Ada Mobile".
    # A "Tesla M6 Mobile" is the MXM module of the M6, not a laptop chip.
    if not tesla and any(token in _LAPTOP_TIER for token in tokens):
        kept.append("mobile")
    return "".join(kept)


def datacenter_family(slug: str) -> str | None:
    """Family key of a data-center board name ("K20 GPU accelerator"), else ``None``."""
    tokens = _tokens(slug)
    if tokens[:1] == ["tesla"] or (tokens and tokens[-1] in _DATACENTER_SUFFIX):
        return gpu_family_key(slug)
    return None


def form_factor_of(key: str, family: str) -> bool:
    """Whether family key ``key`` is a form factor of ``family``: ``k20c`` of ``k20``."""
    return key.startswith(family) and key[len(family) :] in _FORM_FACTORS


def spec_twins(candidate: dict[str, object], existing: dict[str, object]) -> bool:
    """Two names where one extends the other (``HD 2350`` / ``HD 2350 PRO``) and
    the boards share core clock and TDP: likely the same board, so not a new one."""
    left = gpu_key(str(candidate.get("slug", "")))
    right = gpu_key(str(existing.get("slug", "")))
    short, long = sorted((left, right), key=len)
    if len(short) < 4 or short == long or not long.startswith(short):
        return False
    if not long[len(short)].isalpha():
        return False  # "hd235" vs "hd2350" is a different number, not a suffix
    clocks = candidate.get("base_clock_mhz"), existing.get("base_clock_mhz")
    tdps = candidate.get("tdp_w"), existing.get("tdp_w")
    return None not in clocks and None not in tdps and clocks[0] == clocks[1] and tdps[0] == tdps[1]
