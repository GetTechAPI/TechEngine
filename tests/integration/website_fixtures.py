"""Small database fixtures for website endpoint tests."""

from __future__ import annotations

from datetime import date

from sqlmodel import Session, select

from app.database import engine
from app.models.website import Website


def ensure_website_fixtures() -> None:
    """Insert a compact website when the data checkout lacks it."""

    with Session(engine) as session:
        site = session.exec(
            select(Website).where(Website.slug == "wikipedia-test")
        ).first()
        if site is None:
            session.add(
                Website(
                    slug="wikipedia-test",
                    name="Wikipedia (test)",
                    homepage_url="https://www.wikipedia.org/",
                    launch_date=date(2001, 1, 15),
                    owners=["Wikimedia Foundation"],
                    languages=["English"],
                    source_urls=["https://example.com"],
                )
            )
            session.commit()
