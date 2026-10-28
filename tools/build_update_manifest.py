"""Generate protocol-2 metadata from final local assets and real release URLs.

This tool writes a candidate manifest only; it never uploads or edits feeds.
"""
import argparse
import json
from pathlib import Path
import sys
from urllib.parse import quote
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from update_engine.packages import (CLIENT_RECORD, RESOURCE_RECORD, Inventory, digest_file,
                                    inspect_archive, read_json_bytes, MAX_METADATA)
from update_engine.protocol import ARCHITECTURE, PRODUCT, PROTOCOL, Manifest, Release


def release_record(path, kind, urls, notes, channel):
    path = Path(path)
    with zipfile.ZipFile(path) as archive:
        name = CLIENT_RECORD if kind == 'client' else RESOURCE_RECORD
        if archive.getinfo(name).file_size > MAX_METADATA:
            raise ValueError('包内安装清单过大。')
        inventory = Inventory.parse(read_json_bytes(archive.read(name)), kind)
        result = dict(version=inventory.client_version if kind == 'client' else inventory.resource_version,
                      notes=notes, package=dict(kind=kind, format='zip', size=path.stat().st_size,
                                               sha256=digest_file(path), urls=urls))
        if kind == 'client':
            result.update(resource_version=inventory.resource_version, min_updater_protocol=PROTOCOL)
        else:
            result.update(min_client_version=inventory.min_client_version,
                          max_client_version=inventory.max_client_version)
        release = Release.parse(result, kind, channel)
        inspect_archive(archive, release)
        import hashlib
        for filename, expected in inventory.files.items():
            with archive.open(filename) as stream:
                if hashlib.file_digest(stream, 'sha256').hexdigest() != expected['sha256']:
                    raise ValueError('包内文件校验失败：' + filename)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--client', type=Path)
    parser.add_argument('--resource', type=Path)
    parser.add_argument('--repository', required=True)
    parser.add_argument('--tag', required=True)
    parser.add_argument('--sequence', type=int, required=True)
    parser.add_argument('--channel', choices=('stable', 'preview'), default='stable')
    parser.add_argument('--notes', default='')
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    import re
    if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', args.repository):
        parser.error('仓库名称无效。')
    result = dict(schema=PROTOCOL, product=PRODUCT, architecture=ARCHITECTURE,
                  channel=args.channel, sequence=args.sequence)
    for kind, path in (('client', args.client), ('resource', args.resource)):
        if path is not None:
            url = f'https://github.com/{args.repository}/releases/download/{quote(args.tag, safe="")}/{quote(path.name)}'
            result[kind] = release_record(path, kind, [url], args.notes, args.channel)
    Manifest.parse(result, args.channel)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open('x', encoding='utf8') as output:
        json.dump(result, output, ensure_ascii=False, indent=2)
    print(args.out)
    print('仅生成本地候选清单；发布前须确认包已上传到上述真实地址并完成下载复验。')


if __name__ == '__main__':
    main()
