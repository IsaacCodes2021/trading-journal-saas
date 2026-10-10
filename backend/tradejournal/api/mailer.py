"""Sending email: the beta code, and the notice that access has ended.

One small interface, so the provider can change without touching what is sent or why:

    Mailer.send(Email(...))   raises MailError("<short reason code>") when it could not be sent

`ResendMailer` talks to Resend over HTTPS (one call per message, an API key as a bearer token). It follows `auth/kinde_admin.py`:
the HTTP client is injected, every failure is turned into a short reason code, and nothing the provider said, no address and no
key ever goes into an error, a log line or a return value. `LogMailer` is for development: it writes the message to the log
instead of sending it, so the sign-up code can be read from the console; the production app never uses it (a server with no
provider configured answers 503 where an email would be needed).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Protocol

import httpx

logger = logging.getLogger("tj.mail")

DEFAULT_API_URL = "https://api.resend.com"


class MailError(Exception):
    """The message was not sent. `reason` is a short code ("mail_network", "mail_refused_422", "mail_unavailable_503"),
    never anything the provider said."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class Email:
    to: str
    subject: str
    text: str
    html: str | None = None


class Mailer(Protocol):
    def send(self, email: Email) -> None: ...


class ResendMailer:
    def __init__(
        self, *, api_key: str, sender: str, base_url: str = DEFAULT_API_URL, http: httpx.Client | None = None
    ) -> None:
        self._key = api_key
        self._sender = sender
        self._url = f"{base_url.rstrip('/')}/emails"
        self._http = http or httpx.Client(timeout=10.0)

    def send(self, email: Email) -> None:
        payload: dict[str, object] = {
            "from": self._sender,
            "to": [email.to],
            "subject": email.subject,
            "text": email.text,
        }
        if email.html is not None:
            payload["html"] = email.html
        try:
            response = self._http.post(self._url, json=payload, headers={"Authorization": f"Bearer {self._key}"})
        except httpx.HTTPError:
            raise MailError("mail_network") from None
        status = response.status_code
        if 200 <= status < 300:
            return
        if status == 429 or status >= 500:
            raise MailError(f"mail_unavailable_{status}")
        raise MailError(f"mail_refused_{status}")


class LogMailer:
    """Development only: the message goes to the log, not the network."""

    def send(self, email: Email) -> None:
        logger.info("email (not sent: development)\nto: %s\nsubject: %s\n\n%s", email.to, email.subject, email.text)
