"""Shared update decisions: no downloads, processes, or installed-client writes."""
from copy import deepcopy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from update_engine.protocol import (Decision, Installed, Manifest, ProtocolError,
                                    Version, choose_update, https_url)


def package(kind):
    return dict(kind=kind, format='zip', size=100, sha256='a' * 64,
                urls=['https://example.invalid/' + kind + '.zip'])


def feed():
    return dict(schema=2, product='MAAZMXY4', channel='stable', sequence=10,
                architecture='win-x64',
                client=dict(version='2.3.9', resource_version='2.3.9', min_updater_protocol=2,
                            notes='完整客户端', package=package('client')),
                resource=dict(version='2.3.10', min_client_version='2.3.9',
                              package=package('resource')))


def installed(client='2.3.8', resource='2.3.8', protocol=2):
    return Installed(Version(client), Version(resource), protocol)


class VersionTests(unittest.TestCase):
    def test_semver_order_and_numeric_prerelease(self):
        ordered = ['2.3.8-alpha', '2.3.8-alpha.1', '2.3.8-alpha.beta', '2.3.8-beta',
                   '2.3.8-beta.2', '2.3.8-beta.11', '2.3.8-rc.1', '2.3.8', '2.3.9', '2.3.10']
        versions = [Version(v) for v in ordered]
        self.assertEqual(sorted(reversed(versions)), versions)
        self.assertEqual(Version('2.3.8+build1'), Version('2.3.8+build2'))

    def test_invalid_versions_are_not_coerced_to_zero(self):
        for value in (None, 3, True, '', 'latest', 'v2.3.8', '2.3', '02.3.8',
                      '2.3.8-rc.01', '2.3.8-', '2.3.8 ', '2.3.8\n'):
            with self.subTest(value=value), self.assertRaises(ProtocolError):
                Version(value)


class ManifestTests(unittest.TestCase):
    def test_valid_feed_is_copied_and_does_not_mutate_input(self):
        raw = feed()
        before = deepcopy(raw)
        parsed = Manifest.parse(raw)
        self.assertEqual(raw, before)
        raw['client']['package']['urls'].clear()
        self.assertEqual(len(parsed.client.package.urls), 1)

    def test_product_schema_channel_architecture_and_sequence(self):
        for field, value in [('schema', 1), ('schema', True), ('schema', '2'),
                             ('product', 'Other'), ('channel', 'preview'),
                             ('architecture', 'win-arm64'), ('sequence', 0),
                             ('sequence', True), ('sequence', -1)]:
            raw = feed(); raw[field] = value
            with self.subTest(field=field, value=value), self.assertRaises(ProtocolError):
                Manifest.parse(raw)

    def test_preview_channel_cannot_leak_to_stable(self):
        raw = feed(); raw['client']['version'] = '2.3.9-rc.1'
        with self.assertRaises(ProtocolError): Manifest.parse(raw)
        raw['channel'] = 'preview'
        self.assertEqual(str(Manifest.parse(raw, channel='preview').client.version), '2.3.9-rc.1')

    def test_packages_require_correct_kind_hash_size_and_https(self):
        for field, value in [('kind', 'resource'), ('format', 'exe'), ('sha256', ''),
                             ('sha256', 'z' * 64), ('size', True), ('size', 0),
                             ('size', 8 * 1024**3 + 1), ('urls', []),
                             ('urls', ['http://example.invalid/a.zip'])]:
            raw = feed(); raw['client']['package'][field] = value
            with self.subTest(field=field, value=value), self.assertRaises(ProtocolError):
                Manifest.parse(raw)

    def test_unsafe_urls_are_rejected(self):
        for value in ('file:///test.zip', 'https://user:secret@example.invalid/a',
                      'https://example.invalid/a#fragment', 'https://example.invalid/a\n',
                      'https:///a.zip', 'https://example.invalid:bad/a',
                      'https://example.invalid\\evil/a'):
            with self.subTest(value=value), self.assertRaises(ProtocolError): https_url(value)

    def test_empty_release_and_invalid_compatibility_are_errors(self):
        raw = feed(); raw.pop('client'); raw.pop('resource')
        with self.assertRaises(ProtocolError): Manifest.parse(raw)
        raw = feed(); raw['resource']['max_client_version'] = '2.3.8'
        with self.assertRaises(ProtocolError): Manifest.parse(raw)


class UpdateDecisionTests(unittest.TestCase):
    def test_new_client_has_priority_and_resource_update_is_next_check(self):
        manifest = Manifest.parse(feed())
        decision = choose_update(installed(), manifest)
        self.assertEqual((decision.status, decision.reason), ('available', 'client_update'))
        decision = choose_update(installed('2.3.9', '2.3.9'), manifest)
        self.assertEqual(decision.reason, 'resource_update')
        self.assertEqual(choose_update(installed('2.3.9', '2.3.10'), manifest).status, 'up_to_date')

    def test_new_resources_cannot_run_on_old_or_too_new_client(self):
        raw = feed(); raw.pop('client')
        self.assertEqual(choose_update(installed(), Manifest.parse(raw)).reason, 'client_upgrade_required')
        raw['resource']['max_client_version'] = '2.3.9'
        self.assertEqual(choose_update(installed('2.4.0'), Manifest.parse(raw)).reason, 'client_upgrade_required')

    def test_updater_protocol_gate_requires_manual_upgrade(self):
        raw = feed(); raw['client']['min_updater_protocol'] = 3
        self.assertEqual(choose_update(installed(), Manifest.parse(raw)).reason, 'updater_too_old')

    def test_full_update_must_not_downgrade_newer_resources(self):
        self.assertEqual(choose_update(installed(resource='2.3.10'), Manifest.parse(feed())).reason,
                         'client_would_downgrade_resources')

    def test_missing_install_record_requires_bootstrap_and_stale_feed_blocks(self):
        manifest = Manifest.parse(feed())
        self.assertEqual(choose_update(None, manifest).reason, 'bootstrap_required')
        self.assertEqual(choose_update(installed(), manifest, minimum_sequence=11).reason, 'manifest_stale')

    def test_repair_accepts_same_full_version_but_never_resource_only(self):
        raw = feed(); raw.pop('resource')
        manifest = Manifest.parse(raw)
        current = installed('2.3.9', '2.3.9')
        self.assertEqual(choose_update(current, manifest).status, 'up_to_date')
        self.assertEqual(choose_update(current, manifest, repair=True).reason, 'client_update')
        self.assertEqual(choose_update(current, manifest, recovery_required=True).reason, 'client_update')
        raw = feed(); raw.pop('client')
        self.assertEqual(choose_update(current, Manifest.parse(raw), repair=True).reason,
                         'compatible_full_package_required')
        self.assertEqual(choose_update(installed('2.3.10', '2.3.10'), manifest, repair=True).status, 'blocked')


if __name__ == '__main__':
    unittest.main()
