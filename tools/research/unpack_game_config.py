"""Read the game's mod/mo2 FZ container without running Flash or game scripts.

Layout documented by AssetLoader.handFz/parseFz in the historical SWF. Uses
stdlib raw DEFLATE; preserves AMF opaquely and rejects unknown formats/paths.
"""
import argparse
import hashlib
import json
from pathlib import Path
import struct
import zlib


def unpack(data):
    if data[:3] not in (b'mod', b'mo2') or len(data) < 5:
        raise ValueError('Expected a mod/FZ container')
    count = struct.unpack_from('>H', data, 3)[0]
    offset = 5 + 4 * count
    if not 0 < count <= 4096 or offset > len(data):
        raise ValueError('Invalid FZ table')
    lengths = struct.unpack_from('>' + 'I' * count, data, 5)
    if sum(lengths) + offset != len(data):
        raise ValueError('FZ size mismatch')
    files = {}
    for length in lengths:
        decoder = zlib.decompressobj(-15)
        block = decoder.decompress(data[offset:offset + length], 32_000_000)
        offset += length
        if not decoder.eof or decoder.unused_data or len(block) < 4:
            raise ValueError('Invalid/oversized FZ block')
        header, size = struct.unpack_from('>HH', block)
        if size + 2 != header or 2 + header > len(block):
            raise ValueError('Invalid FZ filename')
        name = block[4:4 + size].decode('utf8')
        if not name or Path(name).name != name or any(c in name for c in '/\\:') or name in files:
            raise ValueError('Unsafe or duplicate FZ filename')
        payload = block[2 + header:]
        if data[:3] == b'mo2':
            if len(payload) < 2:
                raise ValueError('Missing FZ2 encoding')
            mode = struct.unpack_from('>H', payload)[0]
            payload = payload[2:]
            if mode == 1:
                # Preserve AMF as opaque data. No custom AMF decoder or game
                # object instantiation is needed to read the XML tables.
                name += '.amf'
            elif mode != 0:
                raise ValueError('Unsupported FZ2 encoding')
        files[name] = payload
    return files


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    files = unpack(args.source.read_bytes())
    args.output.mkdir(parents=True, exist_ok=False)
    for name, content in files.items():
        (args.output / name).write_bytes(content)
    (args.output / 'manifest.json').write_text(json.dumps(dict(
        source_sha256=hashlib.sha256(args.source.read_bytes()).hexdigest(),
        files=[dict(name=n, bytes=len(v), sha256=hashlib.sha256(v).hexdigest()) for n,v in files.items()]
    ), ensure_ascii=False, indent=2), encoding='utf8')
    print(f'Extracted {len(files)} configuration files')
