"""Canonical seed categories and their public collection names."""

CATEGORIES: tuple[str, ...] = (
    "brand", "soc", "smartphone", "tablet", "watch", "pda", "gpu", "cpu",
    "laptop", "monitor", "software", "website",
)
COLLECTIONS = dict(zip(CATEGORIES, (
    "brands", "socs", "smartphones", "tablets", "watches", "pdas", "gpus", "cpus",
    "laptops", "monitors", "software", "websites",
), strict=True))
