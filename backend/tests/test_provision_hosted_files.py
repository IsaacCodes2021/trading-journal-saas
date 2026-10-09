"""The settings file written by the hosted database setup: which connection string goes where, and that it reads back."""

from urllib.parse import urlsplit

from tradejournal.pg import provision_hosted as hosted

SETUP = "postgresql://admin:pw@public.example.com:5432/roundturn?sslmode=require"
PASSWORDS = {role: f"pw{n}" for n, role in enumerate(hosted.ROLES)}
SECRET = "ab" * 32


def lines(text: str) -> dict[str, str]:
    """key -> value for the lines that set something live (not commented out)."""
    out = {}
    for line in text.splitlines():
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            out[key] = value
    return out


def test_a_role_url_swaps_the_user_and_keeps_the_address_and_the_database():
    url = hosted.url_for(SETUP, "app_rw", "secret", host=None)
    parts = urlsplit(url)
    assert (parts.username, parts.password, parts.hostname, parts.port, parts.path) == (
        "app_rw",
        "secret",
        "public.example.com",
        5432,
        "/roundturn",
    )
    assert parts.query == "sslmode=require"  # the public address needs it


def test_a_url_for_the_private_address_drops_the_public_ones_port_and_query():
    parts = urlsplit(hosted.url_for(SETUP, "app_rw", "secret", host="dpg-abc-a"))
    assert (parts.hostname, parts.port, parts.query, parts.path) == ("dpg-abc-a", None, "", "/roundturn")


def test_a_password_with_odd_characters_survives_the_url_and_the_file(tmp_path):
    odd = "p@ss/w:rd#?%"
    assert urlsplit(hosted.url_for(SETUP, "app_rw", odd, host=None)).hostname == "public.example.com"
    path = tmp_path / ".env.hosted"
    path.write_text(
        hosted.render_file(SETUP, {**PASSWORDS, "app_rw": odd}, None, session_secret=SECRET), encoding="utf-8"
    )
    assert hosted.read_passwords(path)["app_rw"] == odd


def test_only_what_the_web_service_needs_is_live_in_the_file():
    text = hosted.render_file(SETUP, PASSWORDS, "dpg-abc-a", session_secret=SECRET)
    assert set(lines(text)) == {"TJ_DATABASE_URL", "TJ_LOGIN_DATABASE_URL", "TJ_SESSION_SECRET"}
    assert (
        "TJ_OWNER_URL" in text and "TJ_ADMIN_DATABASE_URL" in text and "TJ_SWEEP_DATABASE_URL" in text
    )  # kept, but off


def test_the_web_urls_use_the_private_address_and_the_owner_url_the_public_one():
    text = hosted.render_file(SETUP, PASSWORDS, "dpg-abc-a", session_secret=SECRET)
    live = lines(text)
    assert urlsplit(live["TJ_DATABASE_URL"]).hostname == "dpg-abc-a"
    assert urlsplit(live["TJ_LOGIN_DATABASE_URL"]).username == "app_login"
    owner = next(line for line in text.splitlines() if line.startswith("# TJ_OWNER_URL="))
    assert urlsplit(owner.removeprefix("# TJ_OWNER_URL=")).hostname == "public.example.com"


def test_the_file_reads_back_to_the_same_passwords_including_the_commented_ones(tmp_path):
    path = tmp_path / ".env.hosted"
    path.write_text(hosted.render_file(SETUP, PASSWORDS, "dpg-abc-a", session_secret=SECRET), encoding="utf-8")
    assert hosted.read_passwords(path) == PASSWORDS
    assert hosted.read_settings(path)["TJ_SESSION_SECRET"] == SECRET


def test_a_line_for_another_role_is_not_taken_as_a_roles_password(tmp_path):
    path = tmp_path / ".env.hosted"
    path.write_text("TJ_DATABASE_URL=postgresql://someone_else:x@h/db\n", encoding="utf-8")
    assert hosted.read_passwords(path) == {}


def test_a_missing_file_gives_no_passwords(tmp_path):
    assert hosted.read_passwords(tmp_path / "nope") == {}
