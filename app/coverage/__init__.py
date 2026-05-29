"""Coverage gap detector.

Diffs the curated TechAPI dataset against upstream catalogs (Wikipedia, vendor
product pages) and surfaces SKUs that are present upstream but missing locally.

Entry point: ``python -m app.coverage`` — writes ``coverage-report.md``.
"""
