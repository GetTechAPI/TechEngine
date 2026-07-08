"""Monitor endpoints (§6.9). List + detail; monitors are unscored."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Query
from sqlalchemy import func
from sqlmodel import Session, select
from sqlmodel.sql.expression import SelectOfScalar

from app.dependencies import PaginationDep, SessionDep
from app.errors import APIError, not_found
from app.models.brand import Brand
from app.models.monitor import Monitor
from app.routers.utils import build_ref_page
from app.schemas.common import Page, ResourceRef
from app.schemas.monitor import MonitorRead
from app.schemas.serializers import monitor_read, resource_ref

router = APIRouter(prefix="/monitors", tags=["monitors"])

_SORT_FIELDS: dict[str, Any] = {
    "name": Monitor.name,
    "release_date": Monitor.release_date,
    "msrp_usd": Monitor.msrp_usd,
    "size_inch": Monitor.size_inch,
    "refresh_hz": Monitor.refresh_hz,
}


def _resolve_id(session: Session, model: Any, slug: str | None) -> int | None | str:
    if slug is None:
        return None
    row = session.exec(select(model).where(model.slug == slug)).first()
    return row.id if row is not None else "MISSING"


def _apply_sort(stmt: SelectOfScalar[Any], sort: str | None) -> SelectOfScalar[Any]:
    if not sort:
        return stmt.order_by(Monitor.name)
    descending = sort.startswith("-")
    field = sort[1:] if descending else sort
    column = _SORT_FIELDS.get(field)
    if column is None:
        raise APIError(400, "INVALID_REQUEST", f"Cannot sort by '{field}'")
    return stmt.order_by(column.desc() if descending else column.asc())


@router.get("", summary="List monitors")
def list_monitors(
    session: SessionDep,
    pagination: PaginationDep,
    brand: Annotated[str | None, Query()] = None,
    panel_type: Annotated[str | None, Query()] = None,
    base_model: Annotated[str | None, Query(alias="base_model_slug")] = None,
    sort: Annotated[str | None, Query()] = None,
) -> Page[ResourceRef]:
    brand_id = _resolve_id(session, Brand, brand)
    if brand_id == "MISSING":
        return build_ref_page([], count=0, path="/v1/monitors", pagination=pagination)

    filters = []
    if brand_id is not None:
        filters.append(Monitor.brand_id == brand_id)
    if panel_type is not None:
        filters.append(Monitor.panel_type == panel_type)
    if base_model is not None:
        filters.append(Monitor.base_model_slug == base_model)

    count_stmt = select(func.count()).select_from(Monitor)
    list_stmt = select(Monitor)
    for clause in filters:
        count_stmt = count_stmt.where(clause)
        list_stmt = list_stmt.where(clause)

    count = session.exec(count_stmt).one()
    list_stmt = _apply_sort(list_stmt, sort).offset(pagination.offset).limit(pagination.limit)
    rows = session.exec(list_stmt).all()

    refs = [resource_ref("monitors", row.slug, row.name) for row in rows]
    applied = {
        k: v
        for k, v in (
            ("brand", brand),
            ("panel_type", panel_type),
            ("base_model_slug", base_model),
            ("sort", sort),
        )
        if v
    }
    return build_ref_page(
        refs, count=count, path="/v1/monitors", pagination=pagination, filters=applied
    )


@router.get("/{slug}", summary="Get a monitor")
def get_monitor(slug: str, session: SessionDep) -> MonitorRead:
    monitor = session.exec(select(Monitor).where(Monitor.slug == slug)).first()
    if monitor is None:
        raise not_found("Monitor", slug)
    brand = session.get(Brand, monitor.brand_id)
    if brand is None:  # pragma: no cover - guarded by FK + validation
        raise not_found("Brand", str(monitor.brand_id))
    return monitor_read(monitor, brand)
