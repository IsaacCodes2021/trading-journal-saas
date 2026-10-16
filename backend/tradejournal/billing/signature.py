"""The webhook signature, as Paddle sends it and as the fake provider copies (Paddle guide section 5.4).

The header is `ts=<unix seconds>;h1=<hex>`, where h1 is HMAC-SHA256 of "<ts>:<raw body>" under the endpoint's secret. During
a secret's rotation two h1 values may come; any one that matches is enough. The timestamp must be close to now, which,
with each event's id stored once, makes a replay do nothing.
"""

from __future__ import annotations

import hashlib
import hmac

TOLERANCE_SECONDS = 5  # Paddle's own libraries allow this much


def sign(secret: str, raw_body: bytes, ts: int) -> str:
    digest = hmac.new(secret.encode(), str(ts).encode() + b":" + raw_body, hashlib.sha256).hexdigest()
    return f"ts={ts};h1={digest}"


def verify(header: str | None, raw_body: bytes, secret: str, *, now: int, tolerance: int = TOLERANCE_SECONDS) -> bool:
    """True when one of the header's h1 values is the body's signature and its timestamp is within `tolerance` of `now`."""
    if not header or not secret or len(header) > 1000:
        return False
    pairs = [part.split("=", 1) for part in header.split(";") if "=" in part]
    ts = next((value for key, value in pairs if key.strip() == "ts"), "")
    signatures = [value.strip() for key, value in pairs if key.strip() == "h1"]
    if not ts.isdigit() or len(ts) > 12 or abs(now - int(ts)) > tolerance or not signatures:
        return False
    expected = hmac.new(secret.encode(), ts.encode() + b":" + raw_body, hashlib.sha256).hexdigest()
    return any(hmac.compare_digest(expected.encode(), candidate.encode()) for candidate in signatures)
