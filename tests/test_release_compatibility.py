"""Regressions found by auditing an actual release instead of a source preview."""
from pathlib import Path
import json
import shutil
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'agent'))
sys.path.insert(0, str(ROOT / 'tools'))
from build_release import collect
from script_studio_service import StudioService
from task_catalog import task_catalog, execution_plan, task_key


class ReleaseCompatibilityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def write(self, path, value):
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(value, ensure_ascii=False), encoding='utf8')

    def bundle(self):
        # Also reproduces the pre-fix source layout when running against the audit baseline.
        relative = 'agent/recordings' if (ROOT/'agent/recordings').exists() else 'assets/recordings'
        shutil.copytree(ROOT/relative, self.root/relative)
        return json.loads((self.root/relative/'manifest.json').read_text(encoding='utf8'))

    def task(self, links):
        self.write('interface.json', dict(task=[dict(name='任务', entry='Start')], resource=[dict(path=['resource/base'])]))
        self.write('resource/base/pipeline/test.json', dict(Start=dict(next=links),
            Fight=dict(action=dict(type='Custom', param=dict(custom_action='battle_play', custom_action_param=dict(file='existing.json'))))))

    def test_real_package_includes_only_format_example(self):
        names = {name for _, name in collect(str(ROOT))}
        self.assertIn('agent/recordings/manifest.json', names)
        self.assertEqual({p for p in names if p.startswith('agent/recordings/') and p.endswith('.json')},
                         {'agent/recordings/manifest.json', 'agent/recordings/示例-移动与攻击.json'})
        self.assertFalse(any(p.startswith('recordings/') or p.startswith('config/') for p in names))

    def test_corrupt_filename_collision_does_not_disable_script_service(self):
        spec = self.bundle()
        target = self.root/'recordings'/spec['items'][0]['file']
        target.parent.mkdir()
        target.write_bytes(b'{broken-user-script')
        service = StudioService(self.root)
        try:
            self.assertEqual(target.read_bytes(), b'{broken-user-script')
            rows = service.list()['items']
            self.assertTrue(any(row['file'] == target.name and row['error'] for row in rows))
            self.assertEqual(sum(row['is_default'] for row in rows), 1)
        finally:
            service.close()

    def test_nonobject_collision_does_not_disable_script_service(self):
        spec = self.bundle()
        self.write('recordings/'+spec['items'][0]['file'], [])
        service = StudioService(self.root)
        service.close()

    def test_initial_seeding_does_not_rewrite_an_existing_default_task_binding(self):
        self.bundle()
        self.write('recordings/.bindings.json', dict(version=2,tasks={'bounty-candle':dict(file='personal.json',mode='pipeline')}))
        before=(self.root/'recordings/.bindings.json').read_bytes()
        service=StudioService(self.root)
        service.close()
        self.assertEqual((self.root/'recordings/.bindings.json').read_bytes(),before)

    def test_corrupt_default_registry_does_not_hide_user_files_or_block_unrelated_tasks(self):
        self.bundle()
        self.task(['Fight'])
        self.write('recordings/.defaults.json', [])
        self.write('recordings/user.json', dict(version=1, name='用户脚本',base=[1148,720],duration_ms=0,actions=[]))
        service = StudioService(self.root)
        try:
            result = service.list()
            self.assertEqual([row['file'] for row in result['items']], ['user.json'])
            self.assertIn('defaults', result['binding_errors'])
            self.assertFalse(service.dispatch('execution_plan', dict(name='任务',entry='Start'))['handled'])
            self.assertEqual(json.loads((self.root/'recordings/.defaults.json').read_text()), [])
        finally:
            service.close()

    def test_jump_back_links_discover_playback(self):
        self.task(['[JumpBack]Fight'])
        self.assertEqual(task_catalog(self.root)[0]['playback_nodes'], ['Fight'])

    def test_object_links_discover_playback(self):
        self.task([dict(name='Fight', jump_back=True)])
        self.assertEqual(task_catalog(self.root)[0]['playback_nodes'], ['Fight'])

    def test_anchor_links_discover_declared_targets(self):
        self.task(['[Anchor]Battle'])
        self.write('resource/base/pipeline/anchor.json', dict(Setup=dict(anchor={'Battle':'Fight'})))
        self.assertEqual(task_catalog(self.root)[0]['playback_nodes'], ['Fight'])

    def test_library_lists_capabilities_from_saved_instance_variants(self):
        self.task([])
        self.write('config/instances/test.json', dict(TaskItems=[dict(name='任务',entry='Start',pipeline_override={'Start': {'next':['Fight']}})]))
        self.assertEqual(task_catalog(self.root)[0]['playback_nodes'], ['Fight'])
        self.assertFalse(execution_plan(self.root, '任务', 'Start', {})['playback_nodes'])

    def test_existing_explicit_recording_needs_no_new_binding(self):
        self.task(['Fight'])
        self.assertFalse(execution_plan(self.root, '任务', 'Start')['handled'])

    def test_legacy_string_action_parameters_keep_their_explicit_file(self):
        self.task(['Fight'])
        for parameters in ['existing.json', '{"file":"existing.json","speed":1.0}']:
            for action in [dict(action='Custom', custom_action='battle_play', custom_action_param=parameters),
                           dict(action=dict(type='Custom',param=dict(custom_action='battle_play',custom_action_param=parameters)))]:
                with self.subTest(action=action):
                    self.write('resource/base/pipeline/test.json',dict(Start=dict(next=['Fight']),Fight=action))
                    plan=execution_plan(self.root,'任务','Start')
                    self.assertEqual(plan['playback_parameters']['Fight']['file'],'existing.json')
                    self.assertFalse(plan['handled'])

    def test_execution_uses_actual_instance_override(self):
        self.task([])
        overrides = {'Start': {'next': ['Fight']}}
        self.write('recordings/.bindings.json', dict(version=2, tasks={task_key('任务','Start'):dict(file='test.json',mode='pipeline')}))
        self.write('recordings/test.json', dict(version=1,name='测试',base=[1148,720],duration_ms=200,
            actions=[dict(t=0,op='keydown',key=65),dict(t=200,op='keyup',key=65)]))
        plan = execution_plan(self.root, '任务', 'Start', overrides)
        self.assertEqual(plan['playback_nodes'], ['Fight'])
        self.assertTrue(plan['handled'])


if __name__ == '__main__':
    unittest.main()
