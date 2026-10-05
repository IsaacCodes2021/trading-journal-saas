"""Reading an image's kind and size from its own header (images.py), with real headers of each format."""

import base64
import struct

import pytest

from tradejournal import images
from tradejournal.images import MAX_PIXELS, MAX_SIDE, ImageRefused, content_type_of, inspect

# A real 1 x 1 PNG.
PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
)


def png(width: int, height: int) -> bytes:
    return (
        b"\x89PNG\r\n\x1a\n"
        + struct.pack(">I", 13)
        + b"IHDR"
        + struct.pack(">II", width, height)
        + b"\x08\x02\x00\x00\x00"
    )


def gif(width: int, height: int, version: bytes = b"GIF89a") -> bytes:
    return version + struct.pack("<HH", width, height) + b"\x00\x00\x00;"


def jpeg(width: int, height: int, *, marker: int = 0xC0, before: bytes = b"") -> bytes:
    app0 = b"\xff\xe0" + struct.pack(">H", 16) + b"JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
    frame = (
        bytes([0xFF, marker]) + struct.pack(">HBHHB", 17, 8, height, width, 3) + b"\x01\x22\x00\x02\x11\x01\x03\x11\x01"
    )
    return b"\xff\xd8" + app0 + before + frame + b"\xff\xda\x00\x08\x01\x01\x00\x00\x3f\x00" + b"\xff\xd9"


def webp_lossy(width: int, height: int, *, scale: int = 0) -> bytes:
    frame = b"\x10\x02\x00" + b"\x9d\x01\x2a" + struct.pack("<HH", width | (scale << 14), height | (scale << 14))
    body = b"WEBP" + b"VP8 " + struct.pack("<I", len(frame)) + frame
    return b"RIFF" + struct.pack("<I", len(body)) + body


def webp_lossless(width: int, height: int) -> bytes:
    bits = (width - 1) | ((height - 1) << 14)
    chunk = b"\x2f" + struct.pack("<I", bits) + b"\x00" * 4
    body = b"WEBP" + b"VP8L" + struct.pack("<I", len(chunk)) + chunk
    return b"RIFF" + struct.pack("<I", len(body)) + body


def webp_extended(width: int, height: int) -> bytes:
    chunk = b"\x10\x00\x00\x00" + (width - 1).to_bytes(3, "little") + (height - 1).to_bytes(3, "little")
    body = b"WEBP" + b"VP8X" + struct.pack("<I", len(chunk)) + chunk
    return b"RIFF" + struct.pack("<I", len(body)) + body


# ---- each format ------------------------------------------------------------------------------------------------


def test_a_real_png_is_read():
    info = inspect(PNG_1X1)
    assert (info.kind, info.content_type, info.extension, info.width, info.height) == ("png", "image/png", ".png", 1, 1)


@pytest.mark.parametrize(("width", "height"), [(1920, 1080), (3840, 2160), (1, 1), (7, 5000)])
def test_png_sizes(width, height):
    info = inspect(png(width, height))
    assert (info.width, info.height) == (width, height)


@pytest.mark.parametrize("version", [b"GIF87a", b"GIF89a"])
def test_gif_sizes(version):
    info = inspect(gif(640, 480, version))
    assert (info.kind, info.content_type, info.extension, info.width, info.height) == (
        "gif",
        "image/gif",
        ".gif",
        640,
        480,
    )


def test_gif_sizes_are_little_endian():
    assert (inspect(gif(256, 1)).width, inspect(gif(1, 256)).height) == (256, 256)


@pytest.mark.parametrize("marker", [0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF])
def test_jpeg_sizes_from_every_kind_of_frame_header(marker):
    info = inspect(jpeg(1600, 900, marker=marker))
    assert (info.kind, info.content_type, info.extension, info.width, info.height) == (
        "jpeg",
        "image/jpeg",
        ".jpg",
        1600,
        900,
    )


def test_jpeg_height_comes_before_width_in_its_header():
    info = inspect(jpeg(width=4000, height=3000))
    assert (info.width, info.height) == (4000, 3000)


def test_jpeg_segments_before_the_frame_are_skipped_whatever_they_hold():
    comment = b"\xff\xfe" + struct.pack(">H", 2 + 300) + b"c" * 300  # a long comment, with bytes that look like markers
    tricky = b"\xff\xe1" + struct.pack(">H", 2 + 6) + b"\xff\xc0\x00\x00\x01\x01"  # an APP1 that contains an SOF marker
    info = inspect(jpeg(800, 600, before=comment + tricky))
    assert (info.width, info.height) == (800, 600)


def test_jpeg_fill_bytes_between_segments_are_allowed():
    data = jpeg(300, 200, before=b"\xff\xff\xff")
    assert (inspect(data).width, inspect(data).height) == (300, 200)


def test_webp_lossy_masks_the_scaling_bits_out_of_its_size():
    info = inspect(webp_lossy(1280, 720, scale=2))
    assert (info.kind, info.content_type, info.extension, info.width, info.height) == (
        "webp",
        "image/webp",
        ".webp",
        1280,
        720,
    )


def test_webp_lossless_stores_each_side_minus_one():
    info = inspect(webp_lossless(1024, 768))
    assert (info.width, info.height) == (1024, 768)


def test_webp_extended_stores_each_side_minus_one_in_three_bytes():
    info = inspect(webp_extended(10_000, 2_000))
    assert (info.width, info.height) == (10_000, 2_000)


# ---- what is refused ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "data",
    [
        b"",
        b"hello",
        b"%PDF-1.7 ...",
        b"<svg xmlns='http://www.w3.org/2000/svg'><script>alert(1)</script></svg>",
        b"<?xml version='1.0'?><svg/>",
        b"MZ\x90\x00" + b"\x00" * 100,
        b"RIFF\x24\x00\x00\x00WAVEfmt " + b"\x00" * 50,  # RIFF, but a sound
        b"BM" + b"\x00" * 100,  # a bitmap: not one of the four
    ],
)
def test_anything_else_is_not_an_image(data):
    with pytest.raises(ImageRefused, match="not a PNG, JPEG, GIF or WebP"):
        inspect(data)


@pytest.mark.parametrize(
    "data",
    [
        b"\x89PNG\r\n\x1a\n",  # a signature and nothing else
        b"\x89PNG\r\n\x1a\n" + b"\x00" * 32,  # not a header chunk
        b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 13) + b"IDAT" + b"\x00" * 20,  # the wrong first chunk
        b"GIF89a\x00",
        b"\xff\xd8\xff",
        b"\xff\xd8\xff\xe0" + b"\x00" * 32,  # a signature and zeros: never a frame header
        b"\xff\xd8" + b"\xff\xda\x00\x08\x01\x01\x00\x00\x3f\x00",  # the scan starts with no frame before it
        b"RIFF\x24\x00\x00\x00WEBPVP8 " + b"\x00" * 32,  # a lossy chunk with no start code
        b"RIFF\x24\x00\x00\x00WEBPVP8L" + b"\x00" * 32,  # a lossless chunk with no signature byte
        b"RIFF\x24\x00\x00\x00WEBPJUNK" + b"\x00" * 32,
        b"RIFF\x24\x00\x00\x00WEBPVP8 " + b"\x00" * 4,  # too short to hold a size
    ],
)
def test_a_header_that_cannot_be_read_is_refused_in_words(data):
    with pytest.raises(ImageRefused, match="damaged"):
        inspect(data)


@pytest.mark.parametrize("data", [png(0, 10), png(10, 0), gif(0, 5), jpeg(0, 5), webp_lossy(0, 7)])
def test_an_image_with_no_pixels_is_refused(data):
    with pytest.raises(ImageRefused, match="damaged"):
        inspect(data)


def test_the_pixel_cap_is_fifty_million_and_the_side_cap_twenty_thousand():
    assert (MAX_PIXELS, MAX_SIDE) == (50_000_000, 20_000)


def test_an_image_at_the_pixel_cap_is_accepted_and_one_pixel_over_is_not():
    assert inspect(png(10_000, 5_000)).width * inspect(png(10_000, 5_000)).height == MAX_PIXELS
    with pytest.raises(ImageRefused, match="more than the 50 million"):
        inspect(png(10_000, 5_001))


def test_a_tiny_file_that_claims_a_huge_picture_is_refused_before_anyone_decodes_it():
    bomb = png(65_535, 65_535)  # 4 billion pixels in 33 bytes
    assert len(bomb) < 40
    with pytest.raises(ImageRefused, match=r"65,535 by 65,535"):
        inspect(bomb)


def test_a_very_long_thin_image_is_refused_by_its_side_even_under_the_pixel_cap():
    assert inspect(png(MAX_SIDE, 1)).width == MAX_SIDE
    assert inspect(png(1, MAX_SIDE)).height == MAX_SIDE  # a tall strip is as good as a wide one, at exactly the cap
    with pytest.raises(ImageRefused, match="a side"):
        inspect(png(MAX_SIDE + 1, 1))
    with pytest.raises(ImageRefused, match="a side"):
        inspect(png(1, MAX_SIDE + 1))


# ---- headers that look right but are not (each pins a check that a broken version of the code passed) -------------------

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def test_a_png_whose_first_chunk_is_not_ihdr_is_refused_even_when_a_size_sits_where_one_would():
    data = PNG_SIGNATURE + struct.pack(">I", 13) + b"IDAT" + struct.pack(">II", 100, 100) + b"\x08\x02\x00\x00\x00"
    with pytest.raises(ImageRefused, match="damaged"):
        inspect(data)


def test_a_png_whose_header_chunk_has_the_wrong_length_is_refused():
    data = PNG_SIGNATURE + struct.pack(">I", 12) + b"IHDR" + struct.pack(">II", 100, 100) + b"\x08\x02\x00\x00\x00"
    with pytest.raises(ImageRefused, match="damaged"):
        inspect(data)


@pytest.mark.parametrize("length", range(8, 24))
def test_a_png_cut_short_anywhere_in_its_header_is_refused_in_words_and_never_crashes(length):
    with pytest.raises(ImageRefused, match="damaged"):
        inspect(PNG_1X1[:length])
    assert inspect(PNG_1X1[:24]).width == 1  # and the shortest header that holds a size is read


@pytest.mark.parametrize("marker", [0x01, *range(0xD0, 0xDA)])
def test_jpeg_markers_that_carry_no_length_are_skipped_all_of_them(marker):
    data = jpeg(300, 200, before=bytes([0xFF, marker]))
    assert (inspect(data).width, inspect(data).height) == (300, 200)


def test_a_jpeg_frame_header_at_the_very_end_of_the_file_is_read():
    data = b"\xff\xd8\xff\xc0" + struct.pack(">HBHH", 11, 8, 50, 30)  # nothing after the width
    info = inspect(data)
    assert (info.width, info.height) == (30, 50)


def test_a_jpeg_frame_header_too_short_to_hold_a_size_is_refused():
    data = b"\xff\xd8\xff\xc0" + struct.pack(">HBHH", 7, 8, 50, 30) + b"\x00" * 4
    with pytest.raises(ImageRefused, match="damaged"):
        inspect(data)


def test_a_jpeg_scan_that_starts_before_any_frame_is_refused_even_if_a_frame_follows_it():
    scan = b"\xff\xda" + struct.pack(">H", 2)  # a scan header with nothing in it
    frame = b"\xff\xc0" + struct.pack(">HBHHB", 11, 8, 50, 30, 1) + b"\x01\x11\x00"
    with pytest.raises(ImageRefused, match="damaged"):
        inspect(b"\xff\xd8" + scan + frame)


def test_a_lossy_webp_without_the_start_code_is_refused_whatever_size_it_claims():
    frame = b"\x10\x02\x00" + b"\x00\x00\x00" + struct.pack("<HH", 100, 100)
    body = b"WEBP" + b"VP8 " + struct.pack("<I", len(frame)) + frame
    with pytest.raises(ImageRefused, match="damaged"):
        inspect(b"RIFF" + struct.pack("<I", len(body)) + body)


def test_the_refusal_says_the_size_it_found_with_separators():
    with pytest.raises(ImageRefused) as caught:
        inspect(png(12_000, 9_000))
    assert "12,000 by 9,000 pixels" in str(caught.value)
    assert "Take a smaller one" in str(caught.value)


def test_every_format_is_held_to_the_same_cap():
    for make in (png, gif, jpeg, webp_lossy):
        with pytest.raises(ImageRefused, match="more than the 50 million"):
            inspect(make(10_000, 5_001))
    with pytest.raises(ImageRefused, match="more than the 50 million"):
        inspect(webp_lossless(10_000, 5_001))
    with pytest.raises(ImageRefused, match="more than the 50 million"):
        inspect(webp_extended(10_000, 5_001))


def test_a_refusal_is_a_value_error_so_the_routes_answer_it_in_words():
    assert issubclass(ImageRefused, ValueError)


# ---- the type of a file known only by its name -----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("a.png", "image/png"),
        ("A.PNG", "image/png"),
        ("a.jpg", "image/jpeg"),
        ("a.jpeg", "image/jpeg"),
        ("a.gif", "image/gif"),
        ("a.webp", "image/webp"),
        ("a.svg", "application/octet-stream"),
        ("a", "application/octet-stream"),
        ("a.png.exe", "application/octet-stream"),
    ],
)
def test_a_stored_name_gives_its_type(name, expected):
    assert content_type_of(name) == expected


def test_nothing_in_the_table_of_types_is_svg_or_html():
    assert not any("svg" in t or "html" in t for t, _ in images._TYPES.values())


def test_the_front_end_tells_people_the_same_screenshot_limits_the_server_enforces():
    """`frontend/src/lib/limits.ts` is what the trade page and the Help page say; if the server's numbers change, this fails
    until the front end says the same."""
    import re
    from pathlib import Path

    from tradejournal.pg.screenshots import MAX_FILE_BYTES, MAX_PER_TRADE

    source = (Path(__file__).resolve().parents[2] / "frontend" / "src" / "lib" / "limits.ts").read_text(
        encoding="utf-8"
    )
    assert int(re.search(r"export const MAX_IMAGE_MB = (\d+)", source).group(1)) * 1024 * 1024 == MAX_FILE_BYTES
    assert int(re.search(r"export const MAX_IMAGES_PER_TRADE = (\d+)", source).group(1)) == MAX_PER_TRADE
