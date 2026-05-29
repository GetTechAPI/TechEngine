"""Per-source upstream catalog adapters.

Each module exports one or more classes implementing the ``Source`` protocol
from ``base.py``: they fetch a remote catalog and yield ``CoveragePoint``
records that ``app.coverage.report`` then diffs against the curated dataset.
"""
