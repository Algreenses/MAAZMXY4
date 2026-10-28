import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest

from test_update_protocol import feed
from update_engine.network import Cancelled, download, fetch_manifest
from update_engine.packages import Inventory, PackageError
from update_engine.protocol import Package
from update_engine.service import check
from update_engine.storage import write_json


class Response(io.BytesIO):
    def __init__(self, raw, headers=None):
        super().__init__(raw)
        self.headers = headers or {}


class UpdateNetworkTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.raw = b'verified update'
        self.package = Package('client', len(self.raw), hashlib.sha256(self.raw).hexdigest(),
                               ('https://one.invalid/file', 'https://two.invalid/file'))

    def test_failed_mirror_does_not_publish_partial_file(self):
        calls = []
        def opener(url):
            calls.append(url)
            return Response(b'wrong' if len(calls) == 1 else self.raw)
        path = self.root / 'download.zip'
        url = download(self.package, path, opener=opener)
        self.assertEqual(url, self.package.urls[1])
        self.assertEqual(path.read_bytes(), self.raw)
        self.assertEqual(list(self.root.iterdir()), [path])

    def test_oversize_and_hash_failure_do_not_leave_packages(self):
        for data in (self.raw + b'extra', b'corrupt'):
            with self.subTest(data=data), self.assertRaises(PackageError):
                download(self.package, self.root / 'download.zip', opener=lambda _: Response(data))
            self.assertEqual(list(self.root.iterdir()), [])

    def test_download_cancellation_does_not_try_next_mirror(self):
        calls = []
        def opener(url):
            calls.append(url)
            return Response(self.raw)
        with self.assertRaises(Cancelled):
            download(self.package, self.root / 'download.zip', opener=opener,
                     cancelled=lambda: bool(calls))
        self.assertEqual(len(calls), 1)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_concurrent_destination_creation_is_not_overwritten(self):
        target = self.root / 'download.zip'
        def opener(url):
            target.write_bytes(b'another owner')
            return Response(self.raw)
        with self.assertRaises(PackageError):
            download(self.package, target, opener=opener)
        self.assertEqual(target.read_bytes(), b'another owner')

    def test_manifest_read_is_bounded(self):
        with self.assertRaises(PackageError):
            fetch_manifest('https://example.invalid/feed', opener=lambda _: Response(b'x' * (512*1024 + 1)))


class UpdateServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        write_json(self.root / 'update-config.json', dict(schema=1, product='MAAZMXY4',
                   repository='example/MAAZMXY4', default_channel='stable',
                   channels={'stable': ['https://one.invalid/feed', 'https://two.invalid/feed']}))
        write_json(self.root / 'interface.json', {'version': '2.3.8'})
        write_json(self.root / 'client-installation.json', Inventory('client', '2.3.8', '2.3.8',
                   {'interface.json': dict(size=0, sha256='0'*64)}).to_dict())

    def fetch(self, raw):
        return lambda _: json.dumps(raw).encode()

    def test_latest_valid_mirror_wins_not_first_http_success(self):
        older, newer = feed(), feed()
        older['sequence'], newer['sequence'] = 1, 2
        result = check(self.root, fetch=lambda url: self.fetch(older if 'one.' in url else newer)(url))
        self.assertEqual(result['source'], 'https://two.invalid/feed')
        self.assertEqual(result['status'], 'available')
        self.assertEqual(result['update']['kind'], 'client')

    def test_same_sequence_with_different_contents_is_a_release_error(self):
        first, second = feed(), feed()
        second['client']['notes'] = 'silently replaced'
        with self.assertRaisesRegex(PackageError, '同一发布序号'):
            check(self.root, fetch=lambda url: self.fetch(first if 'one.' in url else second)(url))

    def test_accepted_sequence_cannot_be_replaced_or_rolled_back(self):
        raw = feed()
        check(self.root, fetch=self.fetch(raw))
        raw['client']['notes'] = 'changed'
        with self.assertRaisesRegex(PackageError, '不同内容'):
            check(self.root, fetch=self.fetch(raw))
        raw['sequence'] -= 1
        result = check(self.root, fetch=self.fetch(raw))
        self.assertEqual(result['reason'], 'manifest_stale')

    def test_all_network_failures_are_errors_not_latest(self):
        def unavailable(_):
            raise OSError('offline')
        with self.assertRaisesRegex(PackageError, '检查更新失败'):
            check(self.root, fetch=unavailable)
        self.assertFalse((self.root / 'temp/project-update/checked.json').exists())

    def test_old_schema_is_not_guessed_as_a_full_client(self):
        with self.assertRaises(PackageError):
            check(self.root, fetch=self.fetch({'version': '9.0.0', 'url': 'https://example.invalid/full.exe'}))

    def test_fork_and_upstream_have_separate_accepted_sequences(self):
        first = feed()
        first['sequence'] = 999
        check(self.root, fetch=self.fetch(first))
        config = json.loads((self.root / 'update-config.json').read_text())
        config['repository'] = 'upstream/MAAZMXY4'
        write_json(self.root / 'update-config.json', config)
        self.assertEqual(check(self.root, fetch=self.fetch(feed()))['status'], 'available')

    def test_full_repair_can_be_selected_when_interface_is_damaged(self):
        (self.root/'interface.json').write_bytes(b'broken JSON')
        with self.assertRaises(PackageError):
            check(self.root, fetch=self.fetch(feed()))
        result = check(self.root, repair=True, fetch=self.fetch(feed()))
        self.assertEqual(result['update']['kind'], 'client')
        (self.root/'interface.json').write_text('{"version":"9.0.0"}')
        self.assertEqual(check(self.root, repair=True, fetch=self.fetch(feed()))['reason'],
                         'client_would_downgrade_resources')


if __name__ == '__main__':
    unittest.main()
