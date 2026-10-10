"""What the emails say (the wording is the design canvas's "Email: your beta code" and "Email: beta access ended").

Plain text first, then a small HTML version with inline styles only (mail clients ignore the rest) and system fonts. Every value
that came from a person is escaped. Nothing here has a tracking pixel or a link that is not ours.
"""

from __future__ import annotations

from html import escape

from .mailer import Email

SUPPORT = "hello@roundturn.app"
CODE_DAYS = 14  # how long an unused personal code works (migration 0014: beta_join)
ACCESS_DAYS = 30


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
        f"Use it within {CODE_DAYS} days, or ask for a new one.\n\n"
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
        f"<li>Use it within {CODE_DAYS} days, or ask for a new one.</li>"
        "</ul>"
        f'<p style="color:#566476;font-size:14px">Roundturn is in development, so expect rough edges. Reply to this email or '
        f"write to {SUPPORT}. We answer on a best-effort basis.</p>"
        '<p style="color:#566476;font-size:12px">You are getting this because you asked to join the Roundturn beta. If that '
        "was not you, ignore this email and nothing will happen. Roundturn analyzes data you upload and is not investment "
        "advice.</p></div>"
    )
    return Email(to=to, subject=subject, text=text, html=html)
