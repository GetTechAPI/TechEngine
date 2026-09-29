"""Unit tests for the core-count-aware cross-source ratio outlier detector in
``integrity_check``.

Cross-source ratios (e.g. cinebench_r23_multi / geekbench_multi) are the key
wrong-variant contamination signal, but the raw ratio is confounded by core
count: the two benchmarks scale differently with parallelism, so the ratio drifts
monotonically with thread count. The original single global median±MAD therefore
flagged entire legitimate core-count strata (every many-core EPYC/Threadripper/
Xeon) as outliers — the same shape of bug as the flat era ceiling that PR #111
fixed. The fix regresses the core-count trend out and runs the outlier test on
the residuals. These tests pin that behaviour: a whole core-count band that only
follows the natural trend must not flag, while a part that is anomalous *for its
own core count* still must, and callers without a covariate keep the original
global behaviour.
"""

from __future__ import annotations

import math

import integrity_check as ic


def _clean_pair_at(
    threads: int, trend_slope: float = 0.11, base: float = 0.05
) -> tuple[float, float]:
    """Build an (a, b) pair whose log-ratio sits exactly on the core-count trend.

    log(a/b) = base + trend_slope * log(threads); b fixed at 1000.
    """
    log_ratio = base + trend_slope * math.log(threads)
    b = 1000.0
    a = b * math.exp(log_ratio)
    return a, b


# A realistic thread-count ladder spanning desktop -> HEDT -> server, each part's
# ratio lying on the same gentle upward core-count trend (the confounder). The
# population is desktop-dominated (like the real catalog: median ~16 threads), so
# a global median is pulled toward the low-core parts and the legitimate high-core
# trend-followers look like outliers to it.
TREND_THREADS = (
    [4] * 6 + [8] * 8 + [12] * 10 + [16] * 12 + [24] * 6 + [32] * 4
    + [48, 64, 96, 128, 192, 256]
)


def test_pairs_on_the_core_count_trend_do_not_flag() -> None:
    # Every pair follows the same log-ratio-vs-log-threads line, so after the
    # trend is regressed out the residuals are ~0 and nothing is an outlier --
    # even though the raw ratios span a wide range (the old global test flagged
    # the extremes).
    pairs = [
        (f"part-{t}-{i}", *_clean_pair_at(t), t)
        for i, t in enumerate(TREND_THREADS)
    ]
    assert ic.mad_outliers(pairs) == []


def test_raw_global_test_would_have_flagged_the_trend_extremes() -> None:
    # Guard/contrast: the SAME data, fed WITHOUT the covariate, reproduces the
    # old behaviour and flags the high-core extremes. This documents that the fix
    # -- not a change in the data -- is what removes the false positives.
    pairs_no_cov = [(f"part-{t}-{i}", *_clean_pair_at(t)) for i, t in enumerate(TREND_THREADS)]
    flagged = [label for label, _ in ic.mad_outliers(pairs_no_cov)]
    # The many-core trend-followers (which are perfectly legitimate) are what the
    # covariate-free test wrongly flags.
    assert flagged, "global (covariate-free) test should still flag trend extremes"
    assert any("part-256" in f or "part-192" in f for f in flagged)


def test_part_anomalous_for_its_own_core_count_still_flags() -> None:
    # A part whose ratio is far off the trend line *for its thread count* is a
    # genuine wrong-variant candidate and must survive the detrending.
    pairs = [
        (f"part-{t}-{i}", *_clean_pair_at(t), t)
        for i, t in enumerate(TREND_THREADS)
    ]
    # Inject a 32-thread part with a wildly wrong ratio (e.g. a swapped variant):
    bogus_a, bogus_b = _clean_pair_at(32)
    pairs.append(("Swapped-variant 32T", bogus_a * 4.0, bogus_b, 32))
    flagged = [label for label, _ in ic.mad_outliers(pairs)]
    assert "Swapped-variant 32T" in flagged


def test_missing_covariate_recovers_global_behaviour() -> None:
    # Three-tuples (no covariate) must behave exactly like the original detector:
    # a uniform cluster with one gross outlier flags only the outlier.
    pairs = [(f"p{i}", 1000.0 + i, 1000.0) for i in range(20)]
    pairs.append(("gross", 100000.0, 1000.0))
    flagged = [label for label, _ in ic.mad_outliers(pairs)]
    assert flagged == ["gross"]


def test_fewer_than_eight_points_never_flags() -> None:
    pairs = [(f"p{i}", 1000.0 * (i + 1), 1000.0, 8) for i in range(7)]
    assert ic.mad_outliers(pairs) == []


def test_zero_or_missing_values_are_skipped() -> None:
    # a or b of 0/None must not raise and must be dropped from the population.
    pairs = [(f"p{i}", 1000.0, 1000.0, 8) for i in range(10)]
    pairs += [("zero-a", 0, 1000.0, 8), ("none-b", 1000.0, None, 8)]
    # No real outlier among the valid points -> empty, and no exception.
    assert ic.mad_outliers(pairs) == []


def test_returned_ratio_is_the_raw_ratio_not_the_residual() -> None:
    # The reported number must remain the human-readable a/b ratio so reviewers
    # can eyeball it, even though the test runs on residuals.
    pairs = [(f"p{i}", *_clean_pair_at(t), t) for i, t in enumerate(TREND_THREADS)]
    a, b = _clean_pair_at(32)
    pairs.append(("Swapped-variant 32T", a * 4.0, b, 32))
    result = dict(ic.mad_outliers(pairs))
    assert math.isclose(result["Swapped-variant 32T"], round((a * 4.0) / b, 2), rel_tol=1e-6)


def test_theil_sen_recovers_a_known_slope() -> None:
    xs = [math.log(t) for t in (1, 2, 4, 8, 16, 32, 64)]
    ys = [0.5 + 0.3 * x for x in xs]  # perfect line, slope 0.3
    slope, intercept = ic._theil_sen(xs, ys)
    assert math.isclose(slope, 0.3, rel_tol=1e-9)
    assert math.isclose(intercept, 0.5, rel_tol=1e-9)
