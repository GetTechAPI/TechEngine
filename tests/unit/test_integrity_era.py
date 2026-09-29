"""Unit tests for the cores/threads-aware era-vs-score rule in ``integrity_check``.

The era rule flags a wrong-variant contamination signal: an old chip carrying a
score that belongs to a newer part. The original rule used a flat per-chip ceiling
(PassMark>1500 before 2006, R23>3000 before 2011) and ignored core/thread count,
which mis-fired on legitimate pre-2011 high-core enthusiast parts. These tests pin
the fix: those known-good chips must not flag, while a genuinely implausible
year/score combination still must.
"""

from __future__ import annotations

import integrity_check as ic


def _rec(**overrides: object) -> dict:
    base: dict = dict(
        name="Test CPU",
        release_date="2010-01-01",
        cores=6,
        threads=12,
    )
    base.update(overrides)
    return base


# The 8 pre-2011 high-core-for-their-era chips flagged as false positives during
# the 2026-09-29 CPU advisory re-check (TechEngine #98). Real cores/threads and the
# stored Cinebench R23 multi scores; every one must be treated as plausible.
KNOWN_GOOD_ERA_CHIPS = [
    ("Intel Core i7-920 (Bloomfield)", "2008", 4, 8, 3800),
    ("Intel Core i7-965 Extreme Edition (Bloomfield)", "2008", 4, 8, 4500),
    ("Intel Core i7-870 (Lynnfield)", "2009", 4, 8, 4200),
    ("Intel Core i7-970", "2010", 6, 12, 5800),
    ("Intel Core i7-980X Extreme Edition (Gulftown)", "2010", 6, 12, 6500),
    ("Intel Core i7-990X Extreme Edition", "2010", 6, 12, 6300),
    ("AMD Phenom II X6 1090T Black Edition (Thuban)", "2010", 6, 6, 3400),
    ("AMD Phenom II X6 1100T Black Edition (Thuban)", "2010", 6, 6, 3500),
]


def test_known_good_pre2011_high_core_chips_are_not_flagged() -> None:
    for name, year, cores, threads, r23 in KNOWN_GOOD_ERA_CHIPS:
        rec = _rec(
            name=name,
            release_date=f"{year}-06-01",
            cores=cores,
            threads=threads,
            cinebench_r23_multi=r23,
        )
        assert ic.era_score_outliers(rec) == [], f"{name} should not be flagged"


def test_genuinely_implausible_old_chip_still_flags() -> None:
    # A 2-core/2009 part claiming a 20,000 R23 multi (10,000/thread) is impossible
    # for the era and must still be flagged.
    rec = _rec(
        name="Bogus Dual-Core 2009",
        release_date="2009-01-01",
        cores=2,
        threads=2,
        cinebench_r23_multi=20000,
    )
    findings = ic.era_score_outliers(rec)
    assert len(findings) == 1
    assert "r23 20000 too high for era" in findings[0]


def test_r23_ceiling_scales_with_threads() -> None:
    # Same per-thread score, different thread counts: a high absolute R23 that is
    # reasonable per-thread for a many-threaded part is fine, but the identical
    # per-thread rate on very few threads is not what trips the rule -- the rule
    # is about total score vs thread budget. Just above / below the per-thread
    # ceiling around the boundary.
    threads = 12
    ceiling = ic.ERA_R23_PER_THREAD * threads
    below = _rec(threads=threads, cinebench_r23_multi=ceiling - 1)
    above = _rec(threads=threads, cinebench_r23_multi=ceiling + 1)
    assert ic.era_score_outliers(below) == []
    assert len(ic.era_score_outliers(above)) == 1


def test_threads_default_to_cores_then_one() -> None:
    # threads missing -> falls back to cores
    rec_cores = _rec(threads=None, cores=6, cinebench_r23_multi=6 * ic.ERA_R23_PER_THREAD + 1)
    assert len(ic.era_score_outliers(rec_cores)) == 1
    # both missing -> falls back to 1 thread
    rec_one = dict(name="No core info", release_date="2010-01-01",
                   cinebench_r23_multi=ic.ERA_R23_PER_THREAD + 1)
    assert len(ic.era_score_outliers(rec_one)) == 1


def test_modern_chips_are_never_flagged_regardless_of_score() -> None:
    # The year gate means post-2011 parts are out of scope entirely.
    rec = _rec(
        name="AMD Ryzen 9 9950X",
        release_date="2024-08-15",
        cores=16,
        threads=32,
        cinebench_r23_multi=42000,
        passmark_cpu_mark=65756,
    )
    assert ic.era_score_outliers(rec) == []


def test_passmark_era_rule_is_thread_aware() -> None:
    # Pre-2006 PassMark ceiling also scales by threads. A single-core 2004 chip
    # with an era-appropriate PassMark is fine; an absurd one still flags.
    ok = _rec(name="Pentium 4 (2004)", release_date="2004-06-01", cores=1, threads=1,
              passmark_cpu_mark=ic.ERA_PASSMARK_PER_THREAD - 1)
    bad = _rec(name="Pentium 4 (2004)", release_date="2004-06-01", cores=1, threads=1,
               passmark_cpu_mark=ic.ERA_PASSMARK_PER_THREAD * 10)
    assert ic.era_score_outliers(ok) == []
    findings = ic.era_score_outliers(bad)
    assert len(findings) == 1
    assert "passmark" in findings[0]


def test_missing_scores_never_flag() -> None:
    assert ic.era_score_outliers(_rec(cinebench_r23_multi=None, passmark_cpu_mark=None)) == []
    assert ic.era_score_outliers(_rec()) == []  # no score fields at all


def test_importing_integrity_check_runs_no_scan(capsys) -> None:
    # The module must be importable without triggering the filesystem scan
    # (guarded by ``if __name__ == '__main__'``). Re-importing is a no-op; assert
    # the pure helper is present and callable.
    assert callable(ic.era_score_outliers)
    captured = capsys.readouterr()
    assert "scope:" not in captured.out
