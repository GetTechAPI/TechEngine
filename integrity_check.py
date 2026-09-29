"""One-off data-integrity scan for all TechAPI categories (structural + benchmark anomaly).

Complements app/validate.py (schema) with: duplicate detection, slug/file match,
verified-without-source, name/tier vs core-count consistency, single>multi sanity,
era-vs-score outliers, and CROSS-SOURCE correlation outliers (the key wrong-variant
contamination detector). Read-only; prints flagged items for human review.

Usage::

    python integrity_check.py [DATA_ROOT] [--strict] [--hard-report PATH]

By default it prints every flagged item and exits 0 (human-review mode). With
``--strict`` it additionally exits non-zero when any *hard* anomaly is found —
unambiguous corruption that must block the weekly refresh PR: duplicate slugs,
slug/filename mismatches, and physically-impossible single>multi benchmarks.
The statistical cross-source/era outliers stay advisory (a heterogeneous catalog
of server + desktop + mobile parts legitimately produces many ratio outliers), so
they are printed for review but never fail the gate.
``--hard-report`` writes a JSON list of hard anomalies for baseline comparison.
"""
from __future__ import annotations
import os, json, math, re, statistics, sys
from app.categories import CATEGORIES

# Em-dash etc. in section headers must not crash on legacy consoles (e.g. cp949).
try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
except Exception:
    pass

_argv = sys.argv[1:]
STRICT = "--strict" in _argv
_report_index = _argv.index("--hard-report") if "--hard-report" in _argv else -1
HARD_REPORT = _argv[_report_index + 1] if _report_index >= 0 else None
if _report_index >= 0:
    del _argv[_report_index:_report_index + 2]
_positional = [a for a in _argv if not a.startswith("-")]
ROOT = _positional[0] if _positional else r"C:\Users\29\Desktop\TechAPI\data"

# Hard anomalies block the weekly gate under --strict; soft ones are review-only.
HARD: list[str] = []
def hard(msg: str) -> None:
    HARD.append(msg)
    print(msg)

# Era-vs-score: catch wrong-variant contamination (an old chip carrying a score
# that belongs to a newer part). The original rule used a flat per-chip ceiling
# (PassMark>1500 before 2006, R23>3000 before 2011) and ignored core/thread count.
# That mis-fires on legitimate pre-2011 high-core enthusiast parts: Cinebench R23
# and PassMark run on the physical silicon regardless of launch date, so a 6c/12t
# Gulftown (i7-980X/990X) genuinely posts ~6,000-6,500 R23 today and a 4c/8t
# Bloomfield (i7-920) ~3,000-3,800 — all above a flat 3,000 gate. The fix scales
# the ceiling by thread count: pre-2011 microarchitectures (Nehalem/Westmere/K10)
# top out around ~600 R23 and ~450 PassMark *per thread*, whereas a genuinely
# implausible "old chip, modern score" combo (e.g. a 2c/2009 part claiming 20,000
# R23 = 10,000/thread) sits far above the per-thread ceiling and still flags.
ERA_R23_PER_THREAD = 1000      # R23 multi per thread; pre-2011 real parts are ~400-600
ERA_PASSMARK_PER_THREAD = 900  # PassMark per thread; pre-2006 real parts are well under this

def era_score_outliers(rec: dict) -> list[str]:
    """Return era-vs-score finding messages for one CPU record (empty if none).

    Cores/threads-aware: the score ceiling scales with thread count so that
    high-core-for-their-era chips are not flagged, while a per-thread score that
    is implausible for the release era still is. Threads default to cores, then 1.
    """
    findings: list[str] = []
    year = (rec.get("release_date") or "0")[:4]
    threads = rec.get("threads") or rec.get("cores") or 1
    name = rec.get("name", "?")
    pm = rec.get("passmark_cpu_mark")
    r23 = rec.get("cinebench_r23_multi")
    if year < "2006" and pm and pm > ERA_PASSMARK_PER_THREAD * threads:
        findings.append(
            f"  {name!r} ({year}): passmark {pm} too high for era "
            f"({pm / threads:.0f}/thread over {ERA_PASSMARK_PER_THREAD}, {threads}T)"
        )
    if year < "2011" and r23 and r23 > ERA_R23_PER_THREAD * threads:
        findings.append(
            f"  {name!r} ({year}): r23 {r23} too high for era "
            f"({r23 / threads:.0f}/thread over {ERA_R23_PER_THREAD}, {threads}T)"
        )
    return findings

def load(comp):
    recs = []
    for dp, _, fs in os.walk(os.path.join(ROOT, comp)):
        for fn in fs:
            if fn.endswith(".json") and not fn.startswith("_"):
                p = os.path.join(dp, fn)
                recs.append((p, fn[:-5], json.load(open(p, encoding="utf-8"))))
    return recs

def mad_outliers(pairs, lo=0.34, hi=3.0):
    """pairs: list of (label, a, b); flag log(a/b) outliers via median±3*MAD."""
    rs = [(l, math.log(a / b)) for l, a, b in pairs if a and b]
    if len(rs) < 8:
        return []
    med = statistics.median(r for _, r in rs)
    mad = statistics.median(abs(r - med) for _, r in rs) or 1e-9
    return [(l, round(math.exp(r), 2)) for l, r in rs if abs(r - med) > 4 * mad]

def section(t): print(f"\n### {t}")

def collect(recs, fa, fb):
    return [(d["name"], d[fa], d[fb]) for p, fn, d in recs if d.get(fa) and d.get(fb)]

def main() -> None:
    records = {category: load(category) for category in CATEGORIES}
    cpus = records["cpu"]; gpus = records["gpu"]
    print(f"scope: {len(CATEGORIES)}/12 categories ? {sum(map(len, records.values()))} records")
    print(f"loaded CPU={len(cpus)} GPU={len(gpus)}")

    # --- 1. duplicates + slug/file + verified-no-source ---
    section("structural")
    for comp, recs in records.items():
        slugs, names = {}, {}
        for p, fn, d in recs:
            if d.get("verified") is True and not d.get("source_urls"):
                hard(f"  [{comp}] verified without sources: {fn}")
            slugs.setdefault(d.get("slug"), []).append(fn)
            names.setdefault(d.get("name"), []).append(fn)
            if d.get("slug") != fn:
                hard(f"  [{comp}] slug!=file: {fn} slug={d.get('slug')}")
        for s, fl in slugs.items():
            if len(fl) > 1: hard(f"  [{comp}] DUP slug {s}: {sorted(fl)}")
        for n, fl in names.items():
            if comp in ("cpu", "gpu") and len(fl) > 1: hard(f"  [{comp}] DUP name {n!r}: {sorted(fl)}")

    # --- 2. AMD Ryzen line vs DESKTOP model tier-digit (2nd digit); APU/mobile excepted ---
    section("CPU name/tier consistency (desktop mainstream only)")
    TIERMAP = {"6": "5", "7": "7", "8": "7", "9": "9"}  # 2nd model digit -> expected line
    for p, fn, d in cpus:
        n = d.get("name", "")
        # mainstream desktop: 4-digit model, no G/U/H/HS/HX (APU/mobile) suffix
        m = re.match(r"AMD Ryzen (\d) (\d)(\d)\d\d(X3D|X|XT)?$", n)
        if m:
            line, _gen, tier = m.group(1), m.group(2), m.group(3)
            exp = TIERMAP.get(tier)
            if exp and exp != line:
                print(f"  [tier] {n!r}: line Ryzen {line} but tier-digit {tier} → expect Ryzen {exp}")

    # --- 3. benchmark sanity: single>multi (consistent-scale benches) ---
    section("CPU single>multi (cinebench/geekbench — should be multi>=single)")
    for p, fn, d in cpus:
        for s, mu in [("cinebench_r23_single","cinebench_r23_multi"),
                      ("geekbench_single","geekbench_multi"),
                      ("cinebench_2024_single","cinebench_2024_multi")]:
            a, b = d.get(s), d.get(mu)
            if a and b and a > b and (d.get("threads") or 1) > 1:
                hard(f"  {d['name']!r}: {s}={a} > {mu}={b}")

    # --- 4. era vs score (catch wrong-variant: old chip w/ modern score) ---
    section("CPU era-vs-score outliers")
    for p, fn, d in cpus:
        for msg in era_score_outliers(d):
            print(msg)

    # --- 5. cross-source correlation outliers (KEY contamination detector) ---
    section("CPU cross-source ratio outliers (possible wrong-variant)")
    for fa, fb in [("passmark_cpu_mark","cinebench_r23_multi"),
                   ("passmark_cpu_mark","geekbench_multi"),
                   ("cinebench_r23_multi","geekbench_multi"),
                   ("cinebench_2024_multi","cinebench_r23_multi")]:
        out = mad_outliers(collect(cpus, fa, fb))
        for label, ratio in out:
            print(f"  [{fa}/{fb}] {label!r}: ratio={ratio}")

    # --- 6. GPU cross-source + sanity ---
    section("GPU cross-source ratio outliers + sanity")
    for fa, fb in [("passmark_g3d_mark","timespy_score"),
                   ("timespy_score","blender_score"),
                   ("fp32_tflops","timespy_score"),
                   ("passmark_g3d_mark","fp32_tflops")]:
        for label, ratio in mad_outliers(collect(gpus, fa, fb)):
            print(f"  [{fa}/{fb}] {label!r}: ratio={ratio}")

    print("\n(no lines under a section = clean)")

    if HARD_REPORT:
        with open(HARD_REPORT, "w", encoding="utf-8") as report:
            json.dump(sorted(set(HARD)), report, ensure_ascii=False, indent=2)

    if STRICT and HARD:
        print(f"\n❌ integrity gate: {len(HARD)} hard anomaly(ies) — blocking refresh.")
        sys.exit(1)
    if STRICT:
        print("\n✅ integrity gate: no hard anomalies.")


if __name__ == "__main__":
    main()
