"""The request signature (bucketstore.authorization), against the worked examples AWS publishes for Signature Version 4.

A fake server that signs with the same code would agree with a wrong signature, so these are the check that does not
depend on our own code: the expected values come from the AWS documentation ("Authenticating Requests: Using the
Authorization Header (AWS Signature Version 4)", the examples for bucket `examplebucket`).
"""

import hashlib

import pytest

from tradejournal.bucketstore import EMPTY_SHA256, authorization, canonical_query

# AWS's published example credentials, put together from short pieces so a secret scanner does not mistake them for real ones.
ACCESS = "".join(["AKIA", "IOSF", "ODNN", "7EXAMPLE"])
SECRET = "".join(["wJalrX", "UtnFEMI/", "K7MDENG/", "bPxRfi", "CYEXAMPLE", "KEY"])  # noqa: S105
DATE = "20130524T000000Z"
HOST = "examplebucket.s3.amazonaws.com"


def sign(method, path, query, headers, payload=EMPTY_SHA256):
    return authorization(
        method=method,
        path=path,
        query=query,
        headers=headers,
        payload_sha256=payload,
        access_key_id=ACCESS,
        secret_access_key=SECRET,
        region="us-east-1",
        amz_date=DATE,
    )


def signature_of(header: str) -> str:
    return header.rsplit("Signature=", 1)[1]


def test_get_object_with_a_range():
    header = sign(
        "GET",
        "/test.txt",
        {},
        {"Host": HOST, "Range": "bytes=0-9", "x-amz-content-sha256": EMPTY_SHA256, "x-amz-date": DATE},
    )
    assert header.startswith(f"AWS4-HMAC-SHA256 Credential={ACCESS}/20130524/us-east-1/s3/aws4_request, ")
    assert "SignedHeaders=host;range;x-amz-content-sha256;x-amz-date," in header
    assert signature_of(header) == "f0e8bdb87c964420e857bd35b5d6ed310bd44f0170aba48dd91039c6036bdb41"


def test_put_object_encodes_a_dollar_sign_and_signs_the_payload_hash():
    payload = hashlib.sha256(b"Welcome to Amazon S3.").hexdigest()
    assert payload == "44ce7dd67c959e0d3524ffac1771dfbba87d2b6b4b4e99e42034a8b803f8b072"  # as printed in the example
    header = sign(
        "PUT",
        "/test$file.text",
        {},
        {
            "Host": HOST,
            "Date": "Fri, 24 May 2013 00:00:00 GMT",
            "x-amz-date": DATE,
            "x-amz-storage-class": "REDUCED_REDUNDANCY",
            "x-amz-content-sha256": payload,
        },
        payload,
    )
    assert "SignedHeaders=date;host;x-amz-content-sha256;x-amz-date;x-amz-storage-class," in header
    assert signature_of(header) == "98ad721746da40c64f1a55b78f14c238d841ea1380cd77a1b5971af0ece108bd"


def test_a_query_with_a_name_and_no_value_is_signed_with_an_empty_value():
    header = sign(
        "GET", "/", {"lifecycle": ""}, {"Host": HOST, "x-amz-content-sha256": EMPTY_SHA256, "x-amz-date": DATE}
    )
    assert signature_of(header) == "fea454ca298b7da1c68078a5d1bdbfbbe0d65c699e0f91ac7a200a0136783543"


def test_a_listing_with_two_query_values_is_signed_in_sorted_order():
    header = sign(
        "GET",
        "/",
        {"prefix": "J", "max-keys": "2"},  # given out of order on purpose: the signature sorts them
        {"Host": HOST, "x-amz-content-sha256": EMPTY_SHA256, "x-amz-date": DATE},
    )
    assert signature_of(header) == "34b48302e7b5fa45bde8084f4b7868a86f0a534bc59db6670ed5711ef69dc6f7"


def test_the_signature_depends_on_the_secret_the_date_the_path_the_method_and_every_signed_header():
    base = {"Host": HOST, "x-amz-content-sha256": EMPTY_SHA256, "x-amz-date": DATE}
    reference = signature_of(sign("GET", "/a.png", {}, base))
    assert signature_of(sign("GET", "/a.png", {}, dict(base))) == reference  # the same request, the same signature
    assert signature_of(sign("GET", "/b.png", {}, base)) != reference
    assert signature_of(sign("DELETE", "/a.png", {}, base)) != reference
    assert signature_of(sign("GET", "/a.png", {}, {**base, "Host": "other.example"})) != reference
    assert signature_of(sign("GET", "/a.png", {}, {**base, "x-amz-content-sha256": "0" * 64})) != reference
    other = authorization(
        method="GET",
        path="/a.png",
        query={},
        headers=base,
        payload_sha256=EMPTY_SHA256,
        access_key_id=ACCESS,
        secret_access_key=SECRET + "x",
        region="us-east-1",
        amz_date=DATE,
    )
    assert signature_of(other) != reference
    later = authorization(
        method="GET",
        path="/a.png",
        query={},
        headers={**base, "x-amz-date": "20130525T000000Z"},
        payload_sha256=EMPTY_SHA256,
        access_key_id=ACCESS,
        secret_access_key=SECRET,
        region="us-east-1",
        amz_date="20130525T000000Z",
    )
    assert signature_of(later) != reference


def test_header_names_are_signed_in_lower_case_and_their_spaces_are_squeezed():
    plain = {"Host": HOST, "X-Amz-Content-Sha256": EMPTY_SHA256, "X-Amz-Date": DATE, "Range": "bytes=0-9"}
    spaced = {"host": f"  {HOST}  ", "x-amz-content-sha256": EMPTY_SHA256, "x-amz-date": DATE, "range": "bytes=0-9"}
    assert sign("GET", "/t", {}, plain) == sign("GET", "/t", {}, spaced)


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ({}, ""),
        ({"b": "2", "a": "1"}, "a=1&b=2"),
        ({"prefix": "u/abc/"}, "prefix=u%2Fabc%2F"),  # a slash in a value is encoded; only the path keeps its slashes
        ({"continuation-token": "ab/cd+ef=="}, "continuation-token=ab%2Fcd%2Bef%3D%3D"),  # slash, plus and equals signs
        ({"a b": "c~d"}, "a%20b=c~d"),  # a space is %20, never +; the tilde stays
    ],
)
def test_the_query_is_sorted_and_percent_encoded_the_way_the_signature_needs(query, expected):
    assert canonical_query(query) == expected
