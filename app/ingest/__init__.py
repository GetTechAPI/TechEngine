"""Automated ingestion crawler.

Reads upstream catalogs (Wikipedia list pages today, vendor product pages
later), normalizes each row into a TechAPI-shaped JSON record, and writes
draft records into a TechAPI checkout. A companion CI workflow opens a PR
against TechAPI so curators only review.

Entry point: ``python -m app.ingest --category cpu --limit 5``.
"""
