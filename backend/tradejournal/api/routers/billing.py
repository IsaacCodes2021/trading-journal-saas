"""The person's own billing (the billing design, "Routes").

Every route answers 404 while billing is switched off, which is how the Plan page knows to show what it always showed. None
uses the read-only guard: a person whose journal is read-only must be able to subscribe, so changes use the CSRF guard only,
as sign-out and export do. Checkout, cancel, resume and the provider's page are limited to 10 an hour per person, and none
of them takes an id: they always act on the person signed in.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request

from ...auth.iphash import hash_ip
from ..billing_service import BillingRefusal, PgBillingService
from ..deps import ERROR_RESPONSES, ConfigDep, ServicesDep, SessionDep, WriteSessionDep, limited_per_person
from ..schemas.billing import BillingOut, CheckoutOut, PortalOut
from ..schemas.problem import Problem


def billing_on(config: ConfigDep, services: ServicesDep) -> PgBillingService:
    if not config.billing_enabled or services.billing is None:
        raise HTTPException(404, "Not Found")
    return services.billing  # type: ignore[return-value]


BillingDep = Annotated[PgBillingService, Depends(billing_on)]
Limited = limited_per_person(lambda limits: limits.billing)

router = APIRouter(
    prefix="/api/v1/billing",
    tags=["billing"],
    responses={**ERROR_RESPONSES, 404: {"model": Problem}, 409: {"model": Problem}, 503: {"model": Problem}},
)


def _ip(request: Request, config: ConfigDep) -> str | None:
    return hash_ip(config.session_secret, request.client.host if request.client else None)


def _refused(problem: BillingRefusal) -> HTTPException:
    return HTTPException(problem.status, problem.detail)


@router.get("", response_model=BillingOut)
def get_billing(billing: BillingDep, session: SessionDep) -> BillingOut:
    """Where this person's plan and billing stand, for the Plan & billing page."""
    return billing.state(session.user_id)


@router.post("/checkout", response_model=CheckoutOut, dependencies=[Limited])
def start_checkout(billing: BillingDep, session: WriteSessionDep, request: Request, config: ConfigDep) -> CheckoutOut:
    """Open a checkout for Roundturn Pro and say where to pay. A second click within half an hour lands on the same one."""
    try:
        return billing.start_checkout(session.user_id, ip_hash=_ip(request, config))
    except BillingRefusal as problem:
        raise _refused(problem) from None


@router.post("/cancel", response_model=BillingOut, dependencies=[Limited])
def cancel(billing: BillingDep, session: WriteSessionDep, request: Request, config: ConfigDep) -> BillingOut:
    """Stop the next renewal. Full access stays to the end of the period paid for; nothing is refunded or deleted."""
    try:
        return billing.cancel(session.user_id, ip_hash=_ip(request, config))
    except BillingRefusal as problem:
        raise _refused(problem) from None


@router.post("/resume", response_model=BillingOut, dependencies=[Limited])
def resume(billing: BillingDep, session: WriteSessionDep, request: Request, config: ConfigDep) -> BillingOut:
    """Undo a cancel before the period ends: the subscription renews again."""
    try:
        return billing.resume(session.user_id, ip_hash=_ip(request, config))
    except BillingRefusal as problem:
        raise _refused(problem) from None


@router.post("/portal", response_model=PortalOut, dependencies=[Limited])
def portal(billing: BillingDep, session: WriteSessionDep, request: Request, config: ConfigDep) -> PortalOut:
    """A fresh link to the provider's page for the card, invoices and receipts."""
    try:
        return billing.portal(session.user_id, ip_hash=_ip(request, config))
    except BillingRefusal as problem:
        raise _refused(problem) from None
