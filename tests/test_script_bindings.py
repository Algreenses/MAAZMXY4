"""Task bindings use saved library files, never task-owned recording copies."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

AGENT = Path(__file__).resolve().parents[1] / 'agent'
sys.path.insert(0, str(AGENT))
import script_library as library
from script_studio_service import StudioService


def recording(name='战斗', key=74):
    return dict(version=1, name=name, base=[1148, 720], duration_ms=200,
                actions=[dict(t=0, op='keydown', key=key), dict(t=100, op='keyup', key=key)])


class ScriptBindingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.directory = self.root / 'recordings'
        self.directory.mkdir()
        self.path = self.directory / '战斗.json'
        self.write(self.path, recording())
        self.service = StudioService(self.root)

    def tearDown(self):
        self.service.dirty = False
        self.service.close()
        self.temp.cleanup()

    @staticmethod
    def write(path, data):
        path.write_text(json.dumps(data, ensure_ascii=False), encoding='utf8')

    def load(self):
        return self.service.dispatch('load', dict(file=self.path.name))

    def bind(self):
        self.load()
        return self.service.dispatch('bind_task', dict(task_id='bounty-candle'))

    def test_bind_edit_save_keeps_one_live_file_and_no_task_snapshot(self):
        self.bind()
        bound = library.resolve_task_recording(self.directory, 'bounty-candle')
        before = bound.read_bytes()
        operation = self.service.snapshot()['operations'][0]
        self.service.dispatch('edit', dict(id=operation['id'], changes=dict(key=75)))
        self.service.dispatch('save')
        self.assertEqual(library.resolve_task_recording(self.directory, 'bounty-candle'), bound)
        self.assertNotEqual(bound.read_bytes(), before)
        self.assertEqual(json.loads(bound.read_text(encoding='utf8'))['actions'][0]['key'], 75)
        self.assertEqual(library.read_task_bindings(self.directory), {'bounty-candle': '战斗.json'})
        self.assertEqual([p.name for p in self.directory.glob('*.json') if not p.name.startswith('.')], ['战斗.json'])
        self.assertFalse((self.root / '.local-migrations').exists())

    def test_missing_binding_or_file_never_uses_latest_or_global_selection(self):
        self.write(self.directory / '.selected.json', {'file': self.path.name})
        with self.assertRaisesRegex(ValueError, '尚未绑定'):
            library.resolve_task_recording(self.directory, 'bounty-candle')
        self.bind()
        self.path.unlink()
        self.write(self.directory / 'other.json', recording('其他'))
        with self.assertRaisesRegex(FileNotFoundError, '不存在'):
            library.resolve_task_recording(self.directory, 'bounty-candle')

    def test_binding_requires_saved_clean_current_revision(self):
        self.load()
        self.service.dispatch('rename', dict(name='改名'))
        with self.assertRaisesRegex(ValueError, '未保存'):
            self.service.dispatch('bind_task', dict(task_id='bounty-candle'))
        self.service.dispatch('save')
        self.write(self.path, recording('外部变更'))
        with self.assertRaisesRegex(ValueError, '其他程序'):
            self.service.dispatch('bind_task', dict(task_id='bounty-candle'))
        self.assertFalse((self.directory / '.bindings.json').exists())

    def test_save_as_preserves_original_binding_until_explicit_rebind(self):
        self.bind()
        original = self.path.read_bytes()
        self.service.dispatch('rename', dict(name='新录制'))
        self.service.dispatch('save', dict(file='新录制.json'))
        self.assertEqual(self.path.read_bytes(), original)
        self.assertEqual(library.resolve_task_recording(self.directory, 'bounty-candle'), self.path)
        self.service.dispatch('bind_task', dict(task_id='bounty-candle'))
        self.assertEqual(library.resolve_task_recording(self.directory, 'bounty-candle').name, '新录制.json')

    def test_corrupt_index_shows_library_error_and_refuses_overwrite(self):
        self.bind()
        index = self.directory / '.bindings.json'
        index.write_bytes(b'not json')
        listing = self.service.dispatch('list')
        self.assertIn('bounty-candle', listing['binding_errors'])
        self.assertEqual(len(listing['items']), 1)
        with self.assertRaisesRegex(ValueError, '绑定'):
            self.service.dispatch('bind_task', dict(task_id='bounty-candle'))
        self.assertEqual(index.read_bytes(), b'not json')

    def test_unsafe_paths_and_unknown_tokens_are_rejected(self):
        for name in ('../outside.json', '.selected.json', 'C:/other.json', 'a/b.json'):
            with self.subTest(name=name), self.assertRaises(ValueError):
                library.library_recording_path(self.directory, name)
        for token in ('@latest', '@task:', '@task:../bad'):
            with self.subTest(token=token), self.assertRaises(ValueError):
                library.resolve_recording_reference(self.directory, token)

    def test_atomic_failure_preserves_old_binding(self):
        self.bind()
        before = (self.directory / '.bindings.json').read_bytes()
        self.write(self.directory / '第二份.json', recording('第二份'))
        self.service.dispatch('load', dict(file='第二份.json'))
        with patch.object(library.os, 'replace', side_effect=OSError('write failure')):
            with self.assertRaises(OSError):
                self.service.dispatch('bind_task', dict(task_id='bounty-candle'))
        self.assertEqual((self.directory / '.bindings.json').read_bytes(), before)

    def test_concurrent_editor_binding_is_not_lost(self):
        self.bind()
        index = self.directory / '.bindings.json'
        self.write(self.directory / '第二份.json', recording('第二份'))
        self.service.dispatch('load', dict(file='第二份.json'))
        external = dict(version=1, tasks={'bounty-candle': self.path.name, 'other-task': '第二份.json'})
        original_fsync = library.os.fsync
        def change_index(handle):
            original_fsync(handle)
            self.write(index, external)
        with patch.object(library.os, 'fsync', side_effect=change_index):
            with self.assertRaisesRegex(ValueError, '绑定已被其他程序修改'):
                self.service.dispatch('bind_task', dict(task_id='bounty-candle'))
        self.assertEqual(json.loads(index.read_text(encoding='utf8')), external)

    def test_target_changed_during_staging_does_not_rebind(self):
        self.bind()
        index = self.directory / '.bindings.json'
        original = index.read_bytes()
        second = self.directory / '第二份.json'
        self.write(second, recording('第二份'))
        self.service.dispatch('load', dict(file=second.name))
        original_fsync = library.os.fsync
        def change_recording(handle):
            original_fsync(handle)
            self.write(second, recording('外部变更', key=76))
        with patch.object(library.os, 'fsync', side_effect=change_recording):
            with self.assertRaisesRegex(ValueError, '文件已被其他程序修改'):
                self.service.dispatch('bind_task', dict(task_id='bounty-candle'))
        self.assertEqual(index.read_bytes(), original)

    def test_two_studio_instances_cannot_publish_bindings_together(self):
        self.bind()
        before = (self.directory / '.bindings.json').read_bytes()
        with library._binding_lock(self.directory):
            with self.assertRaisesRegex(ValueError, '正在更新任务绑定'):
                self.service.dispatch('bind_task', dict(task_id='bounty-candle'))
        self.assertEqual((self.directory / '.bindings.json').read_bytes(), before)

    def test_binding_empty_or_unbalanced_recording_fails_before_publication(self):
        for actions in ([], [dict(t=0, op='keydown', key=74)]):
            with self.subTest(actions=actions):
                self.write(self.path, {**recording(), 'actions': actions})
                self.load()
                with self.assertRaises(ValueError):
                    self.service.dispatch('bind_task', dict(task_id='bounty-candle'))
        self.assertFalse((self.directory / '.bindings.json').exists())

    def test_strict_selection_and_filename_resolution(self):
        with self.assertRaisesRegex(ValueError, '选择'):
            library.resolve_recording_reference(self.directory, '@selected')
        self.write(self.directory / '.selected.json', {'file': self.path.name})
        for value in ('@selected', '战斗', '战斗.json', str(self.path)):
            self.assertEqual(library.resolve_recording_reference(self.directory, value), self.path)


if __name__ == '__main__':
    unittest.main()
