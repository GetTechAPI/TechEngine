"""One-off data-integrity scan for TechAPI CPU+GPU (structural + benchmark anomaly).

Complements app/validate.py (schema) with: duplicate detection, slug/file match,
verified-without-source, name/tier vs core-count consistency, single>multi sanity,
era-vs-score outliers, and CROSS-SOURCE correlation outliers (the key wrong-variant
contamination detector). Read-only; prints flagged items for human review.

Usage::

    python integrity_check.py [DATA_ROOT] [--strict]

By default it prints every flagged item and exits 0 (human-review mode). With
``--strict`` it additionally exits non-zero when any *hard* anomaly is found —
unambiguous corruption that must block the weekly refresh PR: duplicate slugs,
slug/filename mismatches, and physically-impossible single>multi benchmarks.
The statistical cross-source/era outliers stay advisory (a heterogeneous catalog
of server + desktop + mobile parts legitimately produces many ratio outliers), so
they are printed for review but never fail the gate.
"""
from __future__ import annotations
import os, json, math, re, statistics, sys

# Em-dash etc. in section headers must not crash on legacy consoles (e.g. cp949).
try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
except Exception:
    pass

_argv = sys.argv[1:]
STRICT = "--strict" in _argv
_positional = [a for a in _argv if not a.startswith("-")]
ROOT = _positional[0] if _positional else r"C:\Users\29\Desktop\TechAPI\data"

# Hard anomalies block the weekly gate under --strict; soft ones are review-only.
HARD: list[str] = []
def hard(msg: str) -> None:
    HARD.append(msg)
    print(msg)

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

cpus = load("cpu"); gpus = load("gpu")
print(f"loaded CPU={len(cpus)} GPU={len(gpus)}")

# --- 1. duplicates + slug/file + verified-no-source ---
section("structural")
for comp, recs in (("cpu", cpus), ("gpu", gpus)):
    slugs, names = {}, {}
    for p, fn, d in recs:
        slugs.setdefault(d.get("slug"), []).append(fn)
        names.setdefault(d.get("name"), []).append(fn)
        if d.get("slug") != fn:
            hard(f"  [{comp}] slug!=file: {fn} slug={d.get('slug')}")
    for s, fl in slugs.items():
        if len(fl) > 1: hard(f"  [{comp}] DUP slug {s}: {fl}")
    for n, fl in names.items():
        if len(fl) > 1: hard(f"  [{comp}] DUP name {n!r}: {fl}")

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
    y = (d.get("release_date") or "0")[:4]
    pm = d.get("passmark_cpu_mark"); r23 = d.get("cinebench_r23_multi")
    if y < "2006" and pm and pm > 1500:
        print(f"  {d['name']!r} ({y}): passmark {pm} too high for era")
    if y < "2011" and r23 and r23 > 3000:
        print(f"  {d['name']!r} ({y}): r23 {r23} too high for era")

# --- 5. cross-source correlation outliers (KEY contamination detector) ---
section("CPU cross-source ratio outliers (possible wrong-variant)")
def collect(recs, fa, fb):
    return [(d["name"], d[fa], d[fb]) for p, fn, d in recs if d.get(fa) and d.get(fb)]
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

if STRICT and HARD:
    print(f"\n❌ integrity gate: {len(HARD)} hard anomaly(ies) — blocking refresh.")
    sys.exit(1)
if STRICT:
    print("\n✅ integrity gate: no hard anomalies.")
