import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'agent'))
import recording_store as store
from script_library import bind_task_recording, read_task_bindings
from script_studio_service import StudioService


class RecordingStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.library = self.root / 'recordings'
        self.library.mkdir()
        self.value = dict(version=1, name='默认战斗', builtin_id='default-1', base=[1148,720], duration_ms=200,
                          actions=[dict(t=0,op='keydown',key=65),dict(t=200,op='keyup',key=65)])
        self.raw = json.dumps(self.value,ensure_ascii=False).encode('utf8')
        bundle = self.root / 'agent/recordings'
        bundle.mkdir(parents=True)
        (bundle / '默认战斗.json').write_bytes(self.raw)
        (bundle / 'manifest.json').write_text(json.dumps(dict(version=1, items=[dict(id='default-1',file='默认战斗.json',
            sha256=hashlib.sha256(self.raw).hexdigest())])),encoding='utf8')

    def tearDown(self): self.temp.cleanup()
    def revision(self, filename='默认战斗.json'): return hashlib.sha256((self.library / filename).read_bytes()).hexdigest()

    def test_bundled_files_are_real_editable_library_files_seeded_only_once(self):
        self.assertEqual(store.ensure_default_recordings(self.root),['默认战斗.json'])
        changed={**self.value,'name':'我的修改'}
        (self.library/'默认战斗.json').write_text(json.dumps(changed),encoding='utf8')
        self.assertEqual(store.ensure_default_recordings(self.root),[])
        self.assertEqual(json.loads((self.library/'默认战斗.json').read_text(encoding='utf8'))['name'],'我的修改')
        self.assertEqual(store.default_recordings(self.library),{'默认战斗.json':'default-1'})

    def test_existing_edited_migration_is_reused_by_source_id(self):
        changed={**self.value,'actions':[dict(t=0,op='key',key=66)],'editor':{'source_macro':{'id':'default-1'}}}
        changed.pop('builtin_id')
        original=json.dumps(changed).encode()
        (self.library/'默认战斗.json').write_bytes(original)
        store.ensure_default_recordings(self.root)
        self.assertEqual((self.library/'默认战斗.json').read_bytes(),original)
        self.assertEqual(len(list(self.library.glob('默认*.json'))),1)

    def test_unrelated_filename_collision_never_overwrites_user_data(self):
        original=json.dumps({**self.value,'builtin_id':'other','actions':[]}).encode()
        (self.library/'默认战斗.json').write_bytes(original)
        store.ensure_default_recordings(self.root)
        self.assertEqual((self.library/'默认战斗.json').read_bytes(),original)
        self.assertIn('默认战斗-默认.json',store.default_recordings(self.library))

    def test_delete_removes_bindings_but_keeps_recovery_copy_and_does_not_reseed(self):
        store.ensure_default_recordings(self.root)
        bind_task_recording(self.library,'task-one','默认战斗.json',expected_revision=self.revision())
        bind_task_recording(self.library,'task-two','默认战斗.json',expected_revision=self.revision())
        result=store.delete_recording(self.library,'默认战斗.json',self.revision())
        self.assertFalse((self.library/'默认战斗.json').exists())
        self.assertEqual(Path(result['recovery_file']).read_bytes(),self.raw)
        self.assertEqual(set(result['unbound_tasks']),{'task-one','task-two'})
        self.assertEqual(read_task_bindings(self.library),{})
        store.ensure_default_recordings(self.root)
        self.assertFalse((self.library/'默认战斗.json').exists())

    def test_default_file_deleted_externally_is_not_silently_resurrected(self):
        store.ensure_default_recordings(self.root)
        (self.library/'默认战斗.json').unlink()
        store.ensure_default_recordings(self.root)
        self.assertFalse((self.library/'默认战斗.json').exists())

    def test_duplicate_becomes_normal_unbound_user_file(self):
        store.ensure_default_recordings(self.root)
        result=store.duplicate_recording(self.library,'默认战斗.json','自定义',self.revision())
        self.assertEqual(result['file'],'自定义.json')
        value=json.loads((self.library/'自定义.json').read_text(encoding='utf8'))
        self.assertNotIn('builtin_id',value)
        self.assertEqual(value['actions'],self.value['actions'])
        self.assertNotIn('自定义.json',store.default_recordings(self.library))
        with self.assertRaises(ValueError): store.duplicate_recording(self.library,'默认战斗.json','自定义',self.revision())

    def test_changed_revision_refuses_delete(self):
        store.ensure_default_recordings(self.root)
        with self.assertRaises(ValueError): store.delete_recording(self.library,'默认战斗.json','stale')
        self.assertTrue((self.library/'默认战斗.json').exists())

    def test_rename_preserves_all_bindings_selection_and_default_identity(self):
        store.ensure_default_recordings(self.root)
        bind_task_recording(self.library, 'task-one', '默认战斗.json', expected_revision=self.revision())
        bind_task_recording(self.library, 'task-two', '默认战斗.json', expected_revision=self.revision(), mode='replace')
        (self.library/'.selected.json').write_text(json.dumps({'file':'默认战斗.json'}), encoding='utf8')
        result = store.rename_recording(self.library, '默认战斗.json', '新名称', self.revision())
        self.assertEqual(result['file'], '新名称.json')
        self.assertFalse((self.library/'默认战斗.json').exists())
        renamed = json.loads((self.library/'新名称.json').read_text(encoding='utf8'))
        self.assertEqual(renamed, {**self.value, 'name':'新名称'})
        self.assertEqual(read_task_bindings(self.library), {'task-one':'新名称.json','task-two':'新名称.json'})
        self.assertEqual(store.binding_snapshot(self.library)[0]['task-two']['mode'], 'replace')
        self.assertEqual(json.loads((self.library/'.selected.json').read_text(encoding='utf8'))['file'], '新名称.json')
        self.assertEqual(store.default_recordings(self.library), {'新名称.json':'default-1'})
        self.assertEqual(store.ensure_default_recordings(self.root), [])

    def test_rename_failure_rolls_back_earlier_index_writes_and_keeps_original(self):
        store.ensure_default_recordings(self.root)
        bind_task_recording(self.library, 'task-one', '默认战斗.json', expected_revision=self.revision())
        before = {p.name:p.read_bytes() for p in self.library.glob('*.json')}
        publish, hits = store._publish, []
        def fail(path, raw, expected, verify=None):
            if path.name == '.defaults.json':
                hits.append(path.name)
                raise OSError('injected index failure')
            return publish(path, raw, expected, verify)
        with patch.object(store, '_publish', side_effect=fail), self.assertRaises(OSError):
            store.rename_recording(self.library, '默认战斗.json', '新名称', self.revision())
        self.assertEqual(hits, ['.defaults.json'])
        self.assertEqual({p.name:p.read_bytes() for p in self.library.glob('*.json')}, before)

    def test_rename_rejects_collisions_stale_revision_and_unsafe_paths(self):
        store.ensure_default_recordings(self.root)
        (self.library/'other.json').write_bytes(self.raw)
        for name, revision in [('other',self.revision()), ('OTHER',self.revision()),
                               ('../outside',self.revision()), ('CON',self.revision()), ('新名称','stale')]:
            with self.subTest(name=name), self.assertRaises(ValueError):
                store.rename_recording(self.library,'默认战斗.json',name,revision)
        self.assertEqual((self.library/'默认战斗.json').read_bytes(),self.raw)
        self.assertEqual((self.library/'other.json').read_bytes(),self.raw)

    def test_rename_same_and_case_only_names(self):
        (self.library/'Sample.json').write_bytes(self.raw)
        store.rename_recording(self.library,'Sample.json','Sample',self.revision('Sample.json'))
        store.rename_recording(self.library,'Sample.json','sample',self.revision('Sample.json'))
        self.assertEqual([p.name for p in self.library.glob('*.json')], ['sample.json'])
        self.assertEqual(json.loads((self.library/'sample.json').read_text(encoding='utf8'))['name'],'sample')

    def test_rename_refreshes_open_document_so_next_save_uses_new_file(self):
        service = StudioService(self.root)
        try:
            service.dispatch('load',dict(file='默认战斗.json'))
            renamed = service.dispatch('rename_file',dict(file='默认战斗.json',name='新名称',revision=self.revision()))
            self.assertEqual(renamed['document']['file'],'新名称.json')
            self.assertEqual(renamed['document']['name'],'新名称')
            service.dispatch('save')
            self.assertFalse((self.library/'默认战斗.json').exists())
        finally: service.close()

    def test_rename_never_removes_concurrently_created_destination(self):
        store.ensure_default_recordings(self.root)
        publish, hits = store._publish, []
        def race(path, raw, expected, verify=None):
            if path.name == '新名称.json':
                hits.append(path.name)
                path.write_bytes(b'other writer')
            return publish(path, raw, expected, verify)
        with patch.object(store, '_publish', side_effect=race), self.assertRaises(ValueError):
            store.rename_recording(self.library,'默认战斗.json','新名称',self.revision())
        self.assertEqual(hits,['新名称.json'])
        self.assertEqual((self.library/'新名称.json').read_bytes(),b'other writer')
        self.assertEqual((self.library/'默认战斗.json').read_bytes(),self.raw)

    def test_rename_preserves_another_open_dirty_document(self):
        service=StudioService(self.root)
        try:
            service.dispatch('new')
            before=service.snapshot()
            result=service.dispatch('rename_file',dict(file='默认战斗.json',name='新名称',revision=self.revision()))
            self.assertEqual(result['document'],before)
        finally:
            service.dirty=False
            service.close()

    def test_failed_file_move_rolls_back_default_status_and_bindings(self):
        store.ensure_default_recordings(self.root)
        bind_task_recording(self.library,'task-one','默认战斗.json',expected_revision=self.revision())
        before={p.name:p.read_bytes() for p in self.library.glob('*.json')}
        original=store.os.replace
        def fail_move(source,destination):
            if Path(destination).parent.name == '.trash': raise OSError('locked file')
            return original(source,destination)
        with patch.object(store.os,'replace',side_effect=fail_move),self.assertRaises(OSError):
            store.delete_recording(self.library,'默认战斗.json',self.revision())
        self.assertEqual({p.name:p.read_bytes() for p in self.library.glob('*.json')},before)

    def test_list_and_delete_selected_file_clear_editor_without_stale_save(self):
        service=StudioService(self.root)
        try:
            listing=service.dispatch('list')['items']
            self.assertEqual(len(listing),1)
            self.assertTrue(listing[0]['is_default'])
            service.dispatch('load',dict(file='默认战斗.json'))
            result=service.dispatch('delete_file',dict(file='默认战斗.json',revision=listing[0]['revision']))
            self.assertTrue(result['document']['empty'])
            with self.assertRaises(ValueError): service.dispatch('save')
        finally: service.close()

    def test_real_bundled_recordings_all_load_without_old_project(self):
        actual=Path(__file__).resolve().parents[1]
        manifest=json.loads((actual/'agent/recordings/manifest.json').read_text(encoding='utf8'))
        self.assertEqual([item['file'] for item in manifest['items']], ['示例-移动与攻击.json'])
        self.assertEqual(manifest.get('initial_bindings'), [])
        for item in manifest['items']:
            path=actual/'agent/recordings'/item['file']
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(),item['sha256'])
            value=json.loads(path.read_text(encoding='utf8'))
            self.assertNotIn('source_macro',value.get('editor',{}))
            store.RecordingDocument.from_dict(value).to_dict(strict=True)

    def test_default_task_binding_is_seeded_once_and_never_replaces_user_binding(self):
        manifest=self.root/'agent/recordings/manifest.json'
        value=json.loads(manifest.read_text(encoding='utf8'))
        value['initial_bindings']=[dict(task_id='task-one',default_id='default-1',mode='pipeline')]
        manifest.write_text(json.dumps(value),encoding='utf8')
        store.ensure_default_recordings(self.root)
        self.assertEqual(read_task_bindings(self.library),{'task-one':'默认战斗.json'})
        _, token=store.binding_snapshot(self.library)
        store.set_file_bindings(self.library,'默认战斗.json',{},self.revision(),token)
        store.ensure_default_recordings(self.root)
        self.assertEqual(read_task_bindings(self.library),{})

    def test_row_binding_replaces_only_selected_file_associations_atomically(self):
        store.ensure_default_recordings(self.root)
        store.duplicate_recording(self.library,'默认战斗.json','其他',self.revision())
        bind_task_recording(self.library,'keep-other','其他.json',expected_revision=self.revision('其他.json'))
        _, token=store.binding_snapshot(self.library)
        store.set_file_bindings(self.library,'默认战斗.json',{'first':'pipeline','second':'replace'},self.revision(),token)
        self.assertEqual(read_task_bindings(self.library),{'keep-other':'其他.json','first':'默认战斗.json','second':'默认战斗.json'})
        _, token=store.binding_snapshot(self.library)
        store.set_file_bindings(self.library,'默认战斗.json',{'second':'pipeline'},self.revision(),token)
        self.assertEqual(read_task_bindings(self.library),{'keep-other':'其他.json','second':'默认战斗.json'})

    def test_stale_binding_dialog_cannot_overwrite_another_window(self):
        store.ensure_default_recordings(self.root)
        _, token=store.binding_snapshot(self.library)
        bind_task_recording(self.library,'external-task','默认战斗.json',expected_revision=self.revision())
        with self.assertRaisesRegex(ValueError,'其他窗口'):
            store.set_file_bindings(self.library,'默认战斗.json',{'new-task':'pipeline'},self.revision(),token)
        self.assertEqual(read_task_bindings(self.library),{'external-task':'默认战斗.json'})

    def test_invalid_recording_can_still_be_unbound_from_library_row(self):
        service = StudioService(self.root)
        try:
            bind_task_recording(self.library, 'external-task', '默认战斗.json', expected_revision=self.revision())
            (self.library/'默认战斗.json').write_bytes(b'{broken')
            _, token = store.binding_snapshot(self.library)
            service.dispatch('set_file_bindings', dict(file='默认战斗.json', revision=self.revision(),
                                                     binding_revision=token, tasks={}))
            self.assertEqual(read_task_bindings(self.library), {})
        finally:
            service.close()
