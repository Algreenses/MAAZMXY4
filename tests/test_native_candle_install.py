"""Native task installation touches only owned task/resource paths."""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('native_candle_sync', ROOT / 'tools/sync_native_candle.py')
sync = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sync)


class NativeCandleInstallTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root / 'source'
        self.target = self.root / 'target'
        self.task = dict(name=sync.TASK_NAME, entry=sync.ENTRY, option=['宝烛回放来源', '宝烛自动恢复'],
                         repeatable=True, repeat_count=1, description='原生说明')
        self.options = {
            '宝烛回放来源': dict(type='select', default_case='绑定脚本', cases=[dict(name='绑定脚本'), dict(name='通用脚本')]),
            '宝烛自动恢复': dict(type='select', default_case='关闭', cases=[dict(name='关闭'), dict(name='启用')]),
        }
        self.write(self.source / 'assets/interface.json', dict(task=[deepcopy(self.task)], option=deepcopy(self.options)))
        self.write(self.source / ('assets/resource/base/pipeline/' + sync.PIPELINE_FILE), {
            sync.ENTRY: dict(next=['完成'], recognition=dict(type='TemplateMatch', param=dict(template='bounty-candle/card.png'))),
            '完成': dict(action=dict(type='DoNothing'))})
        self.bytes(self.source / 'assets/resource/base/image/bounty-candle/card.png', b'card image')
        self.before_interface = dict(
            task=[dict(name='其它任务', entry='Other', custom=12), dict(name=sync.TASK_NAME, entry=sync.OLD_ENTRY, option=['刷取次数'])],
            option={'其它选项': {'untouched': True}, '刷取次数': {'old': True}},
            resource=[dict(name='默认', path=['resource/base'])], controller=[dict(name='Win32', win32={'keyboard': 'SendMessage'})],
            custom={'keep': [1, 2]})
        self.write(self.target / 'interface.json', self.before_interface)
        self.old_item = dict(name=sync.TASK_NAME, entry=sync.OLD_ENTRY, default_check=True,
                             option=[dict(name='刷取次数', index=0, data={'刷取次数': '999'}), dict(name='回放文件', index=1)],
                             user_extra={'keep': True})
        self.write(self.target / 'config/instances/default.json', dict(
            InstanceName='用户配置', TaskItems=[dict(name='其它任务', entry='Other', default_check=True), deepcopy(self.old_item)],
            CurrentTasks=['其它任务<|||>Other', sync.TASK_NAME + '<|||>' + sync.OLD_ENTRY], other={'keep': True}))
        self.bytes(self.target / 'recordings/战斗.json', b'user recording bytes')
        self.bytes(self.target / 'resource/base/model/ocr/rec.onnx', b'user model bytes')
        self.bytes(self.target / 'appsettings.json', b'{"user":true}')

    def tearDown(self):
        self.temp.cleanup()

    @staticmethod
    def write(path, data):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False), encoding='utf8')

    @staticmethod
    def bytes(path, raw):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)

    @staticmethod
    def read(path):
        return json.loads(path.read_text(encoding='utf-8-sig'))

    def snapshot(self):
        return {path.relative_to(self.target).as_posix(): path.read_bytes() for path in self.target.rglob('*') if path.is_file()}

    def test_default_dry_run_has_zero_writes(self):
        before = self.snapshot()
        result = sync.sync_native_candle(self.source, self.target)
        self.assertFalse(result['applied'])
        self.assertTrue(result['changes'])
        self.assertEqual(self.snapshot(), before)

    def test_owned_assets_install_and_unrelated_data_is_unchanged(self):
        before = self.snapshot()
        result = sync.sync_native_candle(self.source, self.target, dry_run=False)
        self.assertTrue(result['applied'])
        self.assertTrue((self.target / result['backup'] / 'installation.json').is_file())
        interface = self.read(self.target / 'interface.json')
        self.assertEqual(interface['task'][0], self.before_interface['task'][0])
        self.assertEqual(interface['task'][1], self.task)
        for field in ('resource', 'controller', 'custom'):
            self.assertEqual(interface[field], self.before_interface[field])
        self.assertEqual(interface['option']['其它选项'], {'untouched': True})
        self.assertEqual(interface['option']['刷取次数'], {'old': True})
        for path in ('recordings/战斗.json', 'resource/base/model/ocr/rec.onnx', 'appsettings.json'):
            self.assertEqual((self.target / path).read_bytes(), before[path])

    def test_old_instance_keeps_checked_and_round_count_and_defaults_recovery_off(self):
        sync.sync_native_candle(self.source, self.target, dry_run=False)
        config = self.read(self.target / 'config/instances/default.json')
        task = config['TaskItems'][1]
        self.assertEqual(task['entry'], sync.ENTRY)
        self.assertEqual(task['repeat_count'], 999)
        self.assertTrue(task['default_check'])
        self.assertEqual(task['user_extra'], {'keep': True})
        self.assertEqual(task['option'], [dict(name='宝烛回放来源', index=0), dict(name='宝烛自动恢复', index=0)])
        self.assertEqual(config['CurrentTasks'], ['其它任务<|||>Other', sync.TASK_NAME + '<|||>' + sync.ENTRY])
        self.assertEqual(config['other'], {'keep': True})

    def test_native_choices_and_count_survive_reinstallation_and_json_reformatting(self):
        sync.sync_native_candle(self.source, self.target, dry_run=False)
        path = self.target / 'config/instances/default.json'
        config = self.read(path)
        config['TaskItems'][1]['repeat_count'] = 17
        config['TaskItems'][1]['option'][0]['index'] = 1
        config['TaskItems'][1]['option'][1]['index'] = 1
        path.write_text(json.dumps(config, ensure_ascii=True, indent=4), encoding='utf-8-sig')
        interface = self.target / 'interface.json'
        interface.write_text(json.dumps(self.read(interface), sort_keys=True, indent=4), encoding='utf-8-sig')
        before = self.snapshot()
        result = sync.sync_native_candle(self.source, self.target, dry_run=False)
        self.assertEqual(result['changes'], [])
        self.assertIsNone(result['backup'])
        self.assertEqual(self.snapshot(), before)

    def test_duplicate_task_and_history_entries_are_merged_without_reenabling_deleted_task(self):
        path = self.target / 'config/instances/default.json'
        config = self.read(path)
        native = {**self.task, 'default_check': False, 'repeat_count': 7,
                  'option': [dict(name='宝烛回放来源', index=1), dict(name='宝烛自动恢复', index=0)]}
        config['TaskItems'].append(native)
        config['CurrentTasks'] += [sync.TASK_NAME + '<|||>' + sync.ENTRY, sync.TASK_NAME + '<|||>' + sync.OLD_ENTRY]
        self.write(path, config)
        deleted = self.target / 'config/instances/deleted.json'
        self.write(deleted, dict(TaskItems=[], CurrentTasks=[sync.TASK_NAME + '<|||>' + sync.OLD_ENTRY]))
        sync.sync_native_candle(self.source, self.target, dry_run=False)
        actual = self.read(path)
        tasks = [item for item in actual['TaskItems'] if item.get('name') == sync.TASK_NAME]
        self.assertEqual(len(tasks), 1)
        self.assertFalse(tasks[0]['default_check'])
        self.assertEqual(tasks[0]['repeat_count'], 7)
        self.assertEqual(actual['CurrentTasks'].count(sync.TASK_NAME + '<|||>' + sync.ENTRY), 1)
        self.assertEqual(self.read(deleted), dict(TaskItems=[], CurrentTasks=[sync.TASK_NAME + '<|||>' + sync.ENTRY]))

    def test_write_failure_rolls_back_only_installer_bytes(self):
        before = self.snapshot()
        original = sync._publish
        calls = []
        def fail_once(path, raw, expected):
            calls.append(str(path))
            if len(calls) == 2:
                raise OSError('simulated disk failure')
            return original(path, raw, expected)
        with patch.object(sync, '_publish', side_effect=fail_once):
            with self.assertRaises(OSError):
                sync.sync_native_candle(self.source, self.target, dry_run=False)
        after = {key: value for key, value in self.snapshot().items() if not key.startswith('backup/')}
        self.assertEqual(after, before)

    def test_precommit_configuration_change_is_detected_before_any_asset_write(self):
        original = sync._verify_inputs
        path = self.target / 'interface.json'
        external = {**self.before_interface, 'user_added_during_prepare': True}
        def change(inputs):
            self.write(path, external)
            return original(inputs)
        with patch.object(sync, '_verify_inputs', side_effect=change):
            with self.assertRaises(sync.SyncConflict):
                sync.sync_native_candle(self.source, self.target, dry_run=False)
        self.assertEqual(self.read(path), external)
        self.assertFalse((self.target / 'resource/base/pipeline' / sync.PIPELINE_FILE).exists())

    def test_rollback_preserves_a_file_edited_after_publication(self):
        original = sync._publish
        calls = []
        path = self.target / 'interface.json'
        external = {**self.before_interface, 'concurrent_user_change': True}
        def fail_once(destination, raw, expected):
            calls.append(destination)
            if len(calls) == 2:
                self.write(path, external)
                raise OSError('simulated next-file failure')
            return original(destination, raw, expected)
        with patch.object(sync, '_publish', side_effect=fail_once):
            with self.assertRaises(OSError):
                sync.sync_native_candle(self.source, self.target, dry_run=False)
        self.assertEqual(self.read(path), external)

    def test_source_change_before_commit_is_rejected(self):
        original = sync._verify_inputs
        asset = self.source / 'assets/resource/base/image/bounty-candle/card.png'
        def change(inputs):
            asset.write_bytes(b'a newly edited source asset')
            return original(inputs)
        before = self.snapshot()
        with patch.object(sync, '_verify_inputs', side_effect=change):
            with self.assertRaises(sync.SyncConflict):
                sync.sync_native_candle(self.source, self.target, dry_run=False)
        self.assertEqual(self.snapshot(), before)

    def test_missing_template_or_unknown_next_node_fails_without_writes(self):
        pipeline_path = self.source / 'assets/resource/base/pipeline' / sync.PIPELINE_FILE
        source = self.read(pipeline_path)
        for node in (dict(recognition=dict(type='TemplateMatch', param=dict(template='bounty-candle/missing.png'))),
                     dict(next=['不存在节点'])):
            with self.subTest(node=node):
                self.write(pipeline_path, {sync.ENTRY: node})
                before = self.snapshot()
                with self.assertRaises(ValueError):
                    sync.sync_native_candle(self.source, self.target, dry_run=False)
                self.assertEqual(self.snapshot(), before)
        self.write(pipeline_path, source)

    def test_source_and_target_may_be_same_project(self):
        self.write(self.source / 'interface.json', self.before_interface)
        result = sync.sync_native_candle(self.source, self.source, dry_run=False)
        self.assertTrue(result['applied'])
        self.assertEqual(self.read(self.source / 'interface.json')['task'][1], self.task)
        self.assertEqual((self.source / 'resource/base/image/bounty-candle/card.png').read_bytes(), b'card image')

    def test_symlink_asset_directory_cannot_write_outside_target(self):
        outside = self.root / 'outside'
        outside.mkdir()
        link = self.target / 'resource/base/image'
        link.parent.mkdir(parents=True, exist_ok=True)
        try:
            link.symlink_to(outside, target_is_directory=True)
        except OSError:
            self.skipTest('Windows symlink privilege is not enabled')
        before = self.snapshot()
        with self.assertRaises(ValueError):
            sync.sync_native_candle(self.source, self.target, dry_run=False)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(list(outside.iterdir()), [])


if __name__ == '__main__':
    unittest.main()
