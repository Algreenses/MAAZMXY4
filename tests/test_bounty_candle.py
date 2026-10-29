"""Native task preflight and outcomes; no native library or game inputs."""
from copy import deepcopy
from contextlib import redirect_stdout
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
AGENT = ROOT / 'agent'
sys.path.insert(0, str(AGENT))


def load_runtime():
    modules = {name: ModuleType(name) for name in ('maa', 'maa.agent', 'maa.agent.agent_server', 'maa.custom_action')}
    class Register:
        @staticmethod
        def custom_action(_name): return lambda implementation: implementation
    modules['maa.agent.agent_server'].AgentServer = Register
    modules['maa.custom_action'].CustomAction = object
    spec = importlib.util.spec_from_file_location('isolated_bounty_candle', AGENT/'script_task.py')
    runtime = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, modules):
        spec.loader.exec_module(runtime)
    return runtime


runtime = load_runtime()


class Frame:
    ndim = 3
    shape = (720, 1148, 3)


class Controller:
    def __init__(self):
        self.calls = 0
        self.frame = Frame()
        self.succeeded = True
    def post_screencap(self):
        self.calls += 1
        return SimpleNamespace(wait=lambda: SimpleNamespace(succeeded=self.succeeded, get=lambda: self.frame))


class Context:
    def __init__(self):
        self.tasker = SimpleNamespace(stopping=False, controller=Controller())
        self.patches = []
        self.reject = False
        self.nodes = {}
        for path in (ROOT/'assets/resource/base/pipeline').glob('*.json'):
            self.nodes.update(json.loads(path.read_text(encoding='utf8')))
    def override_pipeline(self, value):
        self.patches.append(deepcopy(value))
        if not self.reject:
            def merge(target, patch):
                for key, item in patch.items():
                    if isinstance(item, dict) and isinstance(target.get(key), dict):
                        merge(target[key], item)
                    else:
                        target[key] = deepcopy(item)
            merge(self.nodes, value)
        return not self.reject
    def get_node_data(self, name):
        return deepcopy(self.nodes.get(name))
    def override_next(self, name, targets):
        return self.override_pipeline({name: {'next': targets}})
    def run_recognition(self, name, frame):
        if name not in self.nodes:
            return None
        # No desktop controller: the fixture remains on a positively known map.
        return SimpleNamespace(hit=name in ('Nav_TaskMap', 'Nav_Probe_hub_scene'))


PREFIX = '悬赏（守卫宝烛）-'
PIPELINE = json.loads((AGENT.parent/'assets/resource/base/pipeline/悬赏（守卫宝烛）.json').read_text(encoding='utf8'))

class BountyCandleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.recordings = self.root/'recordings'
        self.recordings.mkdir()
        self.status = self.root/'debug/task-runs'
        self.patch = patch.multiple(runtime, ROOT=self.root, RECORDINGS=self.recordings, STATUS_DIRECTORY=self.status)
        self.patch.start()
        runtime._rounds.clear()
        self.context = Context()
        self.ident = str(uuid4())
        self.argv = SimpleNamespace(task_detail=SimpleNamespace(task_id=100))
        self.save_recording('one.json', 65)
        self.bind('one.json')

    def tearDown(self):
        runtime._rounds.clear()
        self.patch.stop()
        self.temp.cleanup()

    def bind(self, filename):
        (self.recordings/'.bindings.json').write_text(json.dumps({'version':1, 'tasks':{'bounty-candle':filename}}), encoding='utf8')

    def save_recording(self, filename, key, **fields):
        data = dict(version=1, name='测试录制', base=[1148,720], duration_ms=400,
                    actions=[dict(t=50,op='keydown',key=key),dict(t=250,op='keyup',key=key)], **fields)
        (self.recordings/filename).write_text(json.dumps(data,ensure_ascii=False), encoding='utf8')
        return data

    def run_phase(self, phase, **parameters):
        defaults = PIPELINE[PREFIX + ('恢复登录' if phase == 'recovery_begin' else '初始化')]['action']['param']['custom_action_param']
        self.argv.custom_action_param = dict(defaults, **dict(phase=phase, run_id=self.ident, instance_id='default', **parameters))
        self.argv.node_name = PREFIX + '失败返回确认'
        with redirect_stdout(io.StringIO()):
            return runtime.ScriptTaskState().run(self.context, self.argv)

    def outcome(self):
        return json.loads((self.status/(self.ident+'.json')).read_text(encoding='utf8'))

    def test_state_action_accepts_an_unrelated_task_route_and_playback_node(self):
        self.assertTrue(self.run_phase('begin',file='one.json',playback_node='OtherPlayback',
                        route_node='OtherRoute',exit_mode='finish',routes={'finish':['OtherExit']},
                        navigation=None,result_guard=None))
        patch = self.context.patches[0]
        self.assertEqual(set(patch),{'OtherPlayback','OtherRoute'})
        self.assertEqual(patch['OtherRoute']['next'],['OtherExit'])
        self.assertEqual(Path(patch['OtherPlayback']['action']['param']['custom_action_param']['file']).name,'one.json')

    def test_resume_slots_are_declared_by_the_task_instead_of_fixed_to_four(self):
        self.assertTrue(self.run_phase('recovery_begin',save_slot=7,resume_choice_node='ChooseSlot',
                        resume_guards=['Guard'],resume_slots={'7':'CustomSlot'}))
        self.assertEqual(self.context.patches[0],{'ChooseSlot':{'next':['Guard','CustomSlot']}})

    def test_begin_pins_current_saved_file_and_hash_in_the_native_pipeline(self):
        self.assertTrue(self.run_phase('begin'))
        playback = self.context.patches[0][PREFIX+'回放战斗']['action']['param']['custom_action_param']
        self.assertEqual(Path(playback['file']), self.recordings/'one.json')
        self.assertEqual(playback['expected_sha256'], hashlib.sha256((self.recordings/'one.json').read_bytes()).hexdigest())
        self.assertTrue(playback['strict_task'])
        self.assertEqual(playback['expected_base'], [1148,720])
        self.assertEqual(self.context.patches[0][PREFIX+'回放后分流']['next'],
                         [PREFIX+'阻断提示', PREFIX+'拒绝还魂丹', PREFIX+'等待拒绝复活', PREFIX+'战斗失败', PREFIX+'成功返回地图', PREFIX+'等待返回'])
        self.assertEqual(self.outcome()['status'], 'running')
        self.assertEqual(self.outcome()['recording_sha256'],playback['expected_sha256'])

    def test_next_round_reads_saved_edit_without_any_recording_copy(self):
        self.assertTrue(self.run_phase('begin'))
        original = runtime._rounds[100]['recording']['sha256']
        self.assertTrue(self.run_phase('success'))
        self.save_recording('one.json', 66)
        self.ident = str(uuid4())
        self.context = Context()  # Maa overrides belong to one top-level task.
        self.assertTrue(self.run_phase('begin'))
        self.assertNotEqual(runtime._rounds[100]['recording']['sha256'], original)
        self.assertEqual(sorted(path.name for path in self.recordings.iterdir()), ['.bindings.json', 'one.json'])

    def test_battle_only_mode_uses_native_exit_and_full_mode_does_not(self):
        self.assertTrue(self.run_phase('begin',exit_mode='battle_only'))
        self.assertEqual(self.context.patches[0][PREFIX+'回放后分流']['next'],
                         [PREFIX+'阻断提示', PREFIX+'拒绝还魂丹', PREFIX+'等待拒绝复活', PREFIX+'战斗失败', PREFIX+'成功返回地图', PREFIX+'点击设置'])

    def test_missing_binding_is_fatal_without_latest_fallback_or_capture(self):
        (self.recordings/'.bindings.json').unlink()
        self.assertFalse(self.run_phase('begin'))
        self.assertEqual(self.outcome()['status'],'fatal')
        self.assertEqual(self.context.tasker.controller.calls,0)
        self.assertFalse(runtime._rounds)

    def test_managed_defeat_requests_restart_without_success_or_native_retry(self):
        self.assertTrue(self.run_phase('begin',game_management={'restart_on_failure':True}))
        self.assertFalse(self.run_phase('battle_failure'))
        self.assertEqual((self.outcome()['status'],self.outcome()['reason']),('recoverable','battle_failed'))
        self.assertFalse(runtime._rounds)

    def test_missing_and_changed_files_never_trigger_recovery(self):
        self.assertTrue(self.run_phase('begin'))
        self.save_recording('one.json',67)
        with patch.object(runtime,'confirm_white_screen') as white:
            self.assertFalse(self.run_phase('failure'))
        white.assert_not_called()
        self.assertEqual(self.outcome()['reason'],'recording_changed')

    def test_different_frame_geometry_and_unbalanced_recording_are_fatal(self):
        self.context.tasker.controller.frame=SimpleNamespace(ndim=3,shape=(737,1175,3))
        self.assertFalse(self.run_phase('begin'))
        self.assertEqual(self.outcome()['status'],'fatal')
        self.context.tasker.controller.frame=Frame()
        data=self.save_recording('one.json',65)
        data['actions'].pop()
        (self.recordings/'one.json').write_text(json.dumps(data),encoding='utf8')
        self.ident=str(uuid4())
        self.assertFalse(self.run_phase('begin'))
        self.assertFalse(self.context.patches)

    def test_cancellation_is_never_recoverable(self):
        self.context.tasker.stopping=True
        self.assertFalse(self.run_phase('begin'))
        self.assertEqual(self.outcome()['status'],'cancelled')
        self.assertEqual(self.context.tasker.controller.calls,0)

    def test_unknown_screen_stops_and_only_verified_white_fault_can_recover(self):
        for is_white,expected in ((False,'fatal'),(True,'recoverable')):
            with self.subTest(is_white=is_white):
                self.ident=str(uuid4())
                self.assertTrue(self.run_phase('begin'))
                with patch.object(runtime,'confirm_white_screen',return_value=is_white):
                    self.assertFalse(self.run_phase('failure'))
                self.assertEqual(self.outcome()['status'],expected)
                self.assertFalse(runtime._rounds)

    def test_white_confirmation_uses_three_fresh_frames_and_checks_cancellation(self):
        clock=[0.0]
        def wait(duration): clock[0]+=duration
        with patch.object(runtime.time,'monotonic',side_effect=lambda:clock[0]), patch.object(runtime,'white_frame',return_value=True):
            self.assertTrue(runtime.confirm_white_screen(self.context,wait=wait))
        self.assertEqual(self.context.tasker.controller.calls,3)
        self.assertGreaterEqual(clock[0],.4)
        self.context.tasker.stopping=True
        with self.assertRaises(runtime.Cancelled):
            runtime.confirm_white_screen(self.context,wait=wait)

    def test_recovery_selects_only_requested_existing_slot_and_reports_ready(self):
        self.assertTrue(self.run_phase('recovery_begin',save_slot=4))
        self.assertEqual(self.context.patches[0][PREFIX+'恢复选择存档']['next'],[PREFIX+'阻断提示',PREFIX+'恢复存档4'])
        self.assertTrue(self.run_phase('ready'))
        self.assertEqual(self.outcome()['status'],'ready')
        self.assertFalse(runtime._rounds)

    def test_recovery_refuses_invalid_slots(self):
        for slot in (0,5,True,'1'):
            with self.subTest(slot=slot):
                self.ident=str(uuid4())
                self.assertFalse(self.run_phase('recovery_begin',save_slot=slot))
                self.assertEqual(self.outcome()['status'],'fatal')
        self.assertFalse(self.context.patches)

    def test_status_path_must_match_uuid_in_native_debug_directory(self):
        for parameters in ({'run_id':'not-a-uuid'}, {'run_id':self.ident,'status_path':str(self.root/'escape.json')}):
            with self.subTest(parameters=parameters), self.assertRaises(ValueError):
                runtime.status_path(parameters)
        self.assertFalse(self.status.exists())

    def test_rejected_override_cannot_report_success(self):
        self.context.reject=True
        self.assertFalse(self.run_phase('begin'))
        self.assertEqual(self.outcome()['status'],'fatal')
        self.assertFalse(runtime._rounds)

    def test_unwritable_status_never_counts_success_or_escapes_callback(self):
        with patch.object(runtime, 'write_status', side_effect=OSError('disk unavailable')):
            self.assertFalse(self.run_phase('begin'))
        self.assertFalse(runtime._rounds)

    def test_capture_failure_has_specific_reason_without_claiming_window_loss(self):
        self.context.tasker.controller.succeeded=False
        self.assertFalse(self.run_phase('begin'))
        self.assertEqual(self.outcome()['reason'],'capture_unavailable')
        self.assertEqual(self.outcome()['status'],'fatal')

    def test_automatic_retry_preserves_first_recording_until_next_mfa_round(self):
        self.assertTrue(self.run_phase('begin'))
        first=self.outcome()
        self.assertTrue(self.run_phase('success'))
        self.assertEqual(self.outcome()['recording_sha256'],first['recording_sha256'])
        self.save_recording('one.json',67)
        self.context = Context()
        self.ident=str(uuid4())
        self.context.patches.clear()
        self.context.tasker.controller.calls=0
        self.assertFalse(self.run_phase('begin',expected_recording_file=first['recording_file'],
                                        expected_recording_sha256=first['recording_sha256']))
        self.assertEqual(self.outcome()['reason'],'recording_changed')
        self.assertEqual(self.context.tasker.controller.calls,0)
        self.assertFalse(self.context.patches)
        self.ident=str(uuid4())
        self.assertTrue(self.run_phase('begin'))
        self.assertNotEqual(self.outcome()['recording_sha256'],first['recording_sha256'])

    def test_defeat_returns_then_stops_by_default_without_success(self):
        self.assertTrue(self.run_phase('begin'))
        self.assertTrue(self.run_phase('battle_failure'))
        self.assertFalse(self.run_phase('retry_after_failure'))
        self.assertEqual(self.outcome()['reason'], 'battle_failed')
        self.assertEqual(self.outcome()['failure_retries'], 0)
        self.assertFalse(runtime._rounds)

    def test_defeat_retry_bypasses_quota_and_keeps_one_saved_revision(self):
        self.context.nodes['脚本任务设置']['action']['param']['custom_action_param']['failure_retry'] = {
            'enabled': True, 'max_retries': '1'}
        self.assertTrue(self.run_phase('begin'))
        saved_hash = self.outcome()['recording_sha256']
        self.assertTrue(self.run_phase('battle_failure'))
        self.assertTrue(self.run_phase('retry_after_failure'))
        self.assertEqual(self.context.patches[-1], {PREFIX+'失败返回确认': {'next': [PREFIX+'通用导航']}})
        self.assertEqual(self.outcome()['recording_sha256'], saved_hash)
        self.assertEqual(self.outcome()['failure_retries'], 1)
        self.assertTrue(self.run_phase('battle_failure'))
        self.assertFalse(self.run_phase('retry_after_failure'))
        self.assertEqual(self.outcome()['reason'], 'battle_retry_exhausted')
        self.assertFalse(runtime._rounds)

    def test_zero_retries_disables_reopening_even_when_enabled(self):
        self.context.nodes['脚本任务设置']['action']['param']['custom_action_param']['failure_retry'] = {
            'enabled': True, 'max_retries': '0'}
        self.assertTrue(self.run_phase('begin'))
        self.assertTrue(self.run_phase('battle_failure'))
        patches = len(self.context.patches)
        self.assertFalse(self.run_phase('retry_after_failure'))
        self.assertEqual(self.outcome()['reason'], 'battle_failed')
        self.assertEqual(self.outcome()['failure_retries'], 0)
        self.assertNotIn({PREFIX+'失败返回确认': {'next': [PREFIX+'通用导航']}}, self.context.patches[patches:])
        self.assertFalse(self.outcome()['failure_retry']['enabled'])

    def test_defeat_cannot_be_reported_as_success_or_retried_on_unknown_return(self):
        self.assertTrue(self.run_phase('begin'))
        self.assertTrue(self.run_phase('battle_failure'))
        self.assertFalse(self.run_phase('success'))
        self.assertEqual(self.outcome()['status'], 'fatal')
        self.assertTrue(self.run_phase('begin'))
        self.assertTrue(self.run_phase('battle_failure'))
        self.context.run_recognition = lambda *_args: SimpleNamespace(hit=False)
        with patch.object(runtime.GraphNavigator, '_settle'):
            self.assertFalse(self.run_phase('retry_after_failure'))
        self.assertEqual(self.outcome()['status'], 'fatal')

    def test_editing_during_defeat_retry_cannot_switch_recording(self):
        self.assertTrue(self.run_phase('begin'))
        self.save_recording('one.json', 67)
        self.assertFalse(self.run_phase('navigate'))
        self.assertEqual(self.outcome()['reason'], 'recording_changed')


class NativePipelineStructureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pipeline=json.loads((ROOT/'assets/resource/base/pipeline/悬赏（守卫宝烛）.json').read_text(encoding='utf8'))

    def test_references_and_assets_are_self_contained(self):
        def visit(value):
            if isinstance(value,dict):
                for key,item in value.items():
                    if key=='template':
                        for name in ([item] if isinstance(item,str) else item):
                            self.assertTrue((ROOT/'assets/resource/base/image'/name).is_file(),name)
                    else:visit(item)
            elif isinstance(value,list):
                for item in value:visit(item)
        for name,node in self.pipeline.items():
            for field in ('next','on_error'):
                for target in node.get(field,[]):self.assertIn(target,self.pipeline,name)
            visit(node)
        text=json.dumps(self.pipeline,ensure_ascii=False)
        for forbidden in ('Zaomeng','migrated_task','bundle.json','raw_recordings','F:/','backup/'):
            self.assertNotIn(forbidden,text)

    def test_native_entry_cannot_bypass_the_round_initializer_with_a_legacy_alias(self):
        self.assertNotIn(PREFIX+'刷取次数',self.pipeline)
        entry=self.pipeline[PREFIX+'初始化']
        self.assertEqual(entry['action']['param']['custom_action'],'script_task_state')
        self.assertEqual(entry['action']['param']['custom_action_param']['phase'],'begin')

    def test_two_scene_checks_and_two_return_checks_precede_success(self):
        p=PREFIX
        self.assertEqual(self.pipeline[p+'场内初检']['post_delay'],350)
        self.assertIn(p+'回放战斗',self.pipeline[p+'场内初检']['next'])
        self.assertEqual(self.pipeline[p+'回放战斗']['recognition'],self.pipeline[p+'场内初检']['recognition'])
        self.assertEqual(self.pipeline[p+'返回初检']['post_delay'],350)
        self.assertIn(p+'返回复检',self.pipeline[p+'返回初检']['next'])
        self.assertEqual(self.pipeline[p+'返回复检']['next'],[p+'成功'])

    def test_task_detail_and_entry_button_are_checked_in_the_same_frame(self):
        recognition=self.pipeline[PREFIX+'点击前往副本']['recognition']
        self.assertEqual(recognition['type'],'And')
        self.assertEqual(recognition['param']['box_index'],0)
        checks=[item['recognition'] for item in recognition['param']['all_of']]
        self.assertEqual([item['param']['expected'] for item in checks[:2]],
                         [['^前往副本$'],['^守卫宝烛$']])
        self.assertEqual(checks[2]['type'],'Or')
        self.assertEqual(checks[2]['param']['any_of'][0]['recognition']['param']['expected'],['^已接受$'])
        self.assertNotEqual(checks[0]['param']['roi'],checks[1]['param']['roi'])

    def test_empty_slots_have_no_click_fallback(self):
        for slot in range(1,5):
            node=self.pipeline[PREFIX+'恢复存档'+str(slot)]
            text=json.dumps(node['recognition'],ensure_ascii=False)
            self.assertIn('[Ll][Vv]',text)
            self.assertIn('save-'+str(slot)+'.png',text)
            self.assertEqual(node['on_error'],[PREFIX+'失败'])


if __name__=='__main__':
    unittest.main()
