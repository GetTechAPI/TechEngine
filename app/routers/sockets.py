"""CPU socket endpoints (§6.8). List + detail; sockets are unscored."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Query
from sqlalchemy import func
from sqlmodel import select
from sqlmodel.sql.expression import SelectOfScalar

from app.dependencies import PaginationDep, SessionDep
from app.errors import APIError, not_found
from app.models.brand import Brand
from app.models.socket import Socket
from app.routers.utils import build_ref_page
from app.schemas.common import Page, ResourceRef
from app.schemas.serializers import resource_ref, socket_read
from app.schemas.socket import SocketRead

router = APIRouter(prefix="/sockets", tags=["sockets"])

_SORT_FIELDS: dict[str, Any] = {
    "name": Socket.name,
    "release_date": Socket.release_date,
}
# Optional specs: records without a value sort last in either direction.
_NULLS_LAST = {"release_date"}


def _apply_sort(stmt: SelectOfScalar[Any], sort: str | None) -> SelectOfScalar[Any]:
    if not sort:
        return stmt.order_by(Socket.name)
    descending = sort.startswith("-")
    field = sort[1:] if descending else sort
    column = _SORT_FIELDS.get(field)
    if column is None:
        raise APIError(400, "INVALID_REQUEST", f"Cannot sort by '{field}'")
    order = column.desc() if descending else column.asc()
    return stmt.order_by(order.nulls_last() if field in _NULLS_LAST else order)


@router.get("", summary="List sockets")
def list_sockets(
    session: SessionDep,
    pagination: PaginationDep,
    manufacturer: Annotated[str | None, Query()] = None,
    sort: Annotated[str | None, Query()] = None,
) -> Page[ResourceRef]:
    count_stmt = select(func.count()).select_from(Socket)
    list_stmt = select(Socket)
    if manufacturer is not None:
        brand = session.exec(select(Brand).where(Brand.slug == manufacturer)).first()
        if brand is None:
            return build_ref_page([], count=0, path="/v1/sockets", pagination=pagination)
        count_stmt = count_stmt.where(Socket.manufacturer_id == brand.id)
        list_stmt = list_stmt.where(Socket.manufacturer_id == brand.id)

    count = session.exec(count_stmt).one()
    list_stmt = _apply_sort(list_stmt, sort).offset(pagination.offset).limit(pagination.limit)
    rows = session.exec(list_stmt).all()

    refs = [resource_ref("sockets", row.slug, row.name) for row in rows]
    applied = {k: v for k, v in (("manufacturer", manufacturer), ("sort", sort)) if v}
    return build_ref_page(
        refs, count=count, path="/v1/sockets", pagination=pagination, filters=applied
    )


@router.get("/{slug}", summary="Get a socket")
def get_socket(slug: str, session: SessionDep) -> SocketRead:
    socket = session.exec(select(Socket).where(Socket.slug == slug)).first()
    if socket is None:
        raise not_found("Socket", slug)
    manufacturer = (
        session.get(Brand, socket.manufacturer_id) if socket.manufacturer_id is not None else None
    )
    return socket_read(socket, manufacturer)
