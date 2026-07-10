"""Software endpoints (§6.11). List + detail; software is unscored."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Query
from sqlalchemy import func
from sqlmodel import select
from sqlmodel.sql.expression import SelectOfScalar

from app.dependencies import PaginationDep, SessionDep
from app.errors import APIError, not_found
from app.models.software import Software
from app.routers.utils import build_ref_page
from app.schemas.common import Page, ResourceRef
from app.schemas.serializers import resource_ref, software_read
from app.schemas.software import SoftwareRead

router = APIRouter(prefix="/software", tags=["software"])

_SORT_FIELDS: dict[str, Any] = {
    "name": Software.name,
    "release_date": Software.release_date,
}


def _apply_sort(stmt: SelectOfScalar[Any], sort: str | None) -> SelectOfScalar[Any]:
    if not sort:
        return stmt.order_by(Software.name)
    descending = sort.startswith("-")
    field = sort[1:] if descending else sort
    column = _SORT_FIELDS.get(field)
    if column is None:
        raise APIError(400, "INVALID_REQUEST", f"Cannot sort by '{field}'")
    return stmt.order_by(column.desc() if descending else column.asc())


@router.get("", summary="List software")
def list_software(
    session: SessionDep,
    pagination: PaginationDep,
    sort: Annotated[str | None, Query()] = None,
) -> Page[ResourceRef]:
    count = session.exec(select(func.count()).select_from(Software)).one()
    list_stmt = _apply_sort(select(Software), sort)
    list_stmt = list_stmt.offset(pagination.offset).limit(pagination.limit)
    rows = session.exec(list_stmt).all()

    refs = [resource_ref("software", row.slug, row.name) for row in rows]
    applied = {k: v for k, v in (("sort", sort),) if v}
    return build_ref_page(
        refs, count=count, path="/v1/software", pagination=pagination, filters=applied
    )


@router.get("/{slug}", summary="Get a software product")
def get_software(slug: str, session: SessionDep) -> SoftwareRead:
    software = session.exec(select(Software).where(Software.slug == slug)).first()
    if software is None:
        raise not_found("Software", slug)
    return software_read(software)
