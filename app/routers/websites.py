"""Website endpoints (§6.12). List + detail; websites are unscored."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Query
from sqlalchemy import func
from sqlmodel import select
from sqlmodel.sql.expression import SelectOfScalar

from app.dependencies import PaginationDep, SessionDep
from app.errors import APIError, not_found
from app.models.website import Website
from app.routers.utils import build_ref_page
from app.schemas.common import Page, ResourceRef
from app.schemas.serializers import resource_ref, website_read
from app.schemas.website import WebsiteRead

router = APIRouter(prefix="/websites", tags=["websites"])

_SORT_FIELDS: dict[str, Any] = {
    "name": Website.name,
    "launch_date": Website.launch_date,
}


def _apply_sort(stmt: SelectOfScalar[Any], sort: str | None) -> SelectOfScalar[Any]:
    if not sort:
        return stmt.order_by(Website.name)
    descending = sort.startswith("-")
    field = sort[1:] if descending else sort
    column = _SORT_FIELDS.get(field)
    if column is None:
        raise APIError(400, "INVALID_REQUEST", f"Cannot sort by '{field}'")
    return stmt.order_by(column.desc() if descending else column.asc())


@router.get("", summary="List websites")
def list_websites(
    session: SessionDep,
    pagination: PaginationDep,
    sort: Annotated[str | None, Query()] = None,
) -> Page[ResourceRef]:
    count = session.exec(select(func.count()).select_from(Website)).one()
    list_stmt = _apply_sort(select(Website), sort)
    list_stmt = list_stmt.offset(pagination.offset).limit(pagination.limit)
    rows = session.exec(list_stmt).all()

    refs = [resource_ref("websites", row.slug, row.name) for row in rows]
    applied = {k: v for k, v in (("sort", sort),) if v}
    return build_ref_page(
        refs, count=count, path="/v1/websites", pagination=pagination, filters=applied
    )


@router.get("/{slug}", summary="Get a website")
def get_website(slug: str, session: SessionDep) -> WebsiteRead:
    website = session.exec(select(Website).where(Website.slug == slug)).first()
    if website is None:
        raise not_found("Website", slug)
    return website_read(website)
