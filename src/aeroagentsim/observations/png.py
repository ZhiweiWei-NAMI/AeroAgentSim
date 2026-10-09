"""Bounded PNG integrity/decode gate without an optional image dependency."""

from __future__ import annotations

import struct
import zlib

SIGNATURE = b"\x89PNG\r\n\x1a\n"
MAX_BYTES = 64 * 1024 * 1024


def validate_png(
    data: bytes, *, expected: tuple[int, int] | None = None
) -> tuple[int, int]:
    """Validate CRCs, complete deflate stream, pixel extent and filter bytes.

    The provider contract supports non-interlaced 8-bit PNG, including Chromium
    RGB/RGBA output. Other encodings require an explicitly supplied decoder.
    """
    if not data.startswith(SIGNATURE) or len(data) > MAX_BYTES:
        raise ValueError("empty, invalid or oversized PNG")
    offset = 8
    compressed = bytearray()
    size: tuple[int, int] | None = None
    channels = 0
    ended = False
    seen_idat = False
    palette = False
    while offset < len(data):
        if offset + 12 > len(data):
            raise ValueError("truncated PNG chunk")
        length, kind = struct.unpack(">I4s", data[offset : offset + 8])
        end = offset + 12 + length
        if end > len(data):
            raise ValueError("truncated PNG payload")
        payload = data[offset + 8 : end - 4]
        crc = struct.unpack(">I", data[end - 4 : end])[0]
        if zlib.crc32(kind + payload) & 0xFFFFFFFF != crc:
            raise ValueError("PNG chunk CRC mismatch")
        if size is None and kind != b"IHDR":
            raise ValueError("PNG must start with IHDR")
        if kind == b"IHDR":
            if size is not None or length != 13:
                raise ValueError("invalid PNG IHDR")
            width, height, bits, color, method, filt, interlace = struct.unpack(
                ">IIBBBBB", payload
            )
            if not 0 < width <= 8192 or not 0 < height <= 8192:
                raise ValueError("PNG dimensions out of range")
            if (
                bits != 8
                or color not in {0, 2, 3, 4, 6}
                or (method, filt, interlace) != (0, 0, 0)
            ):
                raise ValueError("PNG contract requires non-interlaced 8-bit encoding")
            channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}[color]
            palette = color == 3
            size = width, height
        elif kind == b"PLTE":
            if not payload or len(payload) % 3 or len(payload) > 768 or seen_idat:
                raise ValueError("invalid PNG palette")
            palette = False
        elif kind == b"IDAT":
            if palette:
                raise ValueError("PNG palette missing")
            compressed.extend(payload)
            seen_idat = True
        elif kind == b"IEND":
            if length or not seen_idat or end != len(data):
                raise ValueError("invalid PNG end/trailing data")
            ended = True
            break
        elif kind[0] & 32 == 0:
            raise ValueError("unsupported PNG critical chunk")
        offset = end
    if not ended or size is None:
        raise ValueError("incomplete PNG")
    if expected is not None and size != expected:
        raise ValueError("PNG dimensions do not match request")
    row = 1 + size[0] * channels
    count = row * size[1]
    decoder = zlib.decompressobj()
    try:
        raw = decoder.decompress(compressed, count + 1)
    except zlib.error as exc:
        raise ValueError("corrupt PNG deflate stream") from exc
    if (
        len(raw) != count
        or not decoder.eof
        or decoder.unused_data
        or decoder.unconsumed_tail
    ):
        raise ValueError("PNG decoded pixel count mismatch")
    if any(raw[index] > 4 for index in range(0, count, row)):
        raise ValueError("invalid PNG scanline filter")
    return size
