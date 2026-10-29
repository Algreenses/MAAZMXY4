"""Export pinned SWFs with FFDec, without opening or executing Flash content.

JPEXS handles SWF decoding, image/sprite rendering, symbols and ActionScript.
Raw exports must stay outside the release source tree. Game assets referenced
externally are not implicitly downloaded by this tool.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[2]


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input-dir', type=Path, required=True)
    parser.add_argument('--ffdec-jar', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--scripts', action='store_true')
    args = parser.parse_args()
    output = args.output.resolve()
    if output.is_relative_to(ROOT):
        parser.error('Raw SWF exports must be kept outside the release source tree')
    sources = json.loads(Path(__file__).with_name('swf-sources.json').read_text(encoding='utf8'))
    for source in sources['sources']:
        if digest(args.input_dir / source['file']) != source['sha256']:
            raise ValueError('SWF source hash mismatch: ' + source['file'])
    output.mkdir(parents=True, exist_ok=False)
    receipt = dict(version=1, ffdec_jar_sha256=digest(args.ffdec_jar), sources=sources, files=[])
    for source in sources['sources']:
        types = 'image,shape,sprite,button,symbolClass,binaryData'
        if args.scripts:
            types += ',script'
        with (output / (source['id'] + '.log')).open('w', encoding='utf8') as log:
            subprocess.run(['java', '-Djava.awt.headless=true', '-Xmx2g', '-jar', str(args.ffdec_jar.resolve()),
                '-onerror', 'abort', '-exportTimeout', '1800', '-format', 'image:png,shape:svg',
                '-export', types, str(output/source['id']), str((args.input_dir/source['file']).resolve())],
                stdout=log, stderr=subprocess.STDOUT, timeout=1900, check=True)
        for file in sorted((output/source['id']).rglob('*')):
            if file.is_file():
                receipt['files'].append(dict(path=file.relative_to(output).as_posix(),bytes=file.stat().st_size,sha256=digest(file)))
    (output/'export-manifest.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    print(f"Exported {len(receipt['files'])} files to {output}")
