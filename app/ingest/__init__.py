"""Automated ingestion crawler.

Reads upstream catalogs (Wikipedia list pages today, vendor product pages
later), normalizes each row into a TechAPI-shaped JSON record, and writes
draft records into a TechAPI checkout. ``ingest.yml`` uploads the result as a
patch; a maintainer opens the TechAPI PR from it by hand.

Entry point: ``python -m app.ingest --category cpu --limit 5``.
"""
