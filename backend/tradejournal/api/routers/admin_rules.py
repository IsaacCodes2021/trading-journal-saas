"""The operator's prop-firm rule templates: list, read, edit, and re-import the research CSV (migration 0019).

The same guard as every operator route (two-step verification in the last 15 minutes, CSRF checked first on a write),
and one audit row for each change. The templates are the firms' published rules, read by everyone to pick an account's
rules, and hold nothing anyone wrote in their journal; editing one never changes a person's verified rule set.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Path, Query, Request, UploadFile

from ...auth.iphash import hash_ip
from ..deps import ERROR_RESPONSES, AdminDep, AdminServiceDep, ConfigDep, WriteAdminDep
from ..rules_view import checked_rules
from ..schemas import Problem
from ..schemas.rules import (
    AdminRuleTemplateListOut,
    AdminRuleTemplateOut,
    RuleTemplateEditIn,
    RuleTemplateImportOut,
)

router = APIRouter(
    prefix="/api/admin/v1",
    tags=["admin"],
    responses={**ERROR_RESPONSES, 404: {"model": Problem}, 409: {"model": Problem}},
)

MAX_TEMPLATE_CSV_BYTES = 5 * 1024 * 1024  # the research CSV is about 650 KB
Limit = Annotated[int, Query(ge=1, le=100)]
Offset = Annotated[int, Query(ge=0, le=1_000_000)]
TemplateId = Annotated[int, Path(ge=1, le=2**63 - 1)]


def _ip(request: Request, config: ConfigDep) -> str | None:
    return hash_ip(config.session_secret, request.client.host if request.client else None)


@router.get("/rule-templates", response_model=AdminRuleTemplateListOut)
def admin_list_rule_templates(
    _: AdminDep,
    admin: AdminServiceDep,
    q: Annotated[
        str | None, Query(max_length=100, description="Words of a firm, program, size, phase or version")
    ] = None,
    limit: Limit = 50,
    offset: Offset = 0,
) -> AdminRuleTemplateListOut:
    """The firms' rule templates by firm, program, size and phase, the newest version first."""
    return admin.list_rule_templates(q=q, limit=limit, offset=offset)


@router.get("/rule-templates/{template_id}", response_model=AdminRuleTemplateOut)
def admin_get_rule_template(template_id: TemplateId, _: AdminDep, admin: AdminServiceDep) -> AdminRuleTemplateOut:
    return admin.rule_template(template_id)


@router.put("/rule-templates/{template_id}", response_model=AdminRuleTemplateOut)
def admin_edit_rule_template(
    template_id: TemplateId,
    body: RuleTemplateEditIn,
    request: Request,
    operator: WriteAdminDep,
    admin: AdminServiceDep,
    config: ConfigDep,
) -> AdminRuleTemplateOut:
    """Replace one template's values and flags. Only the kinds of the values are checked (the research leaves cells
    empty on purpose); people's verified rule sets are copies and do not change."""
    rules = checked_rules(body.values, body.flags, whole=False)
    return admin.edit_rule_template(operator.user_id, template_id, rules, ip_hash=_ip(request, config))


@router.post("/rule-templates/import", response_model=RuleTemplateImportOut)
def admin_import_rule_templates(
    file: UploadFile,
    request: Request,
    operator: WriteAdminDep,
    admin: AdminServiceDep,
    config: ConfigDep,
    dry_run: Annotated[bool, Query(description="Work out the counts and keep nothing (no audit row)")] = False,
) -> RuleTemplateImportOut:
    """Load the research CSV (the 48 columns, one row per rule set): new rule sets are added, changed ones updated and
    the rest left alone. Rows that cannot be read are listed by line; the others are still loaded. A changed row
    replaces the template's values, an operator's edit included, so `changed` names them; a dry run shows them first."""
    data = file.file.read(MAX_TEMPLATE_CSV_BYTES + 1)
    if len(data) > MAX_TEMPLATE_CSV_BYTES:
        raise HTTPException(413, "That file is too large (5 MB at most).")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        raise HTTPException(422, "That file is not UTF-8 text.") from None
    return admin.import_rule_templates(operator.user_id, text, dry_run=dry_run, ip_hash=_ip(request, config))
