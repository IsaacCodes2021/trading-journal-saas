"""The person's tag groups and tags: the catalog, and managing it (rename, recolour, move, merge, delete)."""

from __future__ import annotations

import psycopg
from fastapi import APIRouter, HTTPException

from ...journal import TAG_COLOR_PRESETS, Tag, TagGroup, clean_name
from ...pg import journal as pg_journal
from ..deps import ERROR_RESPONSES, TenantDep, WriteTenantDep
from ..refusals import refusals, require_row, sentence
from ..schemas import Problem
from ..schemas.journal import PathId
from ..schemas.trades import (
    CatalogTagOut,
    ColorPresetOut,
    TagCatalogOut,
    TagEditIn,
    TagGroupIn,
    TagGroupOut,
    TagMergeIn,
)

router = APIRouter(
    prefix="/api/v1", tags=["tags"], responses={**ERROR_RESPONSES, 404: {"model": Problem}, 409: {"model": Problem}}
)


def _read(conn: psycopg.Connection) -> tuple[list[TagGroup], list[Tag], list[str]]:
    """The catalog's contents, read in the same transaction as the change that came before it."""
    return pg_journal.tag_groups(conn), pg_journal.tags(conn), pg_journal.custom_colors(conn)


def _catalog(groups: list[TagGroup], tags: list[Tag], custom_colors: list[str]) -> TagCatalogOut:
    """Everything the tags screen shows. Built *after* the transaction, so a bug here cannot pass for a refusal."""
    return TagCatalogOut(
        groups=[TagGroupOut(id=g.id, name=g.name) for g in groups],
        tags=[
            CatalogTagOut(id=t.id, group_id=t.group_id, name=t.name, color=t.color, trade_count=t.trade_count)
            for t in tags
        ],
        presets=[ColorPresetOut(color=color, name=name) for color, name in TAG_COLOR_PRESETS.items()],
        custom_colors=custom_colors,
    )


def _no_clash(taken: bool, what: str, name: str) -> None:
    if taken:
        raise HTTPException(409, f"There is already {what} called {name}.")


@router.get("/tags", response_model=TagCatalogOut)
def get_tag_catalog(tenant: TenantDep) -> TagCatalogOut:
    """Every category and tag (each with the number of trades that carry it) and the colours on offer."""
    with tenant.session() as conn:
        snapshot = _read(conn)
    return _catalog(*snapshot)


# ---- categories --------------------------------------------------------------------------------------------------


@router.post("/tag-groups", response_model=TagCatalogOut, status_code=201)
def create_tag_group(body: TagGroupIn, tenant: WriteTenantDep) -> TagCatalogOut:
    with refusals(what="category"), tenant.session() as conn:
        name = clean_name(body.name)
        _no_clash(any(g.name.casefold() == name.casefold() for g in pg_journal.tag_groups(conn)), "a category", name)
        pg_journal.create_group(conn, name)
        snapshot = _read(conn)
    return _catalog(*snapshot)


@router.put("/tag-groups/{group_id}", response_model=TagCatalogOut)
def rename_tag_group(group_id: PathId, body: TagGroupIn, tenant: WriteTenantDep) -> TagCatalogOut:
    with refusals(what="category"), tenant.session() as conn:
        require_row(conn, "tag_groups", group_id, "category")
        name = clean_name(body.name)
        groups = pg_journal.tag_groups(conn)
        _no_clash(any(g.id != group_id and g.name.casefold() == name.casefold() for g in groups), "a category", name)
        pg_journal.rename_group(conn, group_id, name)
        snapshot = _read(conn)
    return _catalog(*snapshot)


@router.delete("/tag-groups/{group_id}", response_model=TagCatalogOut)
def delete_tag_group(group_id: PathId, tenant: WriteTenantDep) -> TagCatalogOut:
    """Delete a category, its tags, and those tags' links to trades (the trades themselves stay)."""
    with refusals(what="category"), tenant.session() as conn:
        require_row(conn, "tag_groups", group_id, "category")
        pg_journal.delete_group(conn, group_id)
        snapshot = _read(conn)
    return _catalog(*snapshot)


# ---- tags ------------------------------------------------------------------------------------------------------------


@router.put("/tags/{tag_id}", response_model=TagCatalogOut)
def edit_tag(tag_id: PathId, body: TagEditIn, tenant: WriteTenantDep) -> TagCatalogOut:
    """Rename, recolour or move a tag. The trades carrying it keep it; a name already used in the target category is
    a conflict (merge the two instead)."""
    with refusals(what="tag"), tenant.session() as conn:
        require_row(conn, "tags", tag_id, "tag")
        require_row(conn, "tag_groups", body.group_id, "category")
        try:
            clash = pg_journal.find_tag(conn, body.group_id, body.name)
        except ValueError as problem:
            raise HTTPException(422, sentence(problem)) from None
        _no_clash(clash is not None and clash.id != tag_id, "a tag", clash.name if clash else body.name)
        pg_journal.update_tag(conn, tag_id, name=body.name, group_id=body.group_id, color=body.color)
        snapshot = _read(conn)
    return _catalog(*snapshot)


@router.post("/tags/{tag_id}/merge", response_model=TagCatalogOut)
def merge_tag(tag_id: PathId, body: TagMergeIn, tenant: WriteTenantDep) -> TagCatalogOut:
    """Move every trade from this tag to another, then delete this one. A trade that carried both keeps one."""
    with refusals(what="tag"), tenant.session() as conn:
        require_row(conn, "tags", tag_id, "tag")
        require_row(conn, "tags", body.into_id, "tag")
        pg_journal.merge_tags(conn, tag_id, body.into_id)  # refuses merging a tag into itself, in words (422)
        snapshot = _read(conn)
    return _catalog(*snapshot)


@router.delete("/tags/{tag_id}", response_model=TagCatalogOut)
def delete_tag(tag_id: PathId, tenant: WriteTenantDep) -> TagCatalogOut:
    """Delete a tag everywhere, including from the trades that carry it."""
    with refusals(what="tag"), tenant.session() as conn:
        require_row(conn, "tags", tag_id, "tag")
        pg_journal.delete_tag(conn, tag_id)
        snapshot = _read(conn)
    return _catalog(*snapshot)
