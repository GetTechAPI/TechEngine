"""Chipset endpoints (§6.8). List + detail; chipsets are unscored."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Query
from sqlalchemy import func
from sqlmodel import select
from sqlmodel.sql.expression import SelectOfScalar

from app.dependencies import PaginationDep, SessionDep
from app.errors import APIError, not_found
from app.models.brand import Brand
from app.models.chipset import Chipset
from app.models.socket import Socket
from app.routers.utils import build_ref_page
from app.schemas.chipset import ChipsetRead
from app.schemas.common import Page, ResourceRef
from app.schemas.serializers import chipset_read, resource_ref

router = APIRouter(prefix="/chipsets", tags=["chipsets"])

_SORT_FIELDS: dict[str, Any] = {
    "name": Chipset.name,
    "release_date": Chipset.release_date,
}
# Optional specs: records without a value sort last in either direction.
_NULLS_LAST = {"release_date"}


def _apply_sort(stmt: SelectOfScalar[Any], sort: str | None) -> SelectOfScalar[Any]:
    if not sort:
        return stmt.order_by(Chipset.name)
    descending = sort.startswith("-")
    field = sort[1:] if descending else sort
    column = _SORT_FIELDS.get(field)
    if column is None:
        raise APIError(400, "INVALID_REQUEST", f"Cannot sort by '{field}'")
    order = column.desc() if descending else column.asc()
    return stmt.order_by(order.nulls_last() if field in _NULLS_LAST else order)


@router.get("", summary="List chipsets")
def list_chipsets(
    session: SessionDep,
    pagination: PaginationDep,
    manufacturer: Annotated[str | None, Query()] = None,
    sort: Annotated[str | None, Query()] = None,
) -> Page[ResourceRef]:
    count_stmt = select(func.count()).select_from(Chipset)
    list_stmt = select(Chipset)
    if manufacturer is not None:
        brand = session.exec(select(Brand).where(Brand.slug == manufacturer)).first()
        if brand is None:
            return build_ref_page([], count=0, path="/v1/chipsets", pagination=pagination)
        count_stmt = count_stmt.where(Chipset.manufacturer_id == brand.id)
        list_stmt = list_stmt.where(Chipset.manufacturer_id == brand.id)

    count = session.exec(count_stmt).one()
    list_stmt = _apply_sort(list_stmt, sort).offset(pagination.offset).limit(pagination.limit)
    rows = session.exec(list_stmt).all()

    refs = [resource_ref("chipsets", row.slug, row.name) for row in rows]
    applied = {k: v for k, v in (("manufacturer", manufacturer), ("sort", sort)) if v}
    return build_ref_page(
        refs, count=count, path="/v1/chipsets", pagination=pagination, filters=applied
    )


@router.get("/{slug}", summary="Get a chipset")
def get_chipset(slug: str, session: SessionDep) -> ChipsetRead:
    chipset = session.exec(select(Chipset).where(Chipset.slug == slug)).first()
    if chipset is None:
        raise not_found("Chipset", slug)
    manufacturer = session.get(Brand, chipset.manufacturer_id)
    if manufacturer is None:  # pragma: no cover - guarded by FK + validation
        raise not_found("Brand", str(chipset.manufacturer_id))
    by_slug = {
        socket.slug: socket
        for socket in session.exec(
            select(Socket).where(Socket.slug.in_(chipset.sockets))  # type: ignore[attr-defined]
        ).all()
    }
    # Keep the record's order; seed guarantees every slug resolves.
    sockets = [by_slug[socket_slug] for socket_slug in chipset.sockets if socket_slug in by_slug]
    return chipset_read(chipset, manufacturer, sockets)
