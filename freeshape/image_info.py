"""The pixel size and resolution of an image a Canvas feature places (I12.1), read from the file's own header.

Only PNG and JPEG are accepted. Nothing is decoded: a PNG's size comes from its IHDR chunk and its resolution
from a pHYs chunk; a JPEG's size comes from its SOF marker and its resolution from a JFIF APP0 segment. Any
other file, or one cut short, raises ImageError with a message a person can act on. Standard library only, no
Qt, so the server and the engine worker can call it.

The default size of a canvas is the image's pixels at its own resolution, or at 96 pixels per inch when it gives
none (FreeCAD's image plane uses the same 96 dpi), so a 96 dpi image of 96 pixels is 25.4 mm wide.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass

DEFAULT_DPI = 96.0
MM_PER_INCH = 25.4

_PNG_SIG = b"\x89PNG\r\n\x1a\n"
_JPEG_SOI = b"\xff\xd8"
# JPEG start-of-frame markers: C0..CF except DHT (C4), JPG (C8) and DAC (CC)
_JPEG_SOF = {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}
_ONLY = "Only PNG and JPEG images can be used"


class ImageError(ValueError):
    """The bytes are not a whole PNG or JPEG file. The message says what was found."""


@dataclass(frozen=True)
class ImageInfo:
    format: str                  # "png" or "jpeg"
    width: int                   # pixels
    height: int                  # pixels
    dpi_x: float | None = None   # the resolution the file states, pixels per inch; None when it states none
    dpi_y: float | None = None

    def default_size_mm(self) -> tuple[float, float]:
        """(width, height) in mm: the pixels at the file's own resolution, else at 96 dpi."""
        dx = self.dpi_x if self.dpi_x else DEFAULT_DPI
        dy = self.dpi_y if self.dpi_y else DEFAULT_DPI
        return self.width * MM_PER_INCH / dx, self.height * MM_PER_INCH / dy


def read_info(data) -> ImageInfo:
    """The ImageInfo of a PNG or JPEG file's bytes. ImageError for anything else or a file cut short."""
    if not isinstance(data, (bytes, bytearray, memoryview)):
        raise ImageError(f"{_ONLY}: no image data")
    data = bytes(data)
    if data.startswith(_PNG_SIG):
        return _png(data)
    if data.startswith(_JPEG_SOI):
        return _jpeg(data)
    raise ImageError(f"{_ONLY}; this file is {_what(data)}")


def _what(data: bytes) -> str:
    """A short name for a file that is neither PNG nor JPEG, for the refusal message."""
    if not data:
        return "empty"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "a GIF image"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "a WebP image"
    if data[:2] == b"BM":
        return "a BMP image"
    if data[:4] in (b"II*\x00", b"MM\x00*"):
        return "a TIFF image"
    head = data[:512].lstrip(b"\xef\xbb\xbf \t\r\n").lower()
    if head.startswith(b"<svg") or (head.startswith(b"<?xml") and b"<svg" in data[:4096].lower()):
        return "an SVG drawing"
    return "not an image FreeShape can read"


def _png(data: bytes) -> ImageInfo:
    """Walk the chunks to IEND: IHDR gives the size, pHYs (unit metre) the resolution. A chunk that runs past
    the end, or no IEND, means the file was cut short."""
    pos, size, dpi = len(_PNG_SIG), None, (None, None)
    while True:
        if pos + 8 > len(data):
            raise ImageError("The PNG file is cut short (it ends before its last chunk)")
        length, kind = struct.unpack(">I4s", data[pos:pos + 8])
        body = data[pos + 8:pos + 8 + length]
        if pos + 12 + length > len(data):
            raise ImageError("The PNG file is cut short (it ends inside a chunk)")
        if size is None:
            if kind != b"IHDR" or length < 8:
                raise ImageError("The PNG file has no IHDR header")
            w, h = struct.unpack(">II", body[:8])
            if w == 0 or h == 0:
                raise ImageError("The PNG image has no pixels")
            size = (w, h)
        elif kind == b"pHYs" and length >= 9:
            px, py, unit = struct.unpack(">IIB", body[:9])
            if unit == 1 and px and py:             # pixels per metre
                dpi = (px * 0.0254, py * 0.0254)
        elif kind == b"IEND":
            return ImageInfo("png", size[0], size[1], *dpi)
        pos += 12 + length


def _jpeg(data: bytes) -> ImageInfo:
    """Walk the marker segments to the first SOF: it gives the size; a JFIF APP0 before it gives the
    resolution. The file must end with the EOI marker after the frame, else it was cut short."""
    pos, dpi = 2, (None, None)
    while True:
        while pos < len(data) and data[pos] == 0xFF and pos + 1 < len(data) and data[pos + 1] == 0xFF:
            pos += 1                                  # fill bytes
        if pos + 4 > len(data):
            raise ImageError("The JPEG file is cut short (it ends before its frame header)")
        if data[pos] != 0xFF:
            raise ImageError("The JPEG file is damaged (a marker is missing)")
        marker = data[pos + 1]
        if marker == 0xD9 or marker == 0xDA:
            raise ImageError("The JPEG file has no frame header")
        if 0xD0 <= marker <= 0xD7 or marker == 0x01:  # markers with no length
            pos += 2
            continue
        (length,) = struct.unpack(">H", data[pos + 2:pos + 4])
        end = pos + 2 + length
        if length < 2 or end > len(data):
            raise ImageError("The JPEG file is cut short (it ends inside a segment)")
        body = data[pos + 4:end]
        if marker == 0xE0 and body[:5] == b"JFIF\x00" and len(body) >= 12:
            units, dx, dy = struct.unpack(">BHH", body[7:12])
            if dx and dy and units in (1, 2):         # 1: per inch, 2: per cm
                f = 1.0 if units == 1 else 2.54
                dpi = (dx * f, dy * f)
        elif marker in _JPEG_SOF:
            if len(body) < 5:
                raise ImageError("The JPEG frame header is cut short")
            h, w = struct.unpack(">HH", body[1:5])
            if w == 0 or h == 0:
                raise ImageError("The JPEG image has no pixels")
            if data.rfind(b"\xff\xd9") < end:
                raise ImageError("The JPEG file is cut short (it has no end marker)")
            return ImageInfo("jpeg", w, h, *dpi)
        pos = end
