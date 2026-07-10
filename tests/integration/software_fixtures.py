"""Small database fixtures for software endpoint tests."""

from __future__ import annotations

from datetime import date

from sqlmodel import Session, select

from app.database import engine
from app.models.software import Software


def ensure_software_fixtures() -> None:
    """Insert a compact software product when the data checkout lacks it."""

    with Session(engine) as session:
        sw = session.exec(
            select(Software).where(Software.slug == "blender-test")
        ).first()
        if sw is None:
            session.add(
                Software(
                    slug="blender-test",
                    name="Blender (test)",
                    release_date=date(1998, 1, 2),
                    developers=["Blender Foundation"],
                    publishers=["Blender Foundation"],
                    operating_systems=["Linux", "Windows", "macOS"],
                    programming_languages=["C", "C++", "Python"],
                    licenses=["GNU General Public License"],
                    genres=["3D computer graphics software"],
                    source_urls=["https://example.com"],
                )
            )
            session.commit()
