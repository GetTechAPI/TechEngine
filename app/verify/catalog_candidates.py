"""Gate new-device candidates (Google Play rows) before they become ``device_catalog`` entries.

A candidate is ``{brand, name, models, codenames}`` plus optional Play Console fields
(``form_factor``, ``soc_raw``, ``screen``, ``sdk_min``). Each one runs through staged checks
and comes out ``accept`` or ``hold`` with the reasons. Nothing is dropped: held rows stay in
the review queue (ADR-016).

Stages, cheapest first:

* ``junk``   — not a phone/tablet/watch: TV/panel SoCs, 4K screens, TV/box/POS names and
  codenames, and *vendor mix* — a brand whose Play Console rows are mostly non-mobile
  (Hisense, Landi…) cannot vouch for a row that has no Console row of its own.
* ``seller`` — the Play brand is the manufacturer or distributor, not the seller. Measured
  per brand: if many of its rows name *another* known brand (Foxconn ships "Kogan…",
  Brightstar ships "Alcatel…"), the brand is an ODM and its rows are held. A row that
  names exactly one sibling brand (TCL under alcatel, OnePlus under oppo) is refiled.
* ``label``  — the name is not a marketing name: market suffix (``WP33_Pro_EEA`` → the clean
  name when ``models`` has it), codename echo (``acer_A12P2``), non-Latin script, and for
  major brands a bare model code.
* ``dup``    — the device already exists: exact model number / codename in any record,
  top level or ``variant``, any brand; region-normalised ids; the name's tokens all inside
  one of the brand's slugs; major brands also by numeric stem and by age (old rows map to
  pre-id imports the id join cannot see).
* ``batch``  — two candidates share a normalised model number; the first one wins.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from .common import Record

MAJOR = frozenset({
    "samsung", "xiaomi", "huawei", "honor", "oppo", "vivo", "realme", "oneplus", "motorola",
    "lg", "sony", "zte", "lenovo", "alcatel", "tcl", "nokia", "hmd", "asus", "google", "htc",
    "tecno", "infinix", "itel", "meizu", "sharp", "kyocera", "acer", "blackview", "doogee",
    "umidigi", "oukitel", "nubia", "redmi", "poco", "iqoo", "panasonic",
})
# Brands that share devices or file each other's devices on Play.
SIBLINGS: dict[str, frozenset[str]] = {
    "alcatel": frozenset({"tcl"}), "tcl": frozenset({"alcatel"}),
    "huawei": frozenset({"honor"}), "honor": frozenset({"huawei"}),
    "xiaomi": frozenset({"redmi", "poco", "blackshark"}), "redmi": frozenset({"xiaomi"}),
    "poco": frozenset({"xiaomi"}), "vivo": frozenset({"iqoo", "jovi"}),
    "iqoo": frozenset({"vivo"}), "jovi": frozenset({"vivo"}),
    "oppo": frozenset({"realme", "oneplus"}), "realme": frozenset({"oppo"}),
    "oneplus": frozenset({"oppo"}), "zte": frozenset({"nubia", "redmagic"}),
    "nubia": frozenset({"zte", "redmagic"}), "nokia": frozenset({"hmd"}),
    "hmd": frozenset({"nokia"}), "meizu": frozenset({"lynkco"}),
}
JUNK_VENDORS = frozenset({
    "pax", "datecs", "imin", "zkteco", "avocor", "kaon", "kaonmedia", "czur", "via-tech",
    "horizon", "vios", "prestigio-solutions", "lango", "verifone", "kandao", "idemia", "fbc",
    "jimi", "i3-technologies", "i3connect", "onescreen", "clevertouch", "maxhub", "benq",
    "innocn", "zaikai", "apolosign", "landi", "newland", "changhong", "dangbei", "gobox",
    "linxdot", "nautilus", "micropos", "telpo", "sunmi", "urovo", "castles",
})
ODM_VENDORS = frozenset({
    "foxconn", "compal", "anydata", "abocom", "brightstar", "dbm-maroc", "cellon", "enspert",
    "hon-hai-precision-industry-co-ltd", "wingtech", "huaqin", "longcheer", "tinno", "coosea",
    "lechpol",
})
MOBILE_FORM_FACTORS = frozenset({"Phone", "Tablet", "Wearable"})
JUNK_SOC = re.compile(r"RK3588|Amlogic|AMLA311|A311D|MT8195|MStar|Realtek RTD|MSD6|MT96\d\d", re.I)
JUNK_NAME = re.compile(
    r"^(LED\d|LE\d\d|LCD|LC-|HITV|DV\d|IFPD|TH-\d)|4K|\btv\b|\bbox\b|\bstb\b|set.?top|dongle|"
    r"translat|dvd|intelliboard|whiteboard|display|signage|kiosk|\bpos\b|terminal|scanner|"
    r"printer|projector|treadmill|\bbike\b|miner|\bvr\b|headset|mirage|vidaa|theater|"
    r"\bcar\b|automotive|dashcam|\bops\b|meeting|walkman|chromebook|laptop",
    re.I,
)
JUNK_CODENAME = re.compile(r"^(rk3588.*|oversea_v|ifpd.*|.*_tv|atv.*|.*stb.*|.*dongle.*)$", re.I)
MARKET = re.compile(r"(?i)[_ ](EEA|EU|ROW|NEU|RU|TUR|GL|US|LATAM|IN|GLOBAL)(?=_|$)")
REGION_ID = re.compile(r"(?i)[_\- ](EEA|ROW|US|EU|RU|TR|UK|IN|GLOBAL|ARG|NEU|LATAM)$")
NOISE = frozenset({
    "dual", "sim", "ds", "global", "version", "edition", "the", "mobile", "smartphone",
    "phone", "wifi", "wi", "fi", "lte", "4g", "5g", "3g", "nfc", "plus",
})
ODM_SHARE = 0.2   # share of a brand's rows naming another brand that marks it an ODM
MOBILE_SHARE = 0.8  # share of a brand's Console rows that must be mobile to vouch for the rest
OLD_SDK = 25  # Android 7.1: major-brand rows this old predate stored model numbers


def compact(s: str | None) -> str:
    return re.sub(r"[^A-Z0-9]", "", (s or "").upper())


def words(s: str | None) -> list[str]:
    return [w for w in re.split(r"[\W_]+", (s or "").lower().replace("+", " plus ")) if w]


@dataclass
class Candidate:
    brand: str
    name: str
    models: list[str]
    codenames: list[str]
    form_factor: str | None = None
    soc_raw: str | None = None
    screen: str | None = None
    sdk_min: int | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> Candidate:
        known = {"brand", "name", "models", "codenames", "all_models", "all_codenames",
                 "form_factor", "soc_raw", "screen", "sdk_min"}
        return cls(
            brand=str(row["brand"]), name=str(row["name"]),
            models=list(row.get("all_models") or row.get("models") or []),
            codenames=list(row.get("all_codenames") or row.get("codenames") or []),
            form_factor=row.get("form_factor") or None, soc_raw=row.get("soc_raw"),
            screen=row.get("screen"), sdk_min=row.get("sdk_min"),
            extra={k: v for k, v in row.items() if k not in known},
        )


@dataclass
class Decision:
    candidate: Candidate
    accept: bool
    reasons: list[str]

    def as_row(self) -> dict[str, Any]:
        c = self.candidate
        return {**c.extra, "brand": c.brand, "name": c.name, "all_models": c.models,
                "all_codenames": c.codenames, "form_factor": c.form_factor,
                "soc_raw": c.soc_raw, "screen": c.screen, "sdk_min": c.sdk_min,
                "decision": "accept" if self.accept else "hold", "reasons": self.reasons}


class Index:
    """What the dataset already holds, keyed the ways a Play row can collide with it."""

    def __init__(self, records: Iterable[Record], brand_slugs: Iterable[str]) -> None:
        self.brands = set(brand_slugs)
        self.brand_words = {b for b in self.brands if len(b) >= 4 and "-" not in b}
        self.ids: dict[str, str] = {}  # compact id -> path, any brand
        self.brand_ids: dict[str, set[str]] = defaultdict(set)  # region-stripped
        self.slugs: dict[str, set[str]] = defaultdict(set)
        for r in records:
            brand = str(r.data.get("brand") or "")
            for slug in (r.slug, r.data.get("base_model_slug")):
                if isinstance(slug, str) and slug:
                    self.slugs[brand].add(slug)
            raw_variant = r.data.get("variant")
            variant: dict[str, Any] = raw_variant if isinstance(raw_variant, dict) else {}
            for key in ("model_numbers", "codenames"):
                for src in (r.data.get(key), variant.get(key)):
                    for i in src if isinstance(src, list) else []:
                        if isinstance(i, str) and i.strip():
                            self.ids.setdefault(compact(i), r.path)
                            self.brand_ids[brand].add(compact(REGION_ID.sub("", i.strip())))

    def family(self, brand: str) -> set[str]:
        return {brand} | set(SIBLINGS.get(brand, ()))


def brand_profiles(rows: Iterable[Candidate], index: Index) -> dict[str, dict[str, float]]:
    """Per-brand shares measured on *all* Play rows of that brand (not only candidates)."""
    total: Counter[str] = Counter()
    foreign: Counter[str] = Counter()
    console: Counter[str] = Counter()
    mobile: Counter[str] = Counter()
    for c in rows:
        total[c.brand] += 1
        named = {w for x in [c.name, *c.models] for w in words(x) if w in index.brand_words}
        if named - index.family(c.brand) - set(c.brand.split("-")):
            foreign[c.brand] += 1
        if c.form_factor is not None:
            console[c.brand] += 1
            mobile[c.brand] += c.form_factor in MOBILE_FORM_FACTORS
    return {
        b: {"foreign": foreign[b] / total[b],
            "mobile": (mobile[b] / console[b]) if console[b] else 1.0,
            "console_rows": float(console[b])}
        for b in total
    }


def _clean_name(c: Candidate) -> bool:
    """Swap a market-suffixed label for the clean name ``models`` carries; False if none."""
    if not MARKET.search(c.name):
        return True
    clean = MARKET.sub("", c.name).replace("_", " ").strip()
    hit = [m for m in c.models if compact(m) == compact(clean)]
    if hit:
        c.name = hit[0]
        return True
    return False


def _refile(c: Candidate, index: Index) -> str | None:
    """Return the sibling brand a row really belongs to (TCL under alcatel), if exactly one."""
    named = {w for w in words(c.name) if w in index.brands} - {c.brand}
    sib = named & set(SIBLINGS.get(c.brand, ()))
    return next(iter(sib)) if len(sib) == 1 and named == sib else None


def _junk(c: Candidate, prof: dict[str, float]) -> str | None:
    if c.brand in JUNK_VENDORS:
        return "junk:vendor"
    if c.form_factor is not None and c.form_factor not in MOBILE_FORM_FACTORS:
        return "junk:form_factor"
    if JUNK_SOC.search(c.soc_raw or "") or c.screen in ("2160x3840", "3840x2160"):
        return "junk:soc_or_screen"
    if any(JUNK_NAME.search(x) for x in [c.name, *c.models]):
        return "junk:name"
    if any(JUNK_CODENAME.match(x) for x in c.codenames):
        return "junk:codename"
    if c.form_factor is None and prof["mobile"] < MOBILE_SHARE:
        return "junk:vendor_mix"
    return None


def _seller(c: Candidate, prof: dict[str, float], index: Index) -> str | None:
    if c.brand in ODM_VENDORS:
        return "seller:odm_vendor"
    if prof["foreign"] >= ODM_SHARE:
        return "seller:odm_share"
    named = {w for x in [c.name, *c.models] for w in words(x) if w in index.brand_words}
    if named - index.family(c.brand) - set(c.brand.split("-")):
        return "seller:names_other_brand"
    return None


def _label(c: Candidate) -> str | None:
    if not _clean_name(c):
        return "label:market_suffix"
    if re.search(r"[^\x00-\x7f]", c.name):
        return "label:non_latin"
    if any(x.lower() in (f"{c.brand}_{c.name}".lower(), c.name.lower())
           and not re.search(r"[a-z].*\s", c.name) and c.brand in MAJOR for x in c.codenames):
        return "label:codename"
    code = r"(?i)(\w+ )?[A-Z]{0,4}[_-]?\d{3,5}[A-Za-z0-9_-]*"
    if c.brand in MAJOR and re.fullmatch(code, c.name):
        return "label:model_code"
    if " / " in c.name or re.search(r"(?i)[a-z]{3,}_[a-z0-9]+_", c.name):
        return "label:internal"
    return None


def _dup(c: Candidate, index: Index) -> str | None:
    fam = index.family(c.brand)
    for x in [*c.models, *c.codenames]:
        k = compact(x)
        if len(k) >= 4 and re.search(r"\d", k) and k in index.ids:
            return "dup:id"
        if any(compact(REGION_ID.sub("", x.strip())) in index.brand_ids[b] for b in fam):
            return "dup:region_id"
    toks = [t for t in words(re.sub(r"\([^)]*\)", " ", c.name))
            if t not in NOISE and t not in set(c.brand.split("-"))]
    if toks:
        for b in fam:
            for slug in index.slugs.get(b, ()):
                parts = set(slug.split("-"))
                if all(t in parts for t in toks):
                    return "dup:name_in_slug"
    if c.brand in MAJOR:
        if c.sdk_min is not None and c.sdk_min <= OLD_SDK:
            return "dup:old_major"
        stems = {re.sub(r"[A-Z]+$", "", t) for x in [c.name, *c.models]
                 for t in re.split(r"[\W_]+", x.upper()) if len(t) >= 3 and re.search(r"\d", t)}
        stems = {s for s in stems if len(s) >= 3}
        for b in fam:
            for slug in index.slugs.get(b, ()):
                if stems & {re.sub(r"[A-Z]+$", "", t.upper()) for t in slug.split("-")}:
                    return "dup:numeric_stem"
    return None


def gate(candidates: list[Candidate], all_rows: list[Candidate], index: Index) -> list[Decision]:
    """Run every stage; a candidate is accepted only if no stage objects."""
    profiles = brand_profiles(all_rows, index)
    seen: set[str] = set()
    out: list[Decision] = []
    for c in candidates:
        moved = _refile(c, index)
        if moved:
            c.extra["refiled_from"], c.brand = c.brand, moved
        prof = profiles.get(c.brand, {"foreign": 0.0, "mobile": 1.0, "console_rows": 0.0})
        reasons = [r for r in (_junk(c, prof), _seller(c, prof, index), _label(c), _dup(c, index))
                   if r]
        keys = {compact(REGION_ID.sub("", m.strip())) for m in c.models if len(compact(m)) >= 4}
        if not reasons and keys & seen:
            reasons.append("batch:same_model")
        if not reasons:
            seen |= keys
        out.append(Decision(c, not reasons, reasons))
    return out
