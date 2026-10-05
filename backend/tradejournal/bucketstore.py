"""A private bucket as the file store (doc 03 section 6): Cloudflare R2, or anything else that speaks the S3 API.

`filestore.LocalFileStore` keeps files in a folder, which a host with no persistent disk cannot do and which a second
process (the nightly sweep) cannot share. This is the same five methods over HTTPS, so nothing above `filestore.py`
changes: `api/app.py` and `pg/sweep.py` choose it when the four `TJ_S3_*` settings are given.

It is written with the standard library only (`http.client`, `hmac`, `hashlib`): a request is signed with AWS Signature
Version 4, which is a page of arithmetic, and a dependency would be code that handles the secret key on every request.
The signature is checked in the tests against the worked examples AWS publishes, so a mistake in it cannot hide behind a
fake server that makes the same mistake.

Addressing is path style (`https://<account>.r2.cloudflarestorage.com/<bucket>/<key>`), which R2 and S3 both accept.
Pictures are never linked to the bucket directly: `GET /files/{id}` reads them through the server, so the content
security policy stays `img-src 'self'` and a link cannot outlive the person's session.

Failures are `StoreUnavailable` (an `OSError`: the bucket cannot be reached, refused the credentials, or kept failing),
which the API answers with a 503 and a request to try again. A missing file is `FileMissing`, as with a folder.
"""

from __future__ import annotations

import hashlib
import hmac
import http.client
import re
import time
import xml.etree.ElementTree as ET  # noqa: S405 - the answers come from our own bucket over TLS, never from a request
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from urllib.parse import quote, urlsplit

from .filestore import FileMissing, StoredFile, check_key

ALGORITHM = "AWS4-HMAC-SHA256"
EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()
PAGE_SIZE = 1000  # the most keys a bucket lists in one answer
_UNRESERVED = "-_.~"  # what a signed request leaves unencoded (RFC 3986)

_BUCKET = re.compile(r"[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]")
_REGION = re.compile(r"[a-z0-9-]{1,32}")
_ACCESS_KEY = re.compile(r"[A-Za-z0-9_-]{1,128}")
_SECRET = re.compile(r"[\x21-\x7e]{1,256}")  # printable, no spaces: it goes into a header's arithmetic, never into text


class StoreUnavailable(OSError):
    """The bucket could not be reached or refused the request. Never carries the answer or the credentials."""


@dataclass(frozen=True)
class BucketSettings:
    endpoint: str  # https://<account>.r2.cloudflarestorage.com, no path
    bucket: str
    access_key_id: str
    secret_access_key: str = field(repr=False)  # a secret: host secret store only, never printed or logged
    region: str = "auto"  # R2's name for "wherever"; S3 needs its real region

    def __post_init__(self) -> None:
        parts = urlsplit(self.endpoint)
        if parts.scheme not in ("http", "https") or not parts.netloc or parts.path or parts.query or parts.fragment:
            raise ValueError("TJ_S3_ENDPOINT must look like https://<account>.r2.cloudflarestorage.com (no path)")
        if not _BUCKET.fullmatch(self.bucket):
            raise ValueError("TJ_S3_BUCKET is not a bucket name (3 to 63 lower-case letters, digits, dots and dashes)")
        if not _REGION.fullmatch(self.region):
            raise ValueError("TJ_S3_REGION must be a region name such as auto or us-east-1")
        if not _ACCESS_KEY.fullmatch(self.access_key_id):
            raise ValueError("TJ_S3_ACCESS_KEY_ID is not an access key id")
        if not _SECRET.fullmatch(self.secret_access_key):
            raise ValueError("TJ_S3_SECRET_ACCESS_KEY is empty or holds spaces or control characters")

    @property
    def https(self) -> bool:
        return urlsplit(self.endpoint).scheme == "https"

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> BucketSettings | None:
        """The four settings together, or none (then there is no bucket). The region is optional."""
        names = ("TJ_S3_ENDPOINT", "TJ_S3_BUCKET", "TJ_S3_ACCESS_KEY_ID", "TJ_S3_SECRET_ACCESS_KEY")
        given = [env.get(name, "").strip() for name in names]
        if not any(given):
            return None
        if not all(given):
            raise ValueError(
                "TJ_S3_ENDPOINT, TJ_S3_BUCKET, TJ_S3_ACCESS_KEY_ID and TJ_S3_SECRET_ACCESS_KEY go together"
            )
        region = env.get("TJ_S3_REGION", "").strip() or "auto"
        return cls(given[0], given[1], given[2], given[3], region)


# ---- Signature Version 4 ------------------------------------------------------------------------------------------------


def _hmac(key: bytes, text: str) -> bytes:
    return hmac.new(key, text.encode("utf-8"), hashlib.sha256).digest()


def canonical_query(query: Mapping[str, str]) -> str:
    pairs = sorted((quote(k, safe=_UNRESERVED), quote(v, safe=_UNRESERVED)) for k, v in query.items())
    return "&".join(f"{k}={v}" for k, v in pairs)


def authorization(
    *,
    method: str,
    path: str,
    query: Mapping[str, str],
    headers: Mapping[str, str],
    payload_sha256: str,
    access_key_id: str,
    secret_access_key: str,
    region: str,
    amz_date: str,
    service: str = "s3",
) -> str:
    """The `Authorization` header for a request. `headers` is every header that is to be signed (at least `host`,
    `x-amz-date` and `x-amz-content-sha256`); `path` is the unencoded path; `amz_date` is `YYYYMMDDTHHMMSSZ`."""
    day = amz_date[:8]
    lowered = {name.lower(): " ".join(value.split()) for name, value in headers.items()}
    names = sorted(lowered)
    canonical_headers = "".join(f"{name}:{lowered[name]}\n" for name in names)
    signed = ";".join(names)
    request = "\n".join(
        [
            method,
            quote(path, safe="/" + _UNRESERVED),
            canonical_query(query),
            canonical_headers,
            signed,
            payload_sha256,
        ]
    )
    scope = f"{day}/{region}/{service}/aws4_request"
    to_sign = "\n".join([ALGORITHM, amz_date, scope, hashlib.sha256(request.encode("utf-8")).hexdigest()])
    key = _hmac(_hmac(_hmac(_hmac(("AWS4" + secret_access_key).encode("utf-8"), day), region), service), "aws4_request")
    signature = hmac.new(key, to_sign.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{ALGORITHM} Credential={access_key_id}/{scope}, SignedHeaders={signed}, Signature={signature}"


# ---- the store ----------------------------------------------------------------------------------------------------------


def _code(answer: bytes) -> str:
    """The `<Code>` of an error answer (`NoSuchKey`, `NoSuchBucket`, `SignatureDoesNotMatch`), or "" when there is none."""
    found = re.search(rb"<Code>([A-Za-z]{1,64})</Code>", answer[:4096])
    return found.group(1).decode("ascii") if found else ""


def _name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]  # an answer's elements carry the S3 namespace; only the local name matters


class BucketFileStore:
    """The five methods of `filestore.FileStore` over the bucket's API. One connection per request: a screenshot upload
    is rare next to what it costs to keep sockets alive across threads, and a dropped socket can never poison the next call."""

    def __init__(
        self,
        settings: BucketSettings,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        sleep: Callable[[float], None] = time.sleep,
        attempts: int = 3,
        timeout: float = 30.0,
        page_size: int = PAGE_SIZE,
    ) -> None:
        self._settings = settings
        self._clock = clock
        self._sleep = sleep
        self._attempts = max(1, attempts)
        self._timeout = timeout
        self._page_size = page_size
        self._parts = urlsplit(settings.endpoint)

    # -- the interface --

    def put(self, key: str, data: bytes) -> None:
        self._call("PUT", check_key(key), body=data)

    def get(self, key: str) -> bytes:
        status, data = self._call("GET", check_key(key), missing_ok=True)
        if status == 404:
            raise FileMissing(key)
        return data

    def delete(self, key: str) -> None:
        self._call("DELETE", check_key(key), missing_ok=True)  # deleting what is not there is fine, as with a folder

    def delete_prefix(self, prefix: str) -> int:
        """Remove every file under a folder-like prefix (`u/<user id>/`). Returns how many files. An empty prefix is
        refused, as for a folder, so a bug can never ask for the whole bucket."""
        folder = check_key(prefix.rstrip("/"))  # before anything is listed: `list("")` means every file
        keys = [f.key for f in self.list(folder)]  # all of them first: a listing is not safe to read while deleting
        for key in keys:
            self._call("DELETE", check_key(key), missing_ok=True)
        return len(keys)

    def list(self, prefix: str = "") -> Iterator[StoredFile]:
        folder = check_key(prefix.rstrip("/")) + "/" if prefix else ""
        token: str | None = None
        while True:
            query = {"list-type": "2", "max-keys": str(self._page_size)}
            if folder:
                query["prefix"] = folder
            if token:
                query["continuation-token"] = token
            _, body = self._call("GET", "", query=query)
            try:
                root = ET.fromstring(body)  # noqa: S314
            except ET.ParseError:
                raise StoreUnavailable("the bucket's listing could not be read") from None
            truncated = False
            token = None
            for element in root:
                name = _name(element.tag)
                if name == "IsTruncated":
                    truncated = (element.text or "").strip().lower() == "true"
                elif name == "NextContinuationToken":
                    token = element.text or None
                elif name == "Contents":
                    yield self._stored(element)
            if not truncated or not token:
                return

    # -- the request --

    @staticmethod
    def _stored(element: ET.Element) -> StoredFile:
        fields = {_name(child.tag): (child.text or "") for child in element}
        try:
            modified = datetime.fromisoformat(fields["LastModified"].replace("Z", "+00:00"))
            return StoredFile(key=fields["Key"], size=int(fields["Size"]), modified=modified.astimezone(UTC))
        except (KeyError, ValueError):
            raise StoreUnavailable("the bucket's listing could not be read") from None

    def _call(
        self,
        method: str,
        key: str,
        *,
        query: Mapping[str, str] | None = None,
        body: bytes = b"",
        missing_ok: bool = False,
    ) -> tuple[int, bytes]:
        """One signed request, tried again after a pause when the bucket or the network fails (never when it says no).
        Answers (status, body) for a 2xx, or for a 404 that says *the key* is missing when `missing_ok` (a 404 for a
        bucket that is not there, or any other 404, is an error: it must never read as "that file is gone", or a wrong
        bucket name would make every picture vanish and every deletion look done); anything else is `StoreUnavailable`."""
        path = f"/{self._settings.bucket}" + (f"/{key}" if key else "")
        query = query or {}
        target = quote(path, safe="/" + _UNRESERVED) + (f"?{canonical_query(query)}" if query else "")
        payload = hashlib.sha256(body).hexdigest() if body else EMPTY_SHA256
        problem = "no answer"
        for attempt in range(self._attempts):
            if attempt:
                self._sleep(0.25 * 2 ** (attempt - 1))
            amz_date = self._clock().astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
            headers = {"Host": self._parts.netloc, "x-amz-content-sha256": payload, "x-amz-date": amz_date}
            headers["Authorization"] = authorization(
                method=method,
                path=path,
                query=query,
                headers=headers,
                payload_sha256=payload,
                access_key_id=self._settings.access_key_id,
                secret_access_key=self._settings.secret_access_key,
                region=self._settings.region,
                amz_date=amz_date,
            )
            if body:
                headers["Content-Type"] = "application/octet-stream"
            connection_type = http.client.HTTPSConnection if self._settings.https else http.client.HTTPConnection
            connection = connection_type(self._parts.netloc, timeout=self._timeout)
            try:
                connection.request(method, target, body=body or None, headers=headers)
                response = connection.getresponse()
                data = response.read()
            except (OSError, http.client.HTTPException) as failure:
                problem = f"{type(failure).__name__}"  # the kind only: a message could name the host or a path
                continue
            finally:
                connection.close()
            if 200 <= response.status < 300 or (missing_ok and response.status == 404 and _code(data) == "NoSuchKey"):
                return response.status, data
            problem = f"answered {response.status}"
            if response.status < 500 and response.status != 429:
                break  # a refusal (bad credentials, bad request) will not change on a second try
        raise StoreUnavailable(f"the file store is not available ({problem})")
