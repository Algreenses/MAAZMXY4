"""Decode the checked-in RGB PNG fixtures without Qt, Pillow or another project."""
import struct
import zlib


def read_png_bgr(path):
    import numpy as np

    raw = path.read_bytes()
    if raw[:8] != b'\x89PNG\r\n\x1a\n':
        raise ValueError('Not a PNG fixture')
    offset, compressed, header = 8, bytearray(), None
    while offset < len(raw):
        length = struct.unpack_from('>I', raw, offset)[0]
        kind = raw[offset + 4:offset + 8]
        data = raw[offset + 8:offset + 8 + length]
        if kind == b'IHDR':
            header = struct.unpack('>IIBBBBB', data)
        elif kind == b'IDAT':
            compressed.extend(data)
        offset += length + 12
    if header is None:
        raise ValueError('Missing PNG header')
    width, height, depth, color, compression, filtering, interlace = header
    if depth != 8 or color not in (2, 6) or compression or filtering or interlace:
        raise ValueError('Fixture must be non-interlaced RGB/RGBA8')
    channels = 3 if color == 2 else 4
    stride = width * channels
    decoded = zlib.decompress(compressed)
    if len(decoded) != height * (stride + 1):
        raise ValueError('Invalid PNG fixture dimensions')
    pixels, prior = bytearray(), bytearray(stride)
    for y in range(height):
        start = y * (stride + 1)
        method = decoded[start]
        row = bytearray(decoded[start + 1:start + stride + 1])
        if method:
            for x in range(stride):
                left = row[x - channels] if x >= channels else 0
                above = prior[x]
                corner = prior[x - channels] if x >= channels else 0
                if method == 1:
                    prediction = left
                elif method == 2:
                    prediction = above
                elif method == 3:
                    prediction = (left + above) // 2
                elif method == 4:
                    p = left + above - corner
                    distances = (abs(p - left), abs(p - above), abs(p - corner))
                    prediction = (left, above, corner)[distances.index(min(distances))]
                else:
                    raise ValueError('Invalid PNG filter')
                row[x] = (row[x] + prediction) & 255
        pixels.extend(row)
        prior = row
    return np.frombuffer(pixels, dtype=np.uint8).reshape(height, width, channels)[:, :, :3][:, :, ::-1].copy()
