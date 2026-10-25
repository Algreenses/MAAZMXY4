"""Offline boundary tests for the runtime bootstrap (standard library only).

Run from the repository root with ``python -m unittest discover -s tools
-p test_runtime_setup.py -v``. All fixtures stay inside TemporaryDirectory;
no downloads, package installation, or Windows APIs are used.
"""

from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import socket
import stat
import sys
import tarfile
import tempfile
import unittest
from unittest import mock
import zipfile


MODULE_PATH = Path(__file__).with_name("setup_runtime.py")
SPEC = importlib.util.spec_from_file_location("_runtime_setup_under_test", MODULE_PATH)
if SPEC is None or SPEC.loader is None:
    raise ImportError(f"Cannot load {MODULE_PATH}")
setup_runtime = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = setup_runtime
SPEC.loader.exec_module(setup_runtime)


class RuntimeSetupTestCase(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="maazmxy4-setup-test-")
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        # Fail immediately if a regression unexpectedly attempts network I/O.
        self.network = self.enterContext(
            mock.patch.object(
                socket.socket,
                "connect",
                side_effect=AssertionError("Tests must not access the network"),
            )
        )


class CachedDownloadTests(RuntimeSetupTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.cache = self.base / "cache"
        self.cache.mkdir()
        self.payload = b"verified runtime archive\n"
        self.artifact = {
            "filename": "runtime.zip",
            "url": "https://example.invalid/runtime.zip",
            "sha256": hashlib.sha256(self.payload).hexdigest(),
        }
        self.cached = self.cache / self.artifact["filename"]

    def test_valid_cache_hit_does_not_use_network(self) -> None:
        self.cached.write_bytes(self.payload)

        result = setup_runtime.cached_download(self.artifact, self.cache, offline=False)

        self.assertEqual(Path(result), self.cached)
        self.assertEqual(self.cached.read_bytes(), self.payload)
        self.network.assert_not_called()

    def test_valid_cache_hit_also_works_offline(self) -> None:
        self.cached.write_bytes(self.payload)

        result = setup_runtime.cached_download(self.artifact, self.cache, offline=True)

        self.assertEqual(Path(result), self.cached)
        self.network.assert_not_called()

    def test_corrupt_cached_archive_is_rejected(self) -> None:
        self.cached.write_bytes(b"corrupt or incomplete archive")

        with self.assertRaises(ValueError):
            setup_runtime.cached_download(self.artifact, self.cache, offline=False)

        self.network.assert_not_called()

    def test_offline_missing_archive_is_rejected_without_download(self) -> None:
        with self.assertRaises(FileNotFoundError):
            setup_runtime.cached_download(self.artifact, self.cache, offline=True)

        self.assertFalse(self.cached.exists())
        self.network.assert_not_called()


class ExtractArchiveTests(RuntimeSetupTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.destination = self.base / "destination"
        self.outside = self.base / "outside.txt"

    def write_zip(self, name: str, payload: bytes = b"payload") -> Path:
        archive = self.base / "runtime.zip"
        with zipfile.ZipFile(archive, "w") as output:
            output.writestr(name, payload)
        return archive

    def write_tar(self, name: str, payload: bytes = b"payload") -> Path:
        archive = self.base / "runtime.tar.gz"
        with tarfile.open(archive, "w:gz") as output:
            info = tarfile.TarInfo(name)
            info.size = len(payload)
            output.addfile(info, io.BytesIO(payload))
        return archive

    def assert_rejected(self, archive: Path) -> None:
        # A preexisting sentinel also detects unintended overwrites.
        self.outside.write_bytes(b"keep this sentinel")
        with self.assertRaises(ValueError):
            setup_runtime.extract_archive(archive, self.destination)
        self.assertEqual(self.outside.read_bytes(), b"keep this sentinel")

    def test_zip_extracts_regular_nested_file(self) -> None:
        archive = self.write_zip("nested/runtime.dll", b"zip runtime")

        setup_runtime.extract_archive(archive, self.destination)

        self.assertEqual(
            (self.destination / "nested" / "runtime.dll").read_bytes(), b"zip runtime"
        )

    def test_tar_extracts_regular_nested_file(self) -> None:
        archive = self.write_tar("nested/runtime.dll", b"tar runtime")

        setup_runtime.extract_archive(archive, self.destination)

        self.assertEqual(
            (self.destination / "nested" / "runtime.dll").read_bytes(), b"tar runtime"
        )

    def test_zip_rejects_parent_directory_traversal(self) -> None:
        self.assert_rejected(self.write_zip("../outside.txt"))

    def test_tar_rejects_parent_directory_traversal(self) -> None:
        self.assert_rejected(self.write_tar("../outside.txt"))

    def test_zip_rejects_absolute_destination(self) -> None:
        self.assert_rejected(self.write_zip(str(self.outside.resolve())))

    def test_tar_rejects_absolute_destination(self) -> None:
        self.assert_rejected(self.write_tar(str(self.outside.resolve())))

    def test_zip_rejects_symbolic_link(self) -> None:
        archive = self.base / "symlink.zip"
        with zipfile.ZipFile(archive, "w") as output:
            info = zipfile.ZipInfo("link")
            info.create_system = 3
            info.external_attr = (stat.S_IFLNK | 0o777) << 16
            output.writestr(info, "../outside.txt")

        self.assert_rejected(archive)

        self.assertFalse(os.path.lexists(self.destination / "link"))

    def test_tar_rejects_symbolic_and_hard_links(self) -> None:
        for kind in (tarfile.SYMTYPE, tarfile.LNKTYPE):
            with self.subTest(kind=kind):
                archive = self.base / "link.tar.gz"
                with tarfile.open(archive, "w:gz") as output:
                    info = tarfile.TarInfo("link")
                    info.type = kind
                    info.linkname = "../outside.txt"
                    output.addfile(info)

                self.assert_rejected(archive)

                self.assertFalse(os.path.lexists(self.destination / "link"))


class SyncResourcesTests(RuntimeSetupTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.source = self.base / "repository"
        self.destination = self.base / "runtime"
        self.backup = self.base / "backup"
        self.enterContext(mock.patch.object(setup_runtime, "ROOT", self.source))
        self.canonical_interface = json.dumps(
            {"option": {"replay": {"cases": [{"name": "canonical"}]}}},
            sort_keys=True,
        ).encode()
        self.custom_interface = json.dumps(
            {"option": {"replay": {"cases": [{"name": "my-recording"}]}}},
            sort_keys=True,
        ).encode()
        self.pipeline = Path("resource/base/pipeline/example.json")
        self.write(self.source / "assets/interface.json", self.canonical_interface)
        self.write(self.source / "assets" / self.pipeline, b'{"canonical": true}\n')
        self.write(self.source / "assets/resource/base/image/template.png", b"template")

    @staticmethod
    def write(path: Path, content: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)

    def test_empty_destination_receives_canonical_resources(self) -> None:
        setup_runtime.sync_resources(self.destination)

        self.assertEqual(
            (self.destination / "interface.json").read_bytes(), self.canonical_interface
        )
        self.assertEqual(
            (self.destination / self.pipeline).read_bytes(), b'{"canonical": true}\n'
        )
        self.assertEqual(
            (self.destination / "resource/base/image/template.png").read_bytes(),
            b"template",
        )

    def test_default_sync_preserves_custom_interface_and_recordings(self) -> None:
        self.write(self.destination / "interface.json", self.custom_interface)
        recording = self.destination / "recordings/my-recording.json"
        self.write(recording, b'{"events": ["my actions"]}\n')

        setup_runtime.sync_resources(self.destination)

        self.assertEqual(
            (self.destination / "interface.json").read_bytes(), self.custom_interface
        )
        self.assertEqual(recording.read_bytes(), b'{"events": ["my actions"]}\n')

    def test_overwrite_backs_up_then_replaces_existing_resources(self) -> None:
        self.write(self.destination / "interface.json", self.custom_interface)
        self.write(self.destination / self.pipeline, b'{"local": true}\n')
        recording = self.destination / "recordings/my-recording.json"
        self.write(recording, b"recording to preserve")

        setup_runtime.sync_resources(
            self.destination, overwrite=True, backup=self.backup
        )

        self.assertEqual(
            (self.backup / "interface.json").read_bytes(), self.custom_interface
        )
        self.assertEqual(
            (self.backup / self.pipeline).read_bytes(), b'{"local": true}\n'
        )
        self.assertEqual(
            (self.destination / "interface.json").read_bytes(), self.canonical_interface
        )
        self.assertEqual(
            (self.destination / self.pipeline).read_bytes(), b'{"canonical": true}\n'
        )
        self.assertEqual(recording.read_bytes(), b"recording to preserve")


if __name__ == "__main__":
    unittest.main()
