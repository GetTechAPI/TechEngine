"""Small database fixtures for game endpoint tests."""

from __future__ import annotations

from datetime import date

from sqlmodel import Session, select

from app.database import engine
from app.models.game import Game


def ensure_game_fixtures() -> None:
    """Insert a compact game when the data checkout lacks it."""

    with Session(engine) as session:
        game = session.exec(
            select(Game).where(Game.slug == "the-witcher-3-test")
        ).first()
        if game is None:
            session.add(
                Game(
                    slug="the-witcher-3-test",
                    name="The Witcher 3: Wild Hunt (test)",
                    release_date=date(2015, 5, 19),
                    rating=4.7,
                    rating_count=6000,
                    metacritic=92,
                    playtime_hours=46,
                    platforms=["PC", "PlayStation 5"],
                    genres=["zzz-test-genre", "RPG"],
                    stores=["Steam", "GOG"],
                    developers=["CD Projekt Red"],
                    publishers=["CD Projekt"],
                    tags=["singleplayer", "open-world"],
                    esrb_rating="Mature",
                    source_urls=["https://example.com"],
                )
            )
            session.commit()
