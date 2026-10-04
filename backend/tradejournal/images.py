"""What an uploaded image says about itself, read from its own header (doc 03 section 6, doc 04 T3).

A screenshot is accepted only when its bytes start like a PNG, a JPEG, a GIF or a WebP **and** its header gives a size
that is sane: a file a few hundred bytes long can claim to be 100,000 pixels on a side, and whoever opens it (the browser,
later a thumbnailer) is the one who pays for decoding it. So the largest picture is capped by pixels, not only by bytes.
Nothing is decoded here and no library is used: each format keeps its width and height in a fixed place near the start.

This is the *first* check, not the whole design: the design also re-encodes every image (which strips what a photo
carries and rejects a file that is two formats at once), which needs an imaging library. Until that is added, the file is
stored as it came and served with its own type, `nosniff` and a sandboxing policy (`api/routers/screenshots.py`), so a
file that is two things at once is still only ever shown as the image it starts as.

SVG is never accepted: it can carry script.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

MAX_PIXELS = 50_000_000  # a few times an 8K screenshot (33 million): beyond it a picture is not a screenshot
MAX_SIDE = 20_000  # no side longer than this, so a one-pixel-high strip cannot sit under the pixel cap

SIGNATURE_BYTES = 12  # enough to tell what kind of file it is


@dataclass(frozen=True)
class ImageInfo:
    kind: str  # png, jpeg, gif or webp
    content_type: str
    extension: str  # with the dot
    width: int
    height: int


class ImageRefused(ValueError):
    """The message is a sentence for the person who uploaded the file."""


_TYPES = {
    "png": ("image/png", ".png"),
    "jpeg": ("image/jpeg", ".jpg"),
    "gif": ("image/gif", ".gif"),
    "webp": ("image/webp", ".webp"),
}

NOT_AN_IMAGE = "That is not a PNG, JPEG, GIF or WebP image."
NOT_READABLE = "That image's header is damaged, so its size cannot be read."


def _info(kind: str, width: int, height: int) -> ImageInfo:
    if width < 1 or height < 1:
        raise ImageRefused(NOT_READABLE)
    if width > MAX_SIDE or height > MAX_SIDE or width * height > MAX_PIXELS:
        raise ImageRefused(
            f"That image is {width:,} by {height:,} pixels, which is more than the {MAX_PIXELS // 1_000_000} million "
            f"pixels (or {MAX_SIDE:,} on a side) a screenshot may have. Take a smaller one."
        )
    content_type, extension = _TYPES[kind]
    return ImageInfo(kind, content_type, extension, width, height)


def _png(data: bytes) -> ImageInfo:
    # Signature, then the first chunk, which must be IHDR (13 bytes): width and height, big-endian, are its first eight.
    if len(data) < 24 or data[12:16] != b"IHDR" or data[8:12] != b"\x00\x00\x00\x0d":
        raise ImageRefused(NOT_READABLE)
    width, height = struct.unpack(">II", data[16:24])
    return _info("png", width, height)


def _gif(data: bytes) -> ImageInfo:
    if len(data) < 10:
        raise ImageRefused(NOT_READABLE)
    width, height = struct.unpack("<HH", data[6:10])  # the logical screen: little-endian
    return _info("gif", width, height)


_JPEG_FRAMES = {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}  # start-of-frame markers
_JPEG_NO_LENGTH = {0x01, *range(0xD0, 0xDA)}  # markers with nothing after them: restarts, SOI, EOI, TEM


def _jpeg(data: bytes) -> ImageInfo:
    position = 2  # after the start-of-image marker
    while position + 4 <= len(data):
        if data[position] != 0xFF:
            raise ImageRefused(NOT_READABLE)
        marker = data[position + 1]
        if marker == 0xFF:  # a fill byte before a marker
            position += 1
            continue
        if marker in _JPEG_NO_LENGTH:
            position += 2
            continue
        (length,) = struct.unpack(">H", data[position + 2 : position + 4])
        if marker in _JPEG_FRAMES:
            if position + 9 > len(data) or length < 8:
                raise ImageRefused(NOT_READABLE)
            height, width = struct.unpack(">HH", data[position + 5 : position + 9])
            return _info("jpeg", width, height)
        if marker == 0xDA:  # the scan begins and no frame header came before it
            raise ImageRefused(NOT_READABLE)
        position += 2 + length
    raise ImageRefused(NOT_READABLE)


def _webp(data: bytes) -> ImageInfo:
    # RIFF, a size, WEBP, then the first chunk: "VP8 " (lossy), "VP8L" (lossless) or "VP8X" (extended, with the canvas).
    chunk = data[12:16]
    if chunk == b"VP8 " and len(data) >= 30:
        if data[23:26] != b"\x9d\x01\x2a":
            raise ImageRefused(NOT_READABLE)
        width, height = struct.unpack("<HH", data[26:30])
        return _info("webp", width & 0x3FFF, height & 0x3FFF)
    if chunk == b"VP8L" and len(data) >= 25:
        if data[20] != 0x2F:
            raise ImageRefused(NOT_READABLE)
        (bits,) = struct.unpack("<I", data[21:25])
        return _info("webp", (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1)
    if chunk == b"VP8X" and len(data) >= 30:
        width = int.from_bytes(data[24:27], "little") + 1
        height = int.from_bytes(data[27:30], "little") + 1
        return _info("webp", width, height)
    raise ImageRefused(NOT_READABLE)


def inspect(data: bytes) -> ImageInfo:
    """The kind, content type and size of an image, from its header. Raises `ImageRefused` with a sentence otherwise."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return _png(data)
    if data.startswith((b"GIF87a", b"GIF89a")):
        return _gif(data)
    if data.startswith(b"\xff\xd8\xff"):
        return _jpeg(data)
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return _webp(data)
    raise ImageRefused(NOT_AN_IMAGE)


def content_type_of(name: str) -> str:
    """The type to serve a file under when only its stored name is known (a file kept from before sizes were recorded)."""
    lowered = name.lower()
    for content_type, extension in _TYPES.values():
        if lowered.endswith(extension):
            return content_type
    if lowered.endswith(".jpeg"):
        return "image/jpeg"
    return "application/octet-stream"
