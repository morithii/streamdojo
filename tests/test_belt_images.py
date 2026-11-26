"""Belt character pictures: the belt -> image mapping (JS) and the image files themselves.

The mapping lives in web/js/belt-levels.js and is tested with Node's built-in test runner;
this file runs that test so CI's `pytest -q` covers it, then checks the PNGs with the standard library.
"""
import shutil
import struct
import subprocess
import zlib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
BELTS = ["white", "yellow", "orange", "green", "blue", "brown", "black"]
ORIGINALS = ROOT / "frontend" / "src" / "assets" / "belts"
APP_IMAGES = ROOT / "web" / "img" / "belts"


def png_info(path):
    """(width, height, colour type, top-left pixel alpha) read straight from the PNG bytes."""
    data = path.read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n", f"{path.name} is not a PNG"
    width, height, _depth, colour = struct.unpack(">IIBB", data[16:26])
    idat, pos = b"", 8
    while pos < len(data):                                   # collect the image data chunks
        length, kind = struct.unpack(">I4s", data[pos:pos + 8])
        if kind == b"IDAT":
            idat += data[pos + 8:pos + 8 + length]
        pos += 12 + length
    raw = zlib.decompress(idat)
    # The first pixel of the first row is stored unfiltered under every PNG filter type: byte 0 is the filter
    alpha = raw[4] if colour == 6 else None
    return width, height, colour, alpha


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js not installed")
def test_belt_mapping_js():
    result = subprocess.run(["node", "--test", str(ROOT / "tests" / "web" / "belt-levels.test.mjs")],
                            capture_output=True, text=True, cwd=ROOT)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("belt", BELTS)
def test_original_kept(belt):
    assert (ORIGINALS / f"belt-{belt}.png").is_file()


def test_app_images_same_size_transparent_and_small():
    sizes = set()
    for belt in BELTS:
        path = APP_IMAGES / f"belt-{belt}.png"
        width, height, colour, alpha = png_info(path)
        assert colour == 6, f"{belt}: must keep its alpha channel (RGBA)"
        assert alpha == 0, f"{belt}: background must stay transparent"
        assert path.stat().st_size < 60_000, f"{belt}: optimised copy should be small"
        assert path.stat().st_size < (ORIGINALS / path.name).stat().st_size / 10
        sizes.add((width, height))
    assert sizes == {(113, 192)}, "all seven app pictures must be the same size"
