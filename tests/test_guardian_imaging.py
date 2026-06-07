"""Tests for services.guardian.imaging guardian-image blur."""

from __future__ import annotations

import io

import pytest

from services.guardian import imaging


def _make_image(path, color=(200, 30, 30)):
    from PIL import Image

    img = Image.new("RGB", (64, 64), color)
    # add a hard edge so blur visibly changes pixels
    for x in range(32):
        for y in range(64):
            img.putpixel((x, y), (10, 10, 200))
    img.save(path, format="JPEG", quality=95)


def test_blur_returns_jpeg_bytes(tmp_path):
    p = tmp_path / "x.jpg"
    _make_image(p)
    out = imaging.blur_image_file(str(p), radius=10)
    assert isinstance(out, bytes) and len(out) > 0
    # decodes as a valid JPEG of the same size
    from PIL import Image

    im = Image.open(io.BytesIO(out))
    assert im.size == (64, 64)


def test_blur_changes_edge_pixels(tmp_path):
    p = tmp_path / "x.jpg"
    _make_image(p)
    from PIL import Image

    orig = Image.open(str(p)).convert("RGB")
    out = imaging.blur_image_file(str(p), radius=12)
    blurred = Image.open(io.BytesIO(out)).convert("RGB")
    # at the hard edge (x=31..32) blur should mix the two colors
    op = orig.getpixel((31, 32))
    bp = blurred.getpixel((31, 32))
    assert op != bp


def test_reveal_box_keeps_region_sharper(tmp_path):
    p = tmp_path / "x.jpg"
    _make_image(p)
    from PIL import Image

    out_full = imaging.blur_image_file(str(p), radius=12)
    out_reveal = imaging.blur_image_file(str(p), radius=12, reveal_box=(0, 0, 20, 20))
    a = Image.open(io.BytesIO(out_full)).convert("RGB").getpixel((5, 5))
    b = Image.open(io.BytesIO(out_reveal)).convert("RGB").getpixel((5, 5))
    # the revealed corner differs from the fully-blurred version
    assert a != b


def test_bad_path_raises(tmp_path):
    with pytest.raises(Exception):
        imaging.blur_image_file(str(tmp_path / "missing.jpg"))
