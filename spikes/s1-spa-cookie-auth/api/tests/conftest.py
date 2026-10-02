import html
import re
import sys
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import Settings, create_app  # noqa: E402

ORIGIN = "https://testserver"


@pytest.fixture
def dist(tmp_path):
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets" / "app.abc123.js").write_text("console.log('hi')", encoding="utf-8")
    (tmp_path / "index.html").write_text("<!doctype html><div id=root></div>", encoding="utf-8")
    return tmp_path


@pytest.fixture
def app(dist):
    return create_app(Settings(web_dist=dist, app_origin=ORIGIN))


@pytest.fixture
def client(app):
    return TestClient(app, base_url=ORIGIN)


def sign_in(client, who="alice", next_="/"):
    """Walk the whole redirect dance by hand and return the callback response."""
    r = client.get("/auth/login", params={"next": next_}, follow_redirects=False)
    assert r.status_code == 302
    r = client.get(r.headers["location"], follow_redirects=False)
    assert r.status_code == 200
    href = re.search(rf'id="signin-{who}" href="([^"]+)"', r.text).group(1)
    r = client.get(html.unescape(href), follow_redirects=False)
    assert r.status_code == 302
    cb = urlsplit(r.headers["location"])
    return client.get(f"{cb.path}?{cb.query}", follow_redirects=False)


def csrf_headers(client, **extra):
    token = client.get("/api/v1/session").json()["csrf_token"]
    return {"Origin": ORIGIN, "X-CSRF-Token": token, **extra}
