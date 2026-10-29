"""The pinned-source preparation must remain repeatable with overlapping patches."""

import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
import zipfile


ROOT = Path(__file__).resolve().parents[1]
POWERSHELL = shutil.which('pwsh') or shutil.which('powershell')


@unittest.skipUnless(POWERSHELL, 'PowerShell is required for the frontend preparation script')
class FrontendPatchReapplyTests(unittest.TestCase):
    def test_overlapping_patches_are_repeatable_and_corruption_is_rejected(self):
        with tempfile.TemporaryDirectory(prefix='frontend-patches-') as temporary:
            root = Path(temporary)
            (root / 'tools').mkdir()
            (root / 'frontend/patches').mkdir(parents=True)
            (root / '.runtime-cache').mkdir()
            shutil.copy2(ROOT / 'tools/build_frontend.ps1', root / 'tools/build_frontend.ps1')
            (root / 'icon.ico').write_bytes(b'test icon')

            commit = 'fixture'
            archive = root / '.runtime-cache/mfa-source-1080d3e.zip'
            prefix = f'MFAAvalonia-{commit}/'
            with zipfile.ZipFile(archive, 'w') as bundle:
                bundle.writestr(prefix + 'MFAAvalonia.Desktop/Desktop.csproj', '<Project/>')
                bundle.writestr(prefix + 'MFAAvalonia/Assets/placeholder', '')
                bundle.writestr(prefix + 'MFAAvalonia/Example.cs', 'before original after\n')
                bundle.writestr(prefix + 'MFAAvalonia/Example2.cs', 'begin second end\n')

            patches = [
                {'description': 'first', 'path': 'MFAAvalonia/Example.cs',
                 'find': 'original', 'replace': 'original patched'},
                {'description': 'later', 'path': 'MFAAvalonia/Example.cs',
                 'find': 'before original patched after', 'replace': 'before final after'},
                {'description': 'second', 'path': 'MFAAvalonia/Example2.cs',
                 'find': 'begin second end', 'replace': 'begin second patched end'},
                {'description': 'nested', 'path': 'MFAAvalonia/Example2.cs',
                 'find': 'second patched', 'replace': 'second final'},
            ]
            patch_names = []
            for index, patch in enumerate(patches):
                relative = f'frontend/patches/{index}.json'
                (root / relative).write_text(json.dumps(patch), encoding='utf8')
                patch_names.append(relative)
            manifest = {
                'commit': commit,
                'cache_directory': '.runtime-cache/source',
                'archive_sha256': hashlib.sha256(archive.read_bytes()).hexdigest(),
                'archive_url': 'https://example.invalid/not-used.zip',
                'project': 'MFAAvalonia.Desktop/Desktop.csproj',
                'overlay_directory': 'frontend/absent-overlay',
                'application_icon': 'icon.ico',
                'patches': patch_names,
            }
            (root / 'frontend/upstream.json').write_text(json.dumps(manifest), encoding='utf8')

            def prepare():
                return subprocess.run(
                    [POWERSHELL, '-NoProfile', '-File', str(root / 'tools/build_frontend.ps1'), '-PrepareOnly'],
                    cwd=root, capture_output=True, text=True, encoding='utf8', errors='replace', timeout=60,
                )

            for _ in range(2):
                result = prepare()
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertEqual((root / '.runtime-cache/source/MFAAvalonia/Example.cs').read_text(),
                                 'before final after\n')
                self.assertEqual((root / '.runtime-cache/source/MFAAvalonia/Example2.cs').read_text(),
                                 'begin second final end\n')

            (root / '.runtime-cache/source/MFAAvalonia/Example.cs').write_text('before corrupted after\n')
            result = prepare()
            self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn('Patch anchor is absent or ambiguous', result.stdout + result.stderr)


if __name__ == '__main__':
    unittest.main()
