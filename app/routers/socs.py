"""SoC endpoints (§7.2)."""

from __future__ import annotations

from fastapi import APIRouter
from sqlalchemy import func
from sqlmodel import select

from app.dependencies import PaginationDep, SessionDep
from app.errors import not_found
from app.models.brand import Brand
from app.models.smartphone import Smartphone
from app.models.soc import SoC
from app.routers.utils import build_ref_page
from app.schemas.common import Page, ResourceRef
from app.schemas.serializers import resource_ref, soc_read, soc_score_read
from app.schemas.soc import SoCRead, SoCScoreRead
from app.services.scoring import get_dataset_stats, score_soc

router = APIRouter(prefix="/socs", tags=["socs"])


@router.get("", summary="List SoCs")
def list_socs(session: SessionDep, pagination: PaginationDep) -> Page[ResourceRef]:
    count = session.exec(select(func.count()).select_from(SoC)).one()
    rows = session.exec(
        select(SoC).order_by(SoC.name).offset(pagination.offset).limit(pagination.limit)
    ).all()
    refs = [resource_ref("socs", s.slug, s.name) for s in rows]
    return build_ref_page(refs, count=count, path="/v1/socs", pagination=pagination)


def _load_soc(session: SessionDep, slug: str) -> tuple[SoC, Brand]:
    soc = session.exec(select(SoC).where(SoC.slug == slug)).first()
    if soc is None:
        raise not_found("SoC", slug)
    manufacturer = session.get(Brand, soc.manufacturer_id)
    if manufacturer is None:  # pragma: no cover - guarded by FK + validation
        raise not_found("Brand", str(soc.manufacturer_id))
    return soc, manufacturer


@router.get("/{slug}", summary="Get a SoC")
def get_soc(slug: str, session: SessionDep) -> SoCRead:
    soc, manufacturer = _load_soc(session, slug)
    score = score_soc(soc, stats=get_dataset_stats(session))
    return soc_read(soc, manufacturer, score)


@router.get("/{slug}/score", summary="Get a SoC's scores")
def get_soc_score(slug: str, session: SessionDep) -> SoCScoreRead:
    soc, _manufacturer = _load_soc(session, slug)
    return soc_score_read(score_soc(soc, stats=get_dataset_stats(session)))


@router.get("/{slug}/smartphones", summary="Smartphones using this SoC")
def soc_smartphones(
    slug: str, session: SessionDep, pagination: PaginationDep
) -> Page[ResourceRef]:
    soc = session.exec(select(SoC).where(SoC.slug == slug)).first()
    if soc is None:
        raise not_found("SoC", slug)

    count = session.exec(
        select(func.count()).select_from(Smartphone).where(Smartphone.soc_id == soc.id)
    ).one()
    rows = session.exec(
        select(Smartphone)
        .where(Smartphone.soc_id == soc.id)
        .order_by(Smartphone.name)
        .offset(pagination.offset)
        .limit(pagination.limit)
    ).all()
    refs = [resource_ref("smartphones", p.slug, p.name) for p in rows]
    return build_ref_page(
        refs, count=count, path=f"/v1/socs/{slug}/smartphones", pagination=pagination
    )
