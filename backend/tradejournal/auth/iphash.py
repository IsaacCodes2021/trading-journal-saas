"""A keyed hash of a client address for the audit log: enough to see that two events came from one place, not to learn
where. The key is the server's session secret, so the hashes cannot be looked up in a table of addresses."""

from __future__ import annotations

import hashlib
import hmac


def hash_ip(secret: bytes, ip: str | None) -> str | None:
    return hmac.new(secret, ip.encode(), hashlib.sha256).hexdigest()[:32] if ip else None
