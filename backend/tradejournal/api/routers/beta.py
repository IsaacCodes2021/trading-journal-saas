"""Joining the beta: the one public form (design: the landing page's "Sign up for the beta").

A person gives a first name, an address and a favourite prop firm. If the address can have a code, one is made (the rules are
in the database, migration 0014) and emailed. **The page is told the same thing in every case**: a new address, one asked about
a moment ago, one that already has an account, one that is not an address the database accepts, and one whose email could not
be sent. So the form cannot be used to find out who has joined, and a person is never told "that failed" in a way that would let
a stranger see what happened to someone else's address. The reason for a failure goes to the log as a short code.

Not in the OpenAPI document (like `/auth/login`): the page uses a plain `fetch`, never the API client, whose 401 handling would
send the visitor to the identity provider. It needs no session, so it does what a signed-out request may do, and no more:
same-origin check, JSON only, a per-address rate limit, a hidden field that bots fill in, and a body limit.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request

from ...auth.csrf import check_public_post
from ..deps import ConfigDep, ServicesDep, limited_per_client
from ..emails import beta_code_email
from ..mailer import MailError
from ..schemas.beta import BetaJoinIn, BetaJoinOut

logger = logging.getLogger("tj.beta")

public = APIRouter(prefix="/api/v1", include_in_schema=False)


def same_origin_json(request: Request, config: ConfigDep) -> None:
    check_public_post(request, config.app_origin)


@public.post(
    "/beta/join",
    status_code=202,
    dependencies=[Depends(same_origin_json), limited_per_client(lambda limits: limits.beta_join)],
)
def join_the_beta(body: BetaJoinIn, request: Request, services: ServicesDep, config: ConfigDep) -> BetaJoinOut:
    mailer = request.app.state.mailer
    if services.beta is None or mailer is None:
        raise HTTPException(
            503, "Joining the beta is not open yet."
        )  # said before anything is made that nobody could receive
    if body.website.strip():
        return BetaJoinOut()  # a bot: the same answer, and nothing is done
    made = services.beta.join(first_name=body.first_name, email=body.email, prop_firm=body.prop_firm)
    if made is None:
        return BetaJoinOut()
    link = f"{config.app_origin}/invite?code={made.code}"
    try:
        mailer.send(
            beta_code_email(to=body.email.strip().lower(), first_name=body.first_name, code=made.code, link=link)
        )
    except MailError as problem:
        logger.warning("beta email not sent: %s", problem.reason)
        services.beta.email_result(made.signup_id, ok=False)
    else:
        services.beta.email_result(made.signup_id, ok=True)
    return BetaJoinOut()
