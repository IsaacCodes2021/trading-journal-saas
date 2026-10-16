"""Plan codes: a person redeems one for a period on a plan (doc 03 section 5, ADR-012).

One answer for every code that does not work (wrong, used up, expired, revoked, already used by this person), so a guess
teaches nothing, and a rate limit per person behind it. A person with a plan from a code that has not ended cannot redeem
another until it ends: that is the one refusal that says why, since it is about their own account, not about the code."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from ...auth.iphash import hash_ip
from ..deps import ERROR_RESPONSES, ConfigDep, ServicesDep, WriteSessionDep, limited_per_person
from ..schemas import RedeemOut
from ..schemas.grants import RedeemRequest
from ..schemas.problem import Problem

router = APIRouter(prefix="/api/v1/grants", tags=["plans"], responses={**ERROR_RESPONSES, 409: {"model": Problem}})


@router.post("/redeem", response_model=RedeemOut, dependencies=[limited_per_person(lambda limits: limits.redeem)])
def redeem(
    body: RedeemRequest, request: Request, session: WriteSessionDep, services: ServicesDep, config: ConfigDep
) -> RedeemOut:
    """Use a promo code. Answers the plan the person is on afterwards, and for a discount code (billing on) the discount
    saved for their next checkout."""
    ip_hash = hash_ip(config.session_secret, request.client.host if request.client else None)
    result = services.accounts.redeem_grant(session.user_id, body.code, ip_hash=ip_hash)
    if result.status == "active" and result.ends_at is not None:
        ends = result.ends_at
        raise HTTPException(
            409,
            f"You already have a plan from a code, until {ends:%b} {ends.day}, {ends.year}. Use another code when it ends.",
        )
    if result.status not in ("ok", "discount") or result.plan is None:
        raise HTTPException(422, "That code did not work. It may be wrong, already used, expired, or revoked.")
    return RedeemOut(**result.plan.model_dump(), discount=result.discount)
