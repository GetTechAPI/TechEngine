"""Conservative GPU identity checks for additions-only ingest.

One board is often listed under several names: ``GeForce3 Ti500`` and
``GeForce3 Ti 500``, ``RTX 6000 Ada Generation`` and ``RTX 6000 Ada``, a laptop
part with and without ``Mobile``, one card per memory size. A candidate that
matches a curated board under any of those spellings is skipped, never added
as a second record: a missed addition is cheap, a duplicate is not.
"""

from __future__ import annotations

import re

# Words that name a variant of a board rather than a different board.
_QUALIFIERS = frozenset({
    "agp", "pci", "pcie",  # bus
    "generation", "edition",  # "Ada Generation", "Workstation Edition"
    "mobile", "laptop", "notebook",  # laptop parts; Max-Q is its own power tier
    "engineering", "sample",
})
_MEMORY_RE = re.compile(r"\d+(?:gb|mb)")
# Turing and later workstation boards dropped the Quadro brand ("Quadro T500" is "T500").
_QUADRO_RENAMED_RE = re.compile(r"(?:t|a|rtx)\d*")


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
    """Identity with variant words dropped: bus, memory size, revision, laptop
    markers, "Generation"/"(Workstation) Edition" and the retired Quadro brand."""
    tokens = _tokens(slug)
    if len(tokens) > 1 and tokens[0] == "quadro" and _QUADRO_RENAMED_RE.fullmatch(tokens[1]):
        tokens = tokens[1:]
    kept = [
        token
        for i, token in enumerate(tokens)
        if token not in _QUALIFIERS
        and not _MEMORY_RE.fullmatch(token)
        and not re.fullmatch(r"rev\d*", token)
        # "RTX PRO 6000 Blackwell Workstation (Edition)" is the plain board.
        and not (token == "workstation" and tokens[i + 1 : i + 2] in ([], ["edition"]))
    ]
    return "".join(kept)


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
