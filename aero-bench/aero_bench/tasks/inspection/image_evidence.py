from __future__ import annotations

import struct
import zlib


_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_MAX_CAMERA_PIXELS = 16_777_216


def _rgb_png_scanlines(raw: bytes) -> tuple[int, int, bytes]:
    """Validate the bounded RGB8 PNG stream emitted by the Gazebo provider."""

    if not raw.startswith(_PNG_SIGNATURE):
        raise ValueError("camera frame data is not a PNG")
    cursor = len(_PNG_SIGNATURE)
    chunks: list[tuple[bytes, bytes]] = []
    while cursor < len(raw):
        if len(raw) - cursor < 12:
            raise ValueError("camera PNG has a truncated chunk")
        length = struct.unpack(">I", raw[cursor : cursor + 4])[0]
        kind = raw[cursor + 4 : cursor + 8]
        cursor += 8
        if length > len(raw) - cursor - 4:
            raise ValueError("camera PNG has a truncated chunk payload")
        payload = raw[cursor : cursor + length]
        expected_crc = struct.unpack(">I", raw[cursor + length : cursor + length + 4])[
            0
        ]
        if zlib.crc32(kind + payload) & 0xFFFFFFFF != expected_crc:
            raise ValueError("camera PNG chunk CRC is invalid")
        chunks.append((kind, payload))
        cursor += length + 4
        if kind == b"IEND":
            if cursor != len(raw):
                raise ValueError("camera PNG has trailing bytes after IEND")
            break
    if not chunks or chunks[0][0] != b"IHDR" or chunks[-1][0] != b"IEND":
        raise ValueError("camera PNG is missing its canonical boundary chunks")
    if any(kind not in {b"IHDR", b"IDAT", b"IEND"} for kind, _ in chunks):
        raise ValueError("camera PNG contains an undeclared ancillary chunk")
    if sum(kind == b"IHDR" for kind, _ in chunks) != 1:
        raise ValueError("camera PNG must contain exactly one IHDR")
    if sum(kind == b"IEND" for kind, _ in chunks) != 1 or chunks[-1][1]:
        raise ValueError("camera PNG has an invalid IEND")
    ihdr = chunks[0][1]
    if len(ihdr) != 13:
        raise ValueError("camera PNG has an invalid IHDR")
    width, height, bit_depth, color_type, compression, filtering, interlace = (
        struct.unpack(">IIBBBBB", ihdr)
    )
    if (
        width <= 0
        or height <= 0
        or width * height > _MAX_CAMERA_PIXELS
        or bit_depth != 8
        or color_type != 2
        or compression != 0
        or filtering != 0
        or interlace != 0
    ):
        raise ValueError("camera PNG is not bounded non-interlaced RGB8")
    idat_indices = [index for index, (kind, _) in enumerate(chunks) if kind == b"IDAT"]
    if not idat_indices or idat_indices != list(
        range(idat_indices[0], idat_indices[-1] + 1)
    ):
        raise ValueError("camera PNG IDAT chunks are missing or non-contiguous")
    compressed = b"".join(payload for kind, payload in chunks if kind == b"IDAT")
    expected_decoded_size = height * (1 + width * 3)
    decompressor = zlib.decompressobj()
    try:
        decoded = decompressor.decompress(compressed, expected_decoded_size + 1)
    except zlib.error as error:
        raise ValueError("camera PNG pixel stream cannot be decoded") from error
    if (
        len(decoded) != expected_decoded_size
        or not decompressor.eof
        or decompressor.unused_data
        or decompressor.unconsumed_tail
        or decompressor.flush()
    ):
        raise ValueError("camera PNG decoded pixel size is invalid")
    stride = 1 + width * 3
    if any(decoded[offset] > 4 for offset in range(0, len(decoded), stride)):
        raise ValueError("camera PNG uses an invalid scanline filter")
    return width, height, decoded


def decode_strict_rgb_png(raw: bytes) -> tuple[int, int]:
    """Validate a Gazebo RGB8 PNG and return its dimensions."""

    width, height, _scanlines = _rgb_png_scanlines(raw)
    return width, height


def decode_rgb_png_pixels(raw: bytes) -> tuple[int, int, bytes]:
    """Return actual RGB samples after reversing all five PNG row filters."""

    width, height, scanlines = _rgb_png_scanlines(raw)
    stride = width * 3
    pixels = bytearray(height * stride)
    for row in range(height):
        source = row * (stride + 1)
        destination = row * stride
        kind = scanlines[source]
        for column in range(stride):
            left = pixels[destination + column - 3] if column >= 3 else 0
            above = pixels[destination + column - stride] if row else 0
            upper_left = (
                pixels[destination + column - stride - 3] if row and column >= 3 else 0
            )
            if kind == 0:
                prediction = 0
            elif kind == 1:
                prediction = left
            elif kind == 2:
                prediction = above
            elif kind == 3:
                prediction = (left + above) // 2
            else:
                estimate = left + above - upper_left
                differences = (
                    abs(estimate - left),
                    abs(estimate - above),
                    abs(estimate - upper_left),
                )
                prediction = (left, above, upper_left)[
                    differences.index(min(differences))
                ]
            pixels[destination + column] = (
                scanlines[source + column + 1] + prediction
            ) & 255
    return width, height, bytes(pixels)


__all__ = ["decode_strict_rgb_png", "decode_rgb_png_pixels"]
