"""Laptop endpoints (§6.8). List + detail; laptops are unscored."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Query
from sqlalchemy import func
from sqlmodel import Session, select
from sqlmodel.sql.expression import SelectOfScalar

from app.dependencies import PaginationDep, SessionDep
from app.errors import APIError, not_found
from app.models.brand import Brand
from app.models.cpu import CPU
from app.models.gpu import DiscreteGPU
from app.models.laptop import Laptop
from app.routers.utils import build_ref_page
from app.schemas.common import Page, ResourceRef
from app.schemas.laptop import LaptopRead
from app.schemas.serializers import laptop_read, resource_ref

router = APIRouter(prefix="/laptops", tags=["laptops"])

_SORT_FIELDS: dict[str, Any] = {
    "name": Laptop.name,
    "release_date": Laptop.release_date,
    "msrp_usd": Laptop.msrp_usd,
}


def _resolve_id(session: Session, model: Any, slug: str | None) -> int | None | str:
    if slug is None:
        return None
    row = session.exec(select(model).where(model.slug == slug)).first()
    return row.id if row is not None else "MISSING"


def _apply_sort(stmt: SelectOfScalar[Any], sort: str | None) -> SelectOfScalar[Any]:
    if not sort:
        return stmt.order_by(Laptop.name)
    descending = sort.startswith("-")
    field = sort[1:] if descending else sort
    column = _SORT_FIELDS.get(field)
    if column is None:
        raise APIError(400, "INVALID_REQUEST", f"Cannot sort by '{field}'")
    return stmt.order_by(column.desc() if descending else column.asc())


@router.get("", summary="List laptops")
def list_laptops(
    session: SessionDep,
    pagination: PaginationDep,
    brand: Annotated[str | None, Query()] = None,
    cpu: Annotated[str | None, Query()] = None,
    gpu: Annotated[str | None, Query()] = None,
    base_model: Annotated[str | None, Query(alias="base_model_slug")] = None,
    sort: Annotated[str | None, Query()] = None,
) -> Page[ResourceRef]:
    brand_id = _resolve_id(session, Brand, brand)
    cpu_id = _resolve_id(session, CPU, cpu)
    gpu_id = _resolve_id(session, DiscreteGPU, gpu)

    if "MISSING" in (brand_id, cpu_id, gpu_id):
        return build_ref_page([], count=0, path="/v1/laptops", pagination=pagination)

    filters = []
    if brand_id is not None:
        filters.append(Laptop.brand_id == brand_id)
    if cpu_id is not None:
        filters.append(Laptop.cpu_id == cpu_id)
    if gpu_id is not None:
        filters.append(Laptop.gpu_id == gpu_id)
    if base_model is not None:
        filters.append(Laptop.base_model_slug == base_model)

    count_stmt = select(func.count()).select_from(Laptop)
    list_stmt = select(Laptop)
    for clause in filters:
        count_stmt = count_stmt.where(clause)
        list_stmt = list_stmt.where(clause)

    count = session.exec(count_stmt).one()
    list_stmt = _apply_sort(list_stmt, sort).offset(pagination.offset).limit(pagination.limit)
    rows = session.exec(list_stmt).all()

    refs = [resource_ref("laptops", row.slug, row.name) for row in rows]
    applied = {
        k: v
        for k, v in (
            ("brand", brand),
            ("cpu", cpu),
            ("gpu", gpu),
            ("base_model_slug", base_model),
            ("sort", sort),
        )
        if v
    }
    return build_ref_page(
        refs, count=count, path="/v1/laptops", pagination=pagination, filters=applied
    )


@router.get("/{slug}", summary="Get a laptop")
def get_laptop(slug: str, session: SessionDep) -> LaptopRead:
    laptop = session.exec(select(Laptop).where(Laptop.slug == slug)).first()
    if laptop is None:
        raise not_found("Laptop", slug)
    brand = session.get(Brand, laptop.brand_id)
    if brand is None:  # pragma: no cover - guarded by FK + validation
        raise not_found("Brand", str(laptop.brand_id))
    cpu = session.get(CPU, laptop.cpu_id) if laptop.cpu_id is not None else None
    gpu = session.get(DiscreteGPU, laptop.gpu_id) if laptop.gpu_id is not None else None
    return laptop_read(laptop, brand, cpu, gpu)
