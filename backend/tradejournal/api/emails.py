"""What the emails say (the wording is the design canvas's "Email: your beta code" and "Email: beta access ended").

Plain text first, then a small HTML version with inline styles only (mail clients ignore the rest) and system fonts. Every value
that came from a person is escaped. Nothing here has a tracking pixel or a link that is not ours.
"""

from __future__ import annotations

from html import escape

from .mailer import Email

SUPPORT = "hello@roundturn.app"
CODE_HOURS = 24  # how long an unused personal code works (migration 0018: beta_join)
ACCESS_DAYS = 30
PLAN_URL = "https://app.roundturn.app/settings/plan"  # where Subscribe is (the emails go out from a job with no origin)
PRICE_LINE = (
    "Roundturn Pro is USD 15 a month, plus applicable tax. Cancel any time in Settings; payments are not refunded."
)


def beta_code_email(*, to: str, first_name: str, code: str, link: str) -> Email:
    subject = "Your Roundturn beta code"
    text = (
        f"Hi {first_name},\n\n"
        "Thanks for joining the Roundturn beta. Here is your sign-up code:\n\n"
        f"    {code}\n\n"
        f"Create your account: {link}\n"
        "Or go to the sign-up page and enter the code.\n\n"
        "It works once and is tied to this email address.\n"
        f"It gives you {ACCESS_DAYS} days of full access, counted from the day you create your account.\n"
        f"Use it within {CODE_HOURS} hours, or ask for a new one.\n\n"
        f"Roundturn is in development, so expect rough edges. Reply to this email or write to {SUPPORT}. "
        "We answer on a best-effort basis.\n\n"
        "You are getting this because you asked to join the Roundturn beta. If that was not you, ignore this email and "
        "nothing will happen. Roundturn analyzes data you upload and is not investment advice.\n"
    )
    name, code_html, link_html = escape(first_name), escape(code), escape(link, quote=True)
    html = (
        "<div style=\"font-family:system-ui,-apple-system,'Segoe UI',sans-serif;font-size:16px;line-height:1.55;"
        'color:#17202b;max-width:560px">'
        f"<p>Hi {name},</p>"
        "<p>Thanks for joining the Roundturn beta. Here is your sign-up code:</p>"
        '<p style="font-family:ui-monospace,Consolas,monospace;font-size:28px;letter-spacing:0.06em;'
        f'padding:16px 20px;border:1px solid #b6c1d1;border-radius:10px;background:#f4f6fa;text-align:center">{code_html}</p>'
        f'<p><a href="{link_html}" style="display:inline-block;padding:14px 22px;border-radius:10px;background:#1f5fd1;'
        'color:#ffffff;text-decoration:none;font-weight:600">Create your account</a></p>'
        "<ul>"
        "<li>It works once and is tied to this email address.</li>"
        f"<li>It gives you {ACCESS_DAYS} days of full access, counted from the day you create your account.</li>"
        f"<li>Use it within {CODE_HOURS} hours, or ask for a new one.</li>"
        "</ul>"
        f'<p style="color:#566476;font-size:14px">Roundturn is in development, so expect rough edges. Reply to this email or '
        f"write to {SUPPORT}. We answer on a best-effort basis.</p>"
        '<p style="color:#566476;font-size:12px">You are getting this because you asked to join the Roundturn beta. If that '
        "was not you, ignore this email and nothing will happen. Roundturn analyzes data you upload and is not investment "
        "advice.</p></div>"
    )
    return Email(to=to, subject=subject, text=text, html=html)


def access_ended_email(*, to: str, first_name: str, billing: bool = False) -> Email:
    """Sent once when a person's beta access ends, by running out or by an operator (design: "Email: beta access ended").
    With billing off the words are exactly the approved ones; with it on, "paid plans are not open yet" becomes a Subscribe
    line (the billing design)."""
    subject = "Your Roundturn beta access has ended"
    plans_line = (
        f"You can keep importing and editing by subscribing: {PLAN_URL}\n{PRICE_LINE}\n\n"
        if billing
        else "Paid plans are not open yet, so there is nothing to sign up for today.\n\n"
    )
    plans_html = (
        f'<p><a href="{PLAN_URL}" style="display:inline-block;padding:14px 22px;border-radius:10px;background:#1f5fd1;'
        f'color:#ffffff;text-decoration:none;font-weight:600">Subscribe to keep going</a></p><p>{PRICE_LINE}</p>'
        if billing
        else "<p>Paid plans are not open yet, so there is nothing to sign up for today.</p>"
    )
    text = (
        f"Hi {first_name},\n\n"
        "Your beta access to Roundturn has ended. Thank you for testing it and for any notes you sent.\n\n"
        "Your journal is still there. You can look at your trades, notes and reports and download everything as a ZIP. "
        "Importing and editing are paused.\n\n"
        f"{plans_line}"
        "Want your data out first? Sign in, open Settings, then Data and privacy, and choose Download my data. You can "
        "also delete your account there.\n\n"
        f"Questions? Reply to this email or write to {SUPPORT}.\n\n"
        "You are getting this because your Roundturn beta access ended. Roundturn analyzes data you upload and is not "
        "investment advice.\n"
    )
    name = escape(first_name)
    html = (
        "<div style=\"font-family:system-ui,-apple-system,'Segoe UI',sans-serif;font-size:16px;line-height:1.55;"
        'color:#17202b;max-width:560px">'
        f"<p>Hi {name},</p>"
        "<p>Your beta access to Roundturn has ended. Thank you for testing it and for any notes you sent.</p>"
        "<p>Your journal is still there. You can look at your trades, notes and reports and download everything as a ZIP. "
        "Importing and editing are paused.</p>"
        f"{plans_html}"
        '<p style="padding:14px 16px;border:1px solid #d3dae5;border-radius:10px;background:#f4f6fa;color:#566476;'
        'font-size:14px">Want your data out first? Sign in, open Settings, then Data and privacy, and choose Download my '
        "data. You can also delete your account there.</p>"
        f'<p style="color:#566476;font-size:14px">Questions? Reply to this email or write to {SUPPORT}.</p>'
        '<p style="color:#566476;font-size:12px">You are getting this because your Roundturn beta access ended. Roundturn '
        "analyzes data you upload and is not investment advice.</p></div>"
    )
    return Email(to=to, subject=subject, text=text, html=html)


# ---- billing (the billing design, "Emails"; sent only with billing switched on) ------------------------------------------


def _billing_email(*, to: str, subject: str, first_name: str, paragraphs: list[str], button: str, why: str) -> Email:
    """One of the billing emails: plain paragraphs, one button to the Plan & billing page, the same footer as the others."""
    text = (
        f"Hi {first_name},\n\n"
        + "".join(f"{p}\n\n" for p in paragraphs)
        + f"{button}: {PLAN_URL}\n\n"
        + f"Questions? Reply to this email or write to {SUPPORT}.\n\n"
        + f"{why} Roundturn analyzes data you upload and is not investment advice.\n"
    )
    html = (
        "<div style=\"font-family:system-ui,-apple-system,'Segoe UI',sans-serif;font-size:16px;line-height:1.55;"
        'color:#17202b;max-width:560px">'
        f"<p>Hi {escape(first_name)},</p>"
        + "".join(f"<p>{escape(p)}</p>" for p in paragraphs)
        + f'<p><a href="{PLAN_URL}" style="display:inline-block;padding:14px 22px;border-radius:10px;background:#1f5fd1;'
        f'color:#ffffff;text-decoration:none;font-weight:600">{escape(button)}</a></p>'
        f'<p style="color:#566476;font-size:14px">Questions? Reply to this email or write to {SUPPORT}.</p>'
        f'<p style="color:#566476;font-size:12px">{escape(why)} Roundturn analyzes data you upload and is not investment '
        "advice.</p></div>"
    )
    return Email(to=to, subject=subject, text=text, html=html)


def _day(moment) -> str:
    return f"{moment:%b} {moment.day}, {moment.year}"


def trial_reminder_email(*, to: str, first_name: str, ends_at) -> Email:
    """Two days before the trial ends (question 7 of the billing design)."""
    return _billing_email(
        to=to,
        subject="Your Roundturn trial ends in 2 days",
        first_name=first_name,
        paragraphs=[
            f"Your free trial of Roundturn Pro ends on {_day(ends_at)} (UTC).",
            "To keep importing and editing after that, subscribe before it ends. The first payment is taken when the "
            "trial ends, not before.",
            PRICE_LINE,
            "If you do nothing, your journal becomes read-only: everything stays there to look at and download.",
        ],
        button="Subscribe",
        why="You are getting this because your Roundturn trial is ending.",
    )


def trial_ended_email(*, to: str, first_name: str) -> Email:
    """The trial ran out and nothing else keeps the person on Pro (the "access ended" job, worded for a trial)."""
    return _billing_email(
        to=to,
        subject="Your Roundturn trial has ended",
        first_name=first_name,
        paragraphs=[
            "Your free trial of Roundturn Pro has ended.",
            "Your journal is still there. You can look at your trades, notes and reports and download everything as a "
            "ZIP. Importing and editing are paused until you subscribe.",
            PRICE_LINE,
        ],
        button="Subscribe",
        why="You are getting this because your Roundturn trial ended.",
    )


def subscription_ended_email(*, to: str, first_name: str) -> Email:
    """A cancelled month ran out, or the grace after a failed payment did."""
    return _billing_email(
        to=to,
        subject="Your Roundturn subscription has ended",
        first_name=first_name,
        paragraphs=[
            "Your Roundturn Pro subscription has ended.",
            "Your journal is still there. You can look at your trades, notes and reports and download everything as a "
            "ZIP. Importing and editing are paused until you subscribe again.",
            "Nothing was deleted, and nothing more will be charged.",
        ],
        button="Subscribe again",
        why="You are getting this because your Roundturn subscription ended.",
    )
