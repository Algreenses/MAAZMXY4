"""Offline failure-injection tests for runtime deployment and recovery."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import socket
import sys
import tempfile
import unittest
from unittest import mock


SPEC = importlib.util.spec_from_file_location(
    "_runtime_deploy_under_test", Path(__file__).with_name("setup_runtime.py")
)
assert SPEC is not None and SPEC.loader is not None
setup = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = setup
SPEC.loader.exec_module(setup)


class RuntimeDeploymentTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="maazmxy4-deploy-test-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / "project"
        self.cache = self.root / ".runtime-cache"
        self.cache.mkdir(parents=True)
        self.stage = self.cache / "stage/project"
        self.backup = self.cache / "backups/test"
        self.state = self.root / ".runtime-state.json"
        for name, value in (("ROOT", self.root), ("CACHE", self.cache), ("STATE", self.state)):
            self.enterContext(mock.patch.object(setup, name, value))
        self.enterContext(mock.patch.object(
            socket.socket, "connect", side_effect=AssertionError("No network in deployment tests")
        ))
        self.seed_client(self.root, b"old")
        self.seed_client(self.stage, b"new")

    @staticmethod
    def write(path: Path, data: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def seed_client(self, root: Path, data: bytes) -> None:
        for name in ("libs/client.dll", "plugins/plugin.dll", "runtimes/win-x64/native/MaaFramework.dll"):
            self.write(root / name, data)
        self.write(root / "MFAAvalonia.exe", data)

    def assert_old_client(self) -> None:
        for name in ("libs/client.dll", "plugins/plugin.dll", "runtimes/win-x64/native/MaaFramework.dll", "MFAAvalonia.exe"):
            self.assertEqual((self.root / name).read_bytes(), b"old")

    def fail_rename(self, source: Path):
        original = Path.rename

        def rename(path: Path, destination: Path):
            if path.resolve() == source.resolve():
                raise PermissionError("simulated file lock")
            return original(path, destination)

        return mock.patch.object(Path, "rename", rename)

    def test_component_move_failure_restores_previous_directory(self) -> None:
        with self.fail_rename(self.stage / "libs"):
            with self.assertRaises(PermissionError):
                setup.replace_directory(self.stage / "libs", self.root / "libs", self.backup)
        self.assert_old_client()
        self.assertEqual((self.stage / "libs/client.dll").read_bytes(), b"new")

    def test_middle_deploy_failure_restores_prior_components(self) -> None:
        with self.fail_rename(self.stage / "plugins"):
            with self.assertRaises(PermissionError):
                setup.deploy(self.stage, self.backup)
        self.assert_old_client()
        self.assertEqual((self.backup / "_failed-new/libs/client.dll").read_bytes(), b"new")

    def test_top_level_client_move_failure_also_restores_runtime(self) -> None:
        with self.fail_rename(self.stage / "MFAAvalonia.exe"):
            with self.assertRaises(PermissionError):
                setup.deploy(self.stage, self.backup)
        self.assert_old_client()

    def test_rejects_move_outside_project_before_modifying_original(self) -> None:
        outside = self.root.parent / "outside"
        self.write(outside / "sentinel", b"keep")
        with self.assertRaises(ValueError):
            setup.replace_directory(self.stage / "libs", outside, self.backup)
        self.assertEqual((outside / "sentinel").read_bytes(), b"keep")
        self.assert_old_client()

    def prepare_mock_build(self, final_check_error: bool = False) -> None:
        for directory in ("agent", "tools"):
            (self.root / directory).mkdir(exist_ok=True)
        self.write(self.root / "assets/interface.json", b"new-interface")
        self.write(self.root / "interface.json", b"old-interface")
        self.write(self.root / "assets/resource/base/pipeline/existing.json", b"new-resource")
        self.write(self.root / "assets/resource/base/pipeline/added.json", b"new-resource")
        self.write(self.root / "resource/base/pipeline/existing.json", b"old-resource")
        self.write(self.state, b"old-state")

        def extract(archive: Path, destination: Path) -> None:
            if destination.name == "project":
                self.seed_client(destination, b"new")
                self.write(destination / "MaaAgentBinary/new-only.dll", b"new-only")
            else:
                self.write(destination / "python/python.exe", b"new-python")

        def check(root=None) -> None:
            if root is None:
                self.assertEqual((self.root / "MFAAvalonia.exe").read_bytes(), b"new")
                if final_check_error:
                    raise ValueError("simulated final IPC failure")

        self.enterContext(mock.patch.object(setup, "cached_download", return_value=self.cache / "fake.zip"))
        self.enterContext(mock.patch.object(setup, "extract_archive", side_effect=extract))
        self.enterContext(mock.patch.object(setup, "install_packages"))
        self.enterContext(mock.patch.object(setup, "check", side_effect=check))
        self.enterContext(mock.patch.object(setup, "lock_fingerprint", return_value="locked"))

    def assert_build_restored(self) -> None:
        self.assert_old_client()
        self.assertEqual((self.root / "interface.json").read_bytes(), b"old-interface")
        self.assertEqual((self.root / "resource/base/pipeline/existing.json").read_bytes(), b"old-resource")
        self.assertFalse((self.root / "resource/base/pipeline/added.json").exists())
        self.assertFalse((self.root / "MaaAgentBinary").exists())
        self.assertFalse((self.root / "runtimes/python").exists())
        self.assertEqual(self.state.read_bytes(), b"old-state")
        self.assertTrue(list((self.cache / "backups").glob("*/_failed-new/MFAAvalonia.exe")))

    def test_final_check_failure_restores_components_and_resources(self) -> None:
        self.prepare_mock_build(final_check_error=True)
        with self.assertRaisesRegex(ValueError, "final IPC failure"):
            setup.build({"python": {}, "client": {}}, offline=True, sync=True)
        self.assert_build_restored()

    def test_partial_state_write_failure_restores_previous_state(self) -> None:
        self.prepare_mock_build()
        original = Path.write_text

        def write_text(path: Path, *args, **kwargs):
            if path == self.state:
                path.write_bytes(b"partial-state")
                raise OSError("simulated disk failure")
            return original(path, *args, **kwargs)

        with mock.patch.object(Path, "write_text", write_text):
            with self.assertRaisesRegex(OSError, "disk failure"):
                setup.build({"python": {"version": "3.12.13"}, "client": {"version": "2.16.2"}}, offline=True, sync=True)
        self.assert_build_restored()


if __name__ == "__main__":
    unittest.main()
