import hashlib
import json
from pathlib import Path
import stat
import tempfile
import unittest
import zipfile

from update_engine.packages import (Inventory, PackageError, CLIENT_RECORD, RESOURCE_RECORD,
                                    extract_verified, managed_name, read_json_bytes, build_inventory, write_inventory_files)
from update_engine.protocol import Package, Release, Version


def fingerprint(raw):
    return dict(size=len(raw), sha256=hashlib.sha256(raw).hexdigest())


def package_fixture(root, *, kind='resource', contents=None, metadata=None, extras=(), version='2.3.9'):
    contents = contents or {'interface.json': b'{"version":"2.3.9"}', 'agent/main.py': b'pass\n',
                            'resource/base/pipeline/tasks.json': b'{}'}
    inventory = Inventory(kind, version if kind == 'client' else None, version,
                          {k: fingerprint(v) for k, v in contents.items()},
                          '2.3.8' if kind == 'resource' else None)
    path = Path(root) / 'package.zip'
    with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as archive:
        for name, raw in contents.items():
            archive.writestr(name, raw)
        archive.writestr(inventory.record_name, json.dumps(metadata or inventory.to_dict()).encode())
        for entry, raw in extras:
            archive.writestr(entry, raw)
    release = Release(Version(version), Package(kind, path.stat().st_size,
                      hashlib.sha256(path.read_bytes()).hexdigest(), ('https://example.invalid/package.zip',)), '',
                      resource_version=Version(version) if kind == 'client' else None,
                      min_client_version=Version('2.3.8') if kind == 'resource' else None)
    return path, release, inventory


class UpdatePackageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.destination = self.root / 'new-install'

    def test_complete_extraction_checks_every_file(self):
        path, release, inventory = package_fixture(self.root)
        actual = extract_verified(path, self.destination, release)
        self.assertEqual(actual, inventory)
        self.assertEqual((self.destination / 'agent/main.py').read_bytes(), b'pass\n')
        self.assertEqual(Inventory.parse(read_json_bytes((self.destination / RESOURCE_RECORD).read_bytes()),
                                        'resource'), inventory)

    def test_existing_destination_is_never_removed(self):
        path, release, _ = package_fixture(self.root)
        self.destination.mkdir()
        sentinel = self.destination / 'user-data'
        sentinel.write_text('keep')
        with self.assertRaises(PackageError):
            extract_verified(path, self.destination, release)
        self.assertEqual(sentinel.read_text(), 'keep')

    def test_wrong_package_hash_creates_no_directory(self):
        path, release, _ = package_fixture(self.root)
        path.write_bytes(path.read_bytes() + b'changed')
        with self.assertRaises(PackageError):
            extract_verified(path, self.destination, release)
        self.assertFalse(self.destination.exists())

    def test_each_file_hash_checked_even_when_archive_hash_is_valid(self):
        _, _, inventory = package_fixture(self.root)
        raw = inventory.to_dict()
        raw['files']['agent/main.py']['sha256'] = '0' * 64
        path, release, _ = package_fixture(self.root, metadata=raw)
        with self.assertRaisesRegex(PackageError, '摘要'):
            extract_verified(path, self.destination, release)
        self.assertFalse(self.destination.exists())

    def test_unsafe_paths_and_user_data_never_extract(self):
        for name in ('../escape', '/absolute', 'C:/escape', 'agent/../escape', 'agent\\escape',
                     'agent/CON.txt', 'agent/CON .txt', 'agent/COM¹.txt', 'agent/x.', 'agent/x ', 'agent/x:stream', 'agent/AGENTS.md',
                     'recordings/private.json', 'config/a.json', 'appsettings.json', 'agent/FOO~1/a'):
            with self.subTest(name=name), self.assertRaises(PackageError):
                managed_name(name, 'client')
        self.assertFalse(self.destination.exists())

    def test_files_outside_resource_protocol_rejected(self):
        for name in ('MFAAvalonia.exe', 'updater.py', 'update_engine/protocol.py'):
            with self.subTest(name=name), self.assertRaises(PackageError):
                managed_name(name, 'resource')

    def test_extra_files_and_case_conflicts_rejected_before_extract(self):
        for name in ('agent/EXTRA.py', 'agent/MAIN.py', '../escape'):
            path, release, _ = package_fixture(self.root, extras=[(name, b'x')])
            with self.subTest(name=name), self.assertRaises(PackageError):
                extract_verified(path, self.destination, release)
            self.assertFalse(self.destination.exists())

    def test_symlink_entry_rejected(self):
        entry = zipfile.ZipInfo('agent/link')
        entry.create_system = 3
        entry.external_attr = (stat.S_IFLNK | 0o777) << 16
        path, release, _ = package_fixture(self.root, extras=[(entry, b'../../other')])
        with self.assertRaises(PackageError):
            extract_verified(path, self.destination, release)
        self.assertFalse(self.destination.exists())

    def test_resource_cannot_create_client_plugin_directory(self):
        path, release, _ = package_fixture(self.root, extras=[('runtimes/win-x64/native/plugins/', b'')])
        with self.assertRaises(PackageError):
            extract_verified(path, self.destination, release)

    def test_full_client_optional_plugin_directory(self):
        path, release, _ = package_fixture(self.root, kind='client',
                                           extras=[('runtimes/win-x64/native/plugins/', b'')])
        extract_verified(path, self.destination, release)
        self.assertTrue((self.destination / 'runtimes/win-x64/native/plugins').is_dir())
        self.assertTrue((self.destination / CLIENT_RECORD).is_file())

    def test_version_and_compatibility_are_checked_against_manifest(self):
        _, _, inventory = package_fixture(self.root)
        for key in ('resource_version', 'min_client_version'):
            raw = inventory.to_dict()
            raw[key] = '2.2.0'
            path, release, _ = package_fixture(self.root, metadata=raw)
            with self.subTest(key=key), self.assertRaises(PackageError):
                extract_verified(path, self.destination, release)

    def test_json_duplicate_keys_and_nonfinite_values_rejected(self):
        for raw in (b'{"a":1,"a":2}', b'{"a":NaN}', b'{"a":Infinity}'):
            with self.subTest(raw=raw), self.assertRaises(PackageError):
                read_json_bytes(raw)

    def test_source_change_during_build_is_not_published_as_a_valid_package(self):
        source = self.root/'source.py'
        source.write_bytes(b'original')
        files = [(source, 'agent/main.py')]
        inventory = build_inventory(files, kind='resource', resource_version='2.3.9', min_client_version='2.3.8')
        source.write_bytes(b'changed!')
        with zipfile.ZipFile(self.root/'invalid.zip', 'x') as archive:
            with self.assertRaisesRegex(PackageError, '发生变化'):
                write_inventory_files(archive, files, inventory)


if __name__ == '__main__':
    unittest.main()
