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
        bundle = self.root / 'assets/recordings'
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
        manifest=json.loads((actual/'assets/recordings/manifest.json').read_text(encoding='utf8'))
        self.assertEqual(len(manifest['items']),8)
        for item in manifest['items']:
            path=actual/'assets/recordings'/item['file']
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(),item['sha256'])
            value=json.loads(path.read_text(encoding='utf8'))
            self.assertNotIn('source_macro',value.get('editor',{}))
            store.RecordingDocument.from_dict(value).to_dict(strict=True)

    def test_default_task_binding_is_seeded_once_and_never_replaces_user_binding(self):
        manifest=self.root/'assets/recordings/manifest.json'
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
