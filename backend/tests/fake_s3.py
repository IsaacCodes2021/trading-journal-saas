"""A small S3-compatible server on this machine, for testing `BucketFileStore` over real HTTP.

It checks what a real bucket checks: the request's signature (recomputed from the request as it arrived, with the secret it
was started with), that the payload hash header matches the body, that the clock is close, and that the host header names
this server. It answers PUT, GET and DELETE of an object and ListObjectsV2 with a prefix and pages. Failures can be
arranged: `fail_next`, `slow_next`, and `drop_next` (close the connection with no answer).

It is not a model of S3's every rule, only of what the store relies on; the signature itself is also checked against AWS's
published examples in `test_bucketstore_signing.py`, so the two do not share a mistake.
"""

from __future__ import annotations

import base64
import hashlib
import threading
import time
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qsl, unquote, urlsplit
from xml.sax.saxutils import escape

from tradejournal.bucketstore import authorization

NAMESPACE = "http://s3.amazonaws.com/doc/2006-03-01/"


class FakeBucket:
    def __init__(self, *, access_key: str, secret: str, bucket: str = "roundturn-files", region: str = "auto") -> None:
        self.access_key, self.secret, self.bucket, self.region = access_key, secret, bucket, region
        self.objects: dict[str, tuple[bytes, datetime]] = {}
        self.requests: list[
            tuple[str, str, dict[str, str]]
        ] = []  # (method, key, query) of every request that was signed right
        self.refused: list[str] = []  # the reasons for each request that was turned away
        self._failures: list[int] = []
        self._slow: list[float] = []
        self._drops = 0
        self._lock = threading.Lock()
        handler = self._handler()
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self._server.daemon_threads = True
        self.host = f"127.0.0.1:{self._server.server_address[1]}"
        self.url = f"http://{self.host}"
        self._thread = threading.Thread(target=self._server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()

    # -- arranging trouble --

    def fail_next(self, status: int, times: int = 1) -> None:
        with self._lock:
            self._failures.extend([status] * times)

    def slow_next(self, seconds: float) -> None:
        with self._lock:
            self._slow.append(seconds)

    def drop_next(self, times: int = 1) -> None:
        with self._lock:
            self._drops += times

    def put_directly(self, key: str, data: bytes) -> None:
        self.objects[key] = (data, datetime.now(UTC))

    def keys(self) -> list[str]:
        return sorted(self.objects)

    # -- the server --

    def _handler(self):
        bucket = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args):  # silence the default stderr line per request
                pass

            def _answer(self, status: int, body: bytes = b"", content_type: str = "application/xml") -> None:
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Connection", "close")
                self.end_headers()
                if self.command != "HEAD":
                    self.wfile.write(body)
                self.close_connection = True

            def _error(self, status: int, code: str, reason: str) -> None:
                bucket.refused.append(reason)
                self._answer(status, f"<Error><Code>{code}</Code><Message>{escape(reason)}</Message></Error>".encode())

            def _signed_right(self, body: bytes, path: str, query: dict[str, str]) -> bool:
                header = self.headers.get("Authorization", "")
                if not header.startswith("AWS4-HMAC-SHA256 "):
                    self._error(403, "AccessDenied", "no signature")
                    return False
                try:
                    fields = dict(part.strip().split("=", 1) for part in header[len("AWS4-HMAC-SHA256 ") :].split(","))
                    access_key = fields["Credential"].split("/")[0]
                    signed = fields["SignedHeaders"].split(";")
                    given = fields["Signature"]
                except (KeyError, ValueError):
                    self._error(400, "AuthorizationHeaderMalformed", "malformed header")
                    return False
                if access_key != bucket.access_key:
                    self._error(403, "InvalidAccessKeyId", "unknown key")
                    return False
                amz_date = self.headers.get("x-amz-date", "")
                try:
                    sent = datetime.strptime(amz_date, "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)
                except ValueError:
                    self._error(403, "AccessDenied", "bad date")
                    return False
                if abs((datetime.now(UTC) - sent).total_seconds()) > 300:
                    self._error(403, "RequestTimeTooSkewed", "clock skew")
                    return False
                payload = self.headers.get("x-amz-content-sha256", "")
                if payload != hashlib.sha256(body).hexdigest():
                    self._error(400, "XAmzContentSHA256Mismatch", "payload hash")
                    return False
                if self.headers.get("Host") != bucket.host:
                    self._error(403, "SignatureDoesNotMatch", "host")
                    return False
                expected = authorization(
                    method=self.command,
                    path=path,
                    query=query,
                    headers={name: self.headers.get(name, "") for name in signed},
                    payload_sha256=payload,
                    access_key_id=bucket.access_key,
                    secret_access_key=bucket.secret,
                    region=bucket.region,
                    amz_date=amz_date,
                )
                if expected.rsplit("Signature=", 1)[1] != given:
                    self._error(403, "SignatureDoesNotMatch", "signature")
                    return False
                if "host" not in signed or "x-amz-date" not in signed or "x-amz-content-sha256" not in signed:
                    self._error(403, "AccessDenied", "unsigned header")
                    return False
                return True

            def _handle(self) -> None:
                length = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(length) if length else b""
                parts = urlsplit(self.path)
                path = unquote(parts.path)
                query = dict(parse_qsl(parts.query, keep_blank_values=True))
                with bucket._lock:
                    drop = bucket._drops > 0
                    if drop:
                        bucket._drops -= 1
                    slow = bucket._slow.pop(0) if bucket._slow else 0.0
                    failure = bucket._failures.pop(0) if bucket._failures else 0
                if drop:
                    self.close_connection = True
                    self.connection.close()
                    return
                if slow:
                    time.sleep(slow)
                if failure:
                    self._error(failure, "InternalError", "arranged failure")
                    return
                if not self._signed_right(body, path, query):
                    return
                prefix = f"/{bucket.bucket}"
                if path != prefix and not path.startswith(prefix + "/"):
                    self._error(404, "NoSuchBucket", "no such bucket")
                    return
                key = path[len(prefix) + 1 :]
                bucket.requests.append((self.command, key, query))
                if self.command == "GET" and not key:
                    self._list(query)
                elif self.command == "PUT" and key:
                    bucket.objects[key] = (body, datetime.now(UTC))
                    self._answer(200)
                elif self.command == "GET" and key:
                    if key not in bucket.objects:
                        self._error(404, "NoSuchKey", "no such key")
                    else:
                        self._answer(200, bucket.objects[key][0], "application/octet-stream")
                elif self.command == "DELETE" and key:
                    bucket.objects.pop(key, None)
                    self._answer(204)
                else:
                    self._error(405, "MethodNotAllowed", "not supported")

            def _list(self, query: dict[str, str]) -> None:
                if query.get("list-type") != "2":
                    self._error(400, "InvalidArgument", "only list type 2")
                    return
                prefix = query.get("prefix", "")
                limit = min(int(query.get("max-keys", "1000")), 1000)
                after = base64.b64decode(query["continuation-token"]).decode() if "continuation-token" in query else ""
                matching = sorted(k for k in bucket.objects if k.startswith(prefix) and k > after)
                page, more = matching[:limit], len(matching) > limit
                items = "".join(
                    f"<Contents><Key>{escape(k)}</Key><LastModified>{bucket.objects[k][1].strftime('%Y-%m-%dT%H:%M:%S.000Z')}"
                    f"</LastModified><ETag>&quot;x&quot;</ETag><Size>{len(bucket.objects[k][0])}</Size>"
                    f"<StorageClass>STANDARD</StorageClass></Contents>"
                    for k in page
                )
                token = (
                    f"<NextContinuationToken>{base64.b64encode(page[-1].encode()).decode()}</NextContinuationToken>"
                    if more
                    else ""
                )
                xml = (
                    f'<?xml version="1.0" encoding="UTF-8"?><ListBucketResult xmlns="{NAMESPACE}">'
                    f"<Name>{bucket.bucket}</Name><Prefix>{escape(prefix)}</Prefix><KeyCount>{len(page)}</KeyCount>"
                    f"<MaxKeys>{limit}</MaxKeys><IsTruncated>{'true' if more else 'false'}</IsTruncated>{token}{items}"
                    f"</ListBucketResult>"
                )
                self._answer(200, xml.encode())

            do_GET = do_PUT = do_DELETE = _handle  # noqa: N815

        return Handler
