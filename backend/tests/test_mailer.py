"""Sending email (api/mailer.py) and what the beta code email says (api/emails.py)."""

from __future__ import annotations

import json

import httpx
import pytest

from tradejournal.api.emails import access_ended_email, beta_code_email
from tradejournal.api.mailer import Email, LogMailer, MailError, ResendMailer

KEY = "re_" + "test" + "key0123456789"  # made up
SENDER = "Roundturn <beta@roundturn.app>"
MESSAGE = Email(to="alex@example.test", subject="Hello", text="plain body", html="<p>html body</p>")


def mailer_with(handler, **kwargs) -> ResendMailer:
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return ResendMailer(api_key=KEY, sender=SENDER, http=client, **kwargs)


def test_a_message_is_one_authenticated_post_with_both_bodies():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"id": "abc"})

    mailer_with(handler).send(MESSAGE)
    [request] = seen
    assert request.method == "POST" and str(request.url) == "https://api.resend.com/emails"
    assert request.headers["authorization"] == f"Bearer {KEY}"
    assert json.loads(request.content) == {
        "from": SENDER,
        "to": ["alex@example.test"],
        "subject": "Hello",
        "text": "plain body",
        "html": "<p>html body</p>",
    }


def test_a_plain_text_message_sends_no_html_field():
    seen = []
    mailer_with(lambda r: seen.append(r) or httpx.Response(200)).send(Email(to="a@b.cc", subject="s", text="t"))
    assert "html" not in json.loads(seen[0].content)


def test_the_providers_address_can_be_changed_for_a_test_double():
    seen = []
    mailer = mailer_with(lambda r: seen.append(r) or httpx.Response(200), base_url="http://127.0.0.1:9999/")
    mailer.send(MESSAGE)
    assert str(seen[0].url) == "http://127.0.0.1:9999/emails"


@pytest.mark.parametrize(
    ("status", "reason"),
    [(400, "mail_refused_400"), (401, "mail_refused_401"), (403, "mail_refused_403"), (422, "mail_refused_422")],
)
def test_a_refusal_is_a_short_code_and_not_the_providers_words(status, reason):
    def handler(request):
        return httpx.Response(status, json={"message": f"the key {KEY} for alex@example.test is wrong"})

    with pytest.raises(MailError) as caught:
        mailer_with(handler).send(MESSAGE)
    assert caught.value.reason == reason
    assert (
        KEY not in str(caught.value)
        and "alex@example.test" not in str(caught.value)
        and "wrong" not in str(caught.value)
    )


@pytest.mark.parametrize(
    ("status", "reason"), [(429, "mail_unavailable_429"), (500, "mail_unavailable_500"), (503, "mail_unavailable_503")]
)
def test_a_busy_or_broken_provider_is_a_different_code_so_it_can_be_retried(status, reason):
    with pytest.raises(MailError) as caught:
        mailer_with(lambda r: httpx.Response(status, text="try later")).send(MESSAGE)
    assert caught.value.reason == reason


def test_a_network_failure_is_a_code_and_carries_nothing():
    def handler(request):
        raise httpx.ConnectError(f"cannot reach host with {KEY}")

    with pytest.raises(MailError) as caught:
        mailer_with(handler).send(MESSAGE)
    assert caught.value.reason == "mail_network" and KEY not in repr(caught.value)
    assert caught.value.__cause__ is None and caught.value.__suppress_context__  # `from None`: no chain to print


def test_the_log_mailer_sends_nothing_and_logs_the_message(caplog):
    with caplog.at_level("INFO", logger="tj.mail"):
        LogMailer().send(MESSAGE)
    assert "plain body" in caplog.text and "Hello" in caplog.text


# ---- what the beta code email says ----------------------------------------------------------------------------------


def make_email(**over):
    values = {
        "to": "alex@example.test",
        "first_name": "Alex",
        "code": "TJ-7KQ2-M9XA",
        "link": "https://app.roundturn.app/invite?code=TJ-7KQ2-M9XA",
    }
    return beta_code_email(**(values | over))


def test_the_email_has_the_code_the_link_and_the_terms_of_the_beta_in_both_forms():
    email = make_email()
    assert email.subject == "Your Roundturn beta code" and email.to == "alex@example.test"
    for body in (email.text, email.html):
        assert "TJ-7KQ2-M9XA" in body and "https://app.roundturn.app/invite?code=TJ-7KQ2-M9XA" in body
        assert "30 days of full access" in body and "24 hours" in body and "hello@roundturn.app" in body
        assert "not investment advice" in body
        assert "once" in body


def test_what_a_person_typed_is_escaped_in_the_html_and_the_link_attribute():
    email = make_email(first_name='<script>alert("x")</script>', link='https://x.test/?a="><b>')
    assert "<script>" not in email.html and "&lt;script&gt;" in email.html
    assert '"><b>' not in email.html


def test_there_is_no_tracking_pixel_or_foreign_resource_in_the_html():
    html = make_email().html
    assert "<img" not in html and "http://" not in html
    assert html.count("https://") == 1  # the one link, which is ours


def test_the_access_ended_email_says_what_happened_what_is_kept_and_how_to_get_the_data_out():
    email = access_ended_email(to="alex@example.test", first_name="Alex")
    assert email.subject == "Your Roundturn beta access has ended" and email.to == "alex@example.test"
    for body in (email.text, email.html):
        assert "Hi Alex" in body and "has ended" in body and "still there" in body
        assert "download everything as a ZIP" in body and "hello@roundturn.app" in body
        assert "not investment advice" in body


def test_the_access_ended_email_escapes_the_name_and_has_no_remote_resources():
    email = access_ended_email(to="a@b.cc", first_name="<b>Al</b>")
    assert "<b>Al</b>" not in email.html and "&lt;b&gt;Al&lt;/b&gt;" in email.html
    assert "<img" not in email.html and "http" not in email.html
