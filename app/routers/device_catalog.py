"""Device catalog endpoints. List + detail; identity-only records are unscored."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Query
from sqlalchemy import func
from sqlmodel import select
from sqlmodel.sql.expression import SelectOfScalar

from app.dependencies import PaginationDep, SessionDep
from app.errors import APIError, not_found
from app.models.brand import Brand
from app.models.device_catalog import DeviceCatalog
from app.models.soc import SoC
from app.routers.utils import build_ref_page
from app.schemas.common import Page, ResourceRef
from app.schemas.device_catalog import DeviceCatalogRead
from app.schemas.serializers import device_catalog_read, resource_ref

router = APIRouter(prefix="/device-catalog", tags=["device-catalog"])

_SORT_FIELDS: dict[str, Any] = {
    "name": DeviceCatalog.name,
    "android_sdk_min": DeviceCatalog.android_sdk_min,
}


def _apply_sort(stmt: SelectOfScalar[Any], sort: str | None) -> SelectOfScalar[Any]:
    if not sort:
        return stmt.order_by(DeviceCatalog.name)
    descending = sort.startswith("-")
    field = sort[1:] if descending else sort
    column = _SORT_FIELDS.get(field)
    if column is None:
        raise APIError(400, "INVALID_REQUEST", f"Cannot sort by '{field}'")
    return stmt.order_by(column.desc() if descending else column.asc())


@router.get("", summary="List device catalog entries")
def list_device_catalog(
    session: SessionDep,
    pagination: PaginationDep,
    brand: Annotated[str | None, Query()] = None,
    form_factor: Annotated[str | None, Query()] = None,
    base_model: Annotated[str | None, Query(alias="base_model_slug")] = None,
    sort: Annotated[str | None, Query()] = None,
) -> Page[ResourceRef]:
    filters = []
    if brand is not None:
        row = session.exec(select(Brand).where(Brand.slug == brand)).first()
        if row is None:
            return build_ref_page([], count=0, path="/v1/device-catalog", pagination=pagination)
        filters.append(DeviceCatalog.brand_id == row.id)
    if form_factor is not None:
        filters.append(DeviceCatalog.form_factor == form_factor)
    if base_model is not None:
        filters.append(DeviceCatalog.base_model_slug == base_model)

    count_stmt = select(func.count()).select_from(DeviceCatalog)
    list_stmt = select(DeviceCatalog)
    for clause in filters:
        count_stmt = count_stmt.where(clause)
        list_stmt = list_stmt.where(clause)

    count = session.exec(count_stmt).one()
    list_stmt = _apply_sort(list_stmt, sort).offset(pagination.offset).limit(pagination.limit)
    rows = session.exec(list_stmt).all()

    refs = [resource_ref("device-catalog", row.slug, row.name) for row in rows]
    applied = {
        k: v
        for k, v in (
            ("brand", brand),
            ("form_factor", form_factor),
            ("base_model_slug", base_model),
            ("sort", sort),
        )
        if v
    }
    return build_ref_page(
        refs, count=count, path="/v1/device-catalog", pagination=pagination, filters=applied
    )


@router.get("/{slug}", summary="Get a device catalog entry")
def get_device_catalog_entry(slug: str, session: SessionDep) -> DeviceCatalogRead:
    entry = session.exec(select(DeviceCatalog).where(DeviceCatalog.slug == slug)).first()
    if entry is None:
        raise not_found("Device catalog entry", slug)
    brand = session.get(Brand, entry.brand_id)
    if brand is None:  # pragma: no cover - guarded by FK + validation
        raise not_found("Brand", str(entry.brand_id))
    soc = session.get(SoC, entry.soc_id) if entry.soc_id is not None else None
    soc_manufacturer = session.get(Brand, soc.manufacturer_id) if soc is not None else None
    return device_catalog_read(entry, brand, soc, soc_manufacturer)
