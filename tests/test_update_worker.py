import hashlib
import json
import os
import signal
import subprocess
import sys
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from test_update_packages import fingerprint
from test_update_protocol import feed
from update_engine.packages import Inventory, PackageError
from update_engine.processes import owned_processes
from update_engine.service import _canonical
from update_engine.storage import state_directory, write_json
from update_engine.worker import pending_package, prepare_worker, WORKER_POINTER
from update_engine.validation import validate_stage


class UpdateWorkerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'client'
        self.root.mkdir()
        self.raw = feed()
        self.config = dict(repository='example/MAAZMXY4', channel='stable',
                           urls=['https://example.invalid/feed'], identity='example/MAAZMXY4:stable')
        write_json(self.root/'update-config.json', dict(schema=1, product='MAAZMXY4',
            repository=self.config['repository'], default_channel='stable', channels={'stable': self.config['urls']}))
        contents = {'interface.json': b'{"version":"2.3.8"}', 'update_engine/worker.py': b'# trusted worker',
                    'runtimes/python/pythonw.exe': b'fixture interpreter, never executed',
                    'agent/task_asset_sync.py': b'# trusted migration'}
        for name, raw in contents.items():
            path = self.root/name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
        inventory = Inventory('client', '2.3.8', '2.3.8', {n: fingerprint(v) for n,v in contents.items()})
        write_json(self.root/'client-installation.json', inventory.to_dict())
        base = state_directory(self.root)
        self.download_id = 'a'*32
        write_json(base/'pending.json', {'id': self.download_id})
        write_json(base/'downloads'/self.download_id/'receipt.json', dict(id=self.download_id, config=self.config,
            manifest=self.raw, kind='client', repair=False))
        write_json(base/'accepted-manifests.json', {self.config['identity']:
            dict(sequence=self.raw['sequence'], sha256=hashlib.sha256(_canonical(self.raw)).hexdigest())})

    def test_pending_package_identity_and_accepted_feed_are_required(self):
        self.assertEqual(pending_package(self.root)[0].name, self.download_id)
        write_json(state_directory(self.root)/'accepted-manifests.json', {self.config['identity']:
            dict(sequence=self.raw['sequence']+1, sha256='0'*64)})
        with self.assertRaisesRegex(PackageError, '过期'):
            pending_package(self.root)

    def test_worker_copy_does_not_launch_or_change_client_files(self):
        before = (self.root/'interface.json').read_bytes()
        with patch('subprocess.Popen', side_effect=AssertionError('must not launch')):
            worker = prepare_worker(self.root)
        self.assertEqual((self.root/'interface.json').read_bytes(), before)
        self.assertEqual((worker/'agent/task_asset_sync.py').read_bytes(), b'# trusted migration')
        self.assertEqual((state_directory(self.root)/WORKER_POINTER).read_text(), worker.name)
        with self.assertRaises(PackageError):
            prepare_worker(self.root)

    def test_changed_helper_code_cannot_be_executed_from_copy(self):
        (self.root/'update_engine/worker.py').write_bytes(b'changed')
        with self.assertRaisesRegex(PackageError, '安装助手依赖已修改'):
            prepare_worker(self.root)
        self.assertFalse((state_directory(self.root)/WORKER_POINTER).exists())

    def test_process_ownership_excludes_other_trees_and_only_this_worker(self):
        worker = self.root/'temp/project-update/workers/b'
        images = [(1, str(self.root/'MFAAvalonia.exe')), (2, str(self.root/'runtimes/python/python.exe')),
                  (3, str(self.root.parent/'daily/MFAAvalonia.exe')),
                  (4, str(worker/'runtimes/python/pythonw.exe')),
                  (5, str(self.root/'temp/project-update/workers/another/runtimes/python/pythonw.exe'))]
        self.assertEqual([pid for pid,_ in owned_processes(self.root, worker=worker, images=images)], [1,2,5])

    def test_partial_client_or_resource_package_never_passes_dependency_gate(self):
        for kind in ('client','resource'):
            inventory = Inventory(kind, '2.3.9' if kind == 'client' else None, '2.3.9',
                                   {'interface.json': fingerprint(b'{}')}, '2.3.8')
            with self.subTest(kind=kind), self.assertRaisesRegex(PackageError, '缺少'):
                validate_stage(self.root, inventory)

    def test_installer_handoff_releases_frontend_response_pipe(self):
        # A long-lived installer must not inherit the short-lived JSON helper's
        # stdout pipe: the frontend waits for EOF before it can exit.
        code = '''
import json, subprocess, sys
from pathlib import Path
from update_engine import worker
original = subprocess.Popen
worker.prepare_worker = lambda *a, **k: Path(sys.argv[1])
def child(arguments, **options):
    return original([sys.executable, '-c', 'import time; time.sleep(20)'], **options)
worker.subprocess.Popen = child
print(json.dumps(worker.start_worker(Path(sys.argv[1]))), flush=True)
'''
        process = subprocess.Popen([sys.executable, '-c', code, str(self.root)],
            cwd=Path(__file__).resolve().parents[1], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding='utf8')
        child_pid = None
        try:
            # read the PID even on regression so cleanup never leaks the fixture
            line = process.stdout.readline()
            child_pid = json.loads(line)['pid']
            process.communicate(timeout=5)
            self.assertEqual(process.returncode, 0)
        finally:
            if child_pid is not None:
                try:
                    os.kill(child_pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
            if process.poll() is None:
                process.kill()
            process.communicate(timeout=5)


if __name__ == '__main__':
    unittest.main()
