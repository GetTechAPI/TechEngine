"""Shared endpoints for non-phone mobile device categories."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Query
from sqlalchemy import func
from sqlmodel import Session, select
from sqlmodel.sql.expression import SelectOfScalar

from app.dependencies import PaginationDep, SessionDep
from app.errors import APIError, not_found
from app.models.brand import Brand
from app.models.mobile_device import PDA, Tablet, Watch
from app.models.soc import SoC
from app.routers.utils import build_ref_page
from app.schemas.common import Page, ResourceRef
from app.schemas.mobile_device import MobileDeviceRead
from app.schemas.serializers import mobile_device_read, resource_ref


def _resolve_id(session: Session, model: Any, slug: str | None) -> int | None | str:
    if slug is None:
        return None
    row = session.exec(select(model).where(model.slug == slug)).first()
    return row.id if row is not None else "MISSING"


def _make_router(resource: str, model: Any) -> APIRouter:
    router = APIRouter(prefix=f"/{resource}", tags=[resource])
    sort_fields: dict[str, Any] = {
        "name": model.name,
        "release_date": model.release_date,
        "msrp_usd": model.msrp_usd,
    }

    def apply_sort(stmt: SelectOfScalar[Any], sort: str | None) -> SelectOfScalar[Any]:
        if not sort:
            return stmt.order_by(model.name)
        descending = sort.startswith("-")
        field = sort[1:] if descending else sort
        column = sort_fields.get(field)
        if column is None:
            raise APIError(400, "INVALID_REQUEST", f"Cannot sort by '{field}'")
        return stmt.order_by(column.desc() if descending else column.asc())

    @router.get("", summary=f"List {resource}")
    def list_devices(
        session: SessionDep,
        pagination: PaginationDep,
        brand: Annotated[str | None, Query()] = None,
        soc: Annotated[str | None, Query()] = None,
        base_model: Annotated[str | None, Query(alias="base_model_slug")] = None,
        sort: Annotated[str | None, Query()] = None,
    ) -> Page[ResourceRef]:
        filters = []
        brand_id = _resolve_id(session, Brand, brand)
        soc_id = _resolve_id(session, SoC, soc)

        if brand_id == "MISSING" or soc_id == "MISSING":
            return build_ref_page([], count=0, path=f"/v1/{resource}", pagination=pagination)

        if brand_id is not None:
            filters.append(model.brand_id == brand_id)
        if soc_id is not None:
            filters.append(model.soc_id == soc_id)
        if base_model is not None:
            filters.append(model.base_model_slug == base_model)

        count_stmt = select(func.count()).select_from(model)
        list_stmt = select(model)
        for clause in filters:
            count_stmt = count_stmt.where(clause)
            list_stmt = list_stmt.where(clause)

        count = session.exec(count_stmt).one()
        list_stmt = apply_sort(list_stmt, sort).offset(pagination.offset).limit(pagination.limit)
        rows = session.exec(list_stmt).all()

        refs = [resource_ref(resource, row.slug, row.name) for row in rows]
        applied = {
            k: v
            for k, v in (
                ("brand", brand),
                ("soc", soc),
                ("base_model_slug", base_model),
                ("sort", sort),
            )
            if v
        }
        return build_ref_page(
            refs, count=count, path=f"/v1/{resource}", pagination=pagination, filters=applied
        )

    @router.get("/{slug}", summary=f"Get a {resource[:-1]}")
    def get_device(slug: str, session: SessionDep) -> MobileDeviceRead:
        device = session.exec(select(model).where(model.slug == slug)).first()
        if device is None:
            raise not_found(resource[:-1].title(), slug)
        brand = session.get(Brand, device.brand_id)
        if brand is None:  # pragma: no cover - guarded by FK + validation
            raise not_found("Brand", str(device.brand_id))

        soc = None
        soc_manufacturer = None
        if device.soc_id is not None:
            soc = session.get(SoC, device.soc_id)
            if soc is None:  # pragma: no cover
                raise not_found("SoC", str(device.soc_id))
            soc_manufacturer = session.get(Brand, soc.manufacturer_id)
            if soc_manufacturer is None:  # pragma: no cover
                raise not_found("Brand", str(soc.manufacturer_id))

        return mobile_device_read(resource, device, brand, soc, soc_manufacturer)

    return router


tablets_router = _make_router("tablets", Tablet)
watches_router = _make_router("watches", Watch)
pdas_router = _make_router("pdas", PDA)
