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
they are printed for review but never fail the gate. The CPU cross-source ratio
check regresses out the core-count trend so it compares each part against the
ratio expected for its own core count instead of a desktop-dominated global
median (see mad_outliers).
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
# --only FILE: scope to the data-relative paths listed in FILE (one per line).
# Only the per-record hard checks (+ duplicate slug/name) run; the population-based
# advisory sections need the whole catalog and are skipped.
_only_index = _argv.index("--only") if "--only" in _argv else -1
ONLY_FILE = _argv[_only_index + 1] if _only_index >= 0 else None
if _only_index >= 0:
    del _argv[_only_index:_only_index + 2]
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

ONLY: set[str] | None = None
if ONLY_FILE:
    with open(ONLY_FILE, encoding="utf-8") as _f:
        ONLY = {ln.strip() for ln in _f if ln.strip()}


def load(comp, full=False):
    recs = []
    if ONLY is not None and not full:
        for rel in sorted(ONLY):
            p = os.path.join(ROOT, rel)
            if rel.startswith(comp + "/") and os.path.isfile(p) and not os.path.basename(p).startswith("_"):
                recs.append((p, os.path.basename(p)[:-5], json.load(open(p, encoding="utf-8"))))
        return recs
    for dp, _, fs in os.walk(os.path.join(ROOT, comp)):
        for fn in fs:
            if fn.endswith(".json") and not fn.startswith("_"):
                p = os.path.join(dp, fn)
                recs.append((p, fn[:-5], json.load(open(p, encoding="utf-8"))))
    return recs

# Cross-source ratio outliers: same wrong-variant detector as the era rule, but
# statistical. The original computed ONE global median±MAD over the whole CPU
# catalog for ratios like cinebench_r23_multi/geekbench_multi. That ratio is not
# scale-free across the catalog — it is confounded by core/thread count, because
# the two benchmarks scale differently with parallelism: Cinebench R23 multi
# scales near-linearly with cores while Geekbench multicore compresses at high
# core counts, so the R23/GB ratio climbs monotonically with thread count
# (measured on live data: ~1.05 at 1-4T rising to ~1.48 at 65T+, Pearson
# corr(threads, log-ratio) ≈ +0.52; the PassMark/R23 ratio falls with threads,
# corr ≈ -0.59). A single global median therefore flags entire legitimate
# core-count strata — every many-core EPYC/Threadripper/Xeon and, at the other
# end, the low-core parts — as "contamination" (90 of 739 R23/GB pairs, median
# 56 threads vs 16 overall, all with genuine scores). That is the same shape of
# bug as the flat era ceiling: a fixed reference blind to a variable that
# legitimately shifts what "normal" looks like.
#
# Fix (mirrors PR #111's cores-aware era rule, which divided the score by thread
# count): regress the confounder out. When a per-part covariate is supplied we
# fit a robust (Theil–Sen) line of log-ratio against log(covariate) and run the
# median±MAD test on the *residuals*, so a part is measured against the ratio
# expected for its own core count rather than a desktop-dominated global median.
# The systematic core-count gradient no longer flags; a part whose ratio is
# anomalous for its own class — the real wrong-variant signal — still does
# (live R23/GB flags drop 90 → 10, and the survivors are genuine per-class
# outliers). Coarse banding was rejected: it removes the between-band trend but
# shrinks the within-band MAD envelope, netting *more* false positives.
# Callers with no meaningful covariate (e.g. GPUs) omit it and get the original
# single-population behaviour unchanged.
def _theil_sen(xs: list[float], ys: list[float]) -> tuple[float, float]:
    """Robust slope/intercept via median of pairwise slopes (Theil–Sen)."""
    slopes = [
        (ys[j] - ys[i]) / (xs[j] - xs[i])
        for i in range(len(xs)) for j in range(i + 1, len(xs))
        if xs[j] != xs[i]
    ]
    slope = statistics.median(slopes) if slopes else 0.0
    intercept = statistics.median(y - slope * x for x, y in zip(xs, ys, strict=True))
    return slope, intercept

def mad_outliers(pairs):
    """Flag log(a/b) outliers via median±4*MAD.

    ``pairs`` is a list of ``(label, a, b)`` or ``(label, a, b, covariate)``.
    With a positive per-part ``covariate`` (e.g. thread count) the log-ratio is
    first detrended against ``log(covariate)`` with a robust Theil–Sen fit and
    the outlier test runs on the residuals, so a variable that legitimately
    shifts the ratio does not turn a whole stratum into false positives. Fewer
    than 8 usable points returns nothing — too few to estimate a robust
    median/MAD. Omitting the covariate reproduces the original global test.
    """
    rows: list[tuple[str, float, float | None]] = []
    for item in pairs:
        label, a, b = item[0], item[1], item[2]
        cov = item[3] if len(item) > 3 else None
        if a and b:
            rows.append((label, math.log(a / b), cov))
    if len(rows) < 8:
        return []
    ys = [r for _, r, _ in rows]
    xs = [math.log(c) for _, _, c in rows if c and c > 0]
    if len(xs) == len(rows) and len({round(x, 9) for x in xs}) > 1:
        slope, intercept = _theil_sen(xs, ys)
        scores = [y - (slope * x + intercept) for x, y in zip(xs, ys, strict=True)]
    else:
        scores = ys  # no usable covariate -> original global behaviour
    med = statistics.median(scores)
    mad = statistics.median(abs(s - med) for s in scores) or 1e-9
    return [
        (rows[i][0], round(math.exp(ys[i]), 2))
        for i in range(len(rows)) if abs(scores[i] - med) > 4 * mad
    ]

def section(t): print(f"\n### {t}")

def collect(recs, fa, fb):
    return [(d["name"], d[fa], d[fb]) for p, fn, d in recs if d.get(fa) and d.get(fb)]

def collect_cpu(recs, fa, fb):
    """Like ``collect`` but tags each pair with its thread-count covariate."""
    return [(d["name"], d[fa], d[fb], d.get("threads") or d.get("cores") or 1)
            for p, fn, d in recs if d.get(fa) and d.get(fb)]

def main() -> None:
    records = {category: load(category) for category in CATEGORIES}
    cpus = records["cpu"]; gpus = records["gpu"]
    scoped = ONLY is not None
    ncat = len(CATEGORIES)
    print(f"scope: {ncat}/{ncat} categories ? {sum(map(len, records.values()))} records")
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
        if scoped and recs:
            # Changed records vs the rest of the catalog. File names equal slugs by
            # convention, so listing names finds a slug clash without parsing.
            changed_fn = {fn for _, fn, _ in recs}
            stems = {}
            for dp, _, fs in os.walk(os.path.join(ROOT, comp)):
                for f in fs:
                    if f.endswith(".json") and not f.startswith("_"):
                        stems.setdefault(f[:-5], []).append(os.path.join(dp, f))
            for s, fl in slugs.items():
                if len(stems.get(s, [])) > len(fl):
                    hard(f"  [{comp}] DUP slug {s}: {sorted(os.path.basename(x) for x in stems[s])}")
            if comp in ("cpu", "gpu") and names:
                known = {}
                for p, fn, d in load(comp, full=True):
                    known.setdefault(d.get("name"), []).append(fn)
                for n in names:
                    if len(known.get(n, [])) > len(names[n]):
                        hard(f"  [{comp}] DUP name {n!r}: {sorted(known[n])}")

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
    # Thread-count-aware (see mad_outliers): the ratio between two CPU benchmarks
    # is confounded by core count, so the core-count trend is regressed out and
    # each part is judged against the ratio expected for its own core count
    # rather than a desktop-dominated global median.
    section("CPU cross-source ratio outliers (possible wrong-variant)")
    for fa, fb in [] if scoped else [("passmark_cpu_mark","cinebench_r23_multi"),
                   ("passmark_cpu_mark","geekbench_multi"),
                   ("cinebench_r23_multi","geekbench_multi"),
                   ("cinebench_2024_multi","cinebench_r23_multi")]:
        out = mad_outliers(collect_cpu(cpus, fa, fb))
        for label, ratio in out:
            print(f"  [{fa}/{fb}] {label!r}: ratio={ratio}")

    # --- 6. GPU cross-source + sanity ---
    # Left as a single population on purpose: unlike the CPU thread-count
    # confounder, the GPU ratios mix a theoretical spec (fp32_tflops) with
    # empirical benchmarks across gaming vs. compute cards and many hardware
    # eras, so there is no single clean stratifying variable. These stay
    # advisory-only and are surfaced for human review rather than gated.
    section("GPU cross-source ratio outliers + sanity")
    for fa, fb in [] if scoped else [("passmark_g3d_mark","timespy_score"),
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
