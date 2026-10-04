"""The person's tag groups and tags (read side)."""

from __future__ import annotations

from fastapi import APIRouter

from ...pg import journal as pg_journal
from ..deps import ERROR_RESPONSES, TenantDep
from ..schemas.trades import CatalogTagOut, TagCatalogOut, TagGroupOut

router = APIRouter(prefix="/api/v1", tags=["tags"], responses=ERROR_RESPONSES)


@router.get("/tags", response_model=TagCatalogOut)
def get_tag_catalog(tenant: TenantDep) -> TagCatalogOut:
    """Every group and tag, each tag with the number of trades that carry it."""
    with tenant.session() as conn:
        groups = pg_journal.tag_groups(conn)
        tags = pg_journal.tags(conn)
    return TagCatalogOut(
        groups=[TagGroupOut(id=g.id, name=g.name) for g in groups],
        tags=[
            CatalogTagOut(id=t.id, group_id=t.group_id, name=t.name, color=t.color, trade_count=t.trade_count)
            for t in tags
        ],
    )
