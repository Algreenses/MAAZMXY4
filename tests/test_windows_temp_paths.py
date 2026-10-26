"""Run path-sensitive fault injections with a real Windows short TEMP alias."""
import ctypes
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
CASES = [
    'test_script_migration.MigrationTests.test_concurrent_destination_creation_is_not_overwritten_or_removed',
    'test_script_migration.MigrationTests.test_manifest_commit_failure_preserves_old_manifest_and_old_recording',
    'test_script_migration.MigrationTests.test_second_write_failure_rolls_back_first_and_never_commits_manifest',
    'test_script_migration.MigrationTests.test_source_change_during_publication_rolls_back_created_outputs',
    'test_script_studio_service.ServiceTests.test_resolved_path_guard_rejects_external_targets_without_native_symlinks',
]


@unittest.skipUnless(sys.platform == 'win32', 'Windows 8.3 path regression')
class WindowsTempPathTests(unittest.TestCase):
    def test_fault_injection_with_short_temp_alias(self):
        with tempfile.TemporaryDirectory(prefix='maazmxy-ci-short-path-') as directory:
            get_short = ctypes.WinDLL('kernel32', use_last_error=True).GetShortPathNameW
            get_short.argtypes = (ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint32)
            get_short.restype = ctypes.c_uint32
            size = get_short(directory, None, 0)
            self.assertGreater(size, 0, f'GetShortPathNameW failed: {ctypes.get_last_error()}')
            buffer = ctypes.create_unicode_buffer(size)
            self.assertTrue(get_short(directory, buffer, size))
            alias = buffer.value
            if Path(alias) == Path(alias).resolve():
                self.skipTest('Temporary volume does not provide distinct 8.3 aliases')
            # Only this child receives TEMP/TMP; never modify system settings or
            # the parent runner's cached tempfile directory. No symlink privilege needed.
            env = dict(os.environ, TEMP=alias, TMP=alias)
            code = '''
import pathlib,sys,tempfile,unittest
sys.path.insert(0, str(pathlib.Path.cwd() / 'tests'))
with tempfile.TemporaryDirectory() as name:
    assert pathlib.Path(name) != pathlib.Path(name).resolve(), 'Short TEMP alias was not used'
suite = unittest.defaultTestLoader.loadTestsFromNames(sys.argv[1:])
raise SystemExit(not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful())
'''
            result = subprocess.run([sys.executable, '-X', 'utf8', '-c', code, *CASES],
                                    cwd=ROOT, env=env, capture_output=True, text=True, encoding='utf8', timeout=60)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == '__main__':
    unittest.main()
