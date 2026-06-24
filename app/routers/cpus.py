"""Computer CPU endpoints (§7.2, §6.7)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query
from sqlalchemy import func
from sqlmodel import select

from app.dependencies import PaginationDep, SessionDep
from app.errors import not_found
from app.models.brand import Brand
from app.models.cpu import CPU
from app.routers.utils import build_ref_page
from app.schemas.common import Page, ResourceRef
from app.schemas.cpu import CPURead, CPUScoreRead
from app.schemas.serializers import cpu_read, cpu_score_read, resource_ref
from app.services.scoring import get_dataset_stats, score_cpu

router = APIRouter(prefix="/cpus", tags=["cpus"])


@router.get("", summary="List CPUs")
def list_cpus(
    session: SessionDep,
    pagination: PaginationDep,
    segment: Annotated[str | None, Query()] = None,
) -> Page[ResourceRef]:
    count_stmt = select(func.count()).select_from(CPU)
    list_stmt = select(CPU)
    if segment is not None:
        count_stmt = count_stmt.where(CPU.segment == segment)
        list_stmt = list_stmt.where(CPU.segment == segment)

    count = session.exec(count_stmt).one()
    rows = session.exec(
        list_stmt.order_by(CPU.name).offset(pagination.offset).limit(pagination.limit)
    ).all()
    refs = [resource_ref("cpus", c.slug, c.name) for c in rows]
    filters = {"segment": segment} if segment else None
    return build_ref_page(
        refs, count=count, path="/v1/cpus", pagination=pagination, filters=filters
    )


def _load_cpu(session: SessionDep, slug: str) -> tuple[CPU, Brand]:
    cpu = session.exec(select(CPU).where(CPU.slug == slug)).first()
    if cpu is None:
        raise not_found("CPU", slug)
    manufacturer = session.get(Brand, cpu.manufacturer_id)
    if manufacturer is None:  # pragma: no cover - guarded by FK + validation
        raise not_found("Brand", str(cpu.manufacturer_id))
    return cpu, manufacturer


@router.get("/{slug}", summary="Get a CPU")
def get_cpu(slug: str, session: SessionDep) -> CPURead:
    cpu, manufacturer = _load_cpu(session, slug)
    score = score_cpu(cpu, stats=get_dataset_stats(session))
    return cpu_read(cpu, manufacturer, score)


@router.get("/{slug}/score", summary="Get a CPU's scores")
def get_cpu_score(slug: str, session: SessionDep) -> CPUScoreRead:
    cpu, _manufacturer = _load_cpu(session, slug)
    return cpu_score_read(score_cpu(cpu, stats=get_dataset_stats(session)))
