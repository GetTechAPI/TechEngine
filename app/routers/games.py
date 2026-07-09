"""Game endpoints (§6.10). List + detail; games are unscored."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Query
from sqlalchemy import func
from sqlmodel import select
from sqlmodel.sql.expression import SelectOfScalar

from app.dependencies import PaginationDep, SessionDep
from app.errors import APIError, not_found
from app.models.game import Game
from app.routers.utils import build_ref_page
from app.schemas.common import Page, ResourceRef
from app.schemas.game import GameRead
from app.schemas.serializers import game_read, resource_ref

router = APIRouter(prefix="/games", tags=["games"])

_SORT_FIELDS: dict[str, Any] = {
    "name": Game.name,
    "release_date": Game.release_date,
    "rating": Game.rating,
    "metacritic": Game.metacritic,
}


def _apply_sort(stmt: SelectOfScalar[Any], sort: str | None) -> SelectOfScalar[Any]:
    if not sort:
        return stmt.order_by(Game.name)
    descending = sort.startswith("-")
    field = sort[1:] if descending else sort
    column = _SORT_FIELDS.get(field)
    if column is None:
        raise APIError(400, "INVALID_REQUEST", f"Cannot sort by '{field}'")
    return stmt.order_by(column.desc() if descending else column.asc())


@router.get("", summary="List games")
def list_games(
    session: SessionDep,
    pagination: PaginationDep,
    sort: Annotated[str | None, Query()] = None,
) -> Page[ResourceRef]:
    count = session.exec(select(func.count()).select_from(Game)).one()
    list_stmt = _apply_sort(select(Game), sort).offset(pagination.offset).limit(pagination.limit)
    rows = session.exec(list_stmt).all()

    refs = [resource_ref("games", row.slug, row.name) for row in rows]
    applied = {k: v for k, v in (("sort", sort),) if v}
    return build_ref_page(
        refs, count=count, path="/v1/games", pagination=pagination, filters=applied
    )


@router.get("/{slug}", summary="Get a game")
def get_game(slug: str, session: SessionDep) -> GameRead:
    game = session.exec(select(Game).where(Game.slug == slug)).first()
    if game is None:
        raise not_found("Game", slug)
    return game_read(game)
