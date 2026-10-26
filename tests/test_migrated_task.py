"""Private task migration with fake windows, frames and Maa; never send input."""

from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import threading
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch

AGENT = Path(__file__).resolve().parents[1] / 'agent'
sys.path.insert(0, str(AGENT))
from task_migration.navigation import (GraphNavigator, NavigationError, prune_navigation,
                                       select_state, shortest_path)


def load_runtime():
    modules = {name: ModuleType(name) for name in ('maa', 'maa.agent', 'maa.agent.agent_server', 'maa.custom_action')}
    class AgentServer:
        @staticmethod
        def custom_action(_name):
            return lambda implementation: implementation
    modules['maa.agent.agent_server'].AgentServer = AgentServer
    modules['maa.custom_action'].CustomAction = object
    spec = importlib.util.spec_from_file_location('isolated_migrated_task', AGENT / 'migrated_task.py')
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, modules):
        spec.loader.exec_module(module)
    return module


runtime = load_runtime()


def node(expected=None, *, action=None, next=None, **extra):
    return dict(recognition=dict(type='DirectHit') if expected is None else dict(type='OCR', param=dict(expected=[expected])),
                action=action or dict(type='DoNothing'), next=next or [], **extra)


def bundle_data(iterations=1):
    pipeline = {name: node(state) for name, state in (('MapProbe', 'map'), ('HubProbe', 'hub'), ('BoardProbe', 'board'),
                                                     ('PauseProbe', 'pause'), ('Blocked', 'blocked'))}
    pipeline.update(Edge_MapHub=node('map', action=dict(type='Click')), Edge_HubBoard=node('hub', action=dict(type='Click')),
        Enter=node('board', action=dict(type='Click'), next=['Blocked', 'Ready1']),
        Ready1=node('ready', next=['Blocked', 'Replay'], post_delay=350),
        Replay=node('ready', action=dict(type='Custom', param=dict(custom_action='ZaomengRecordedMacro',
            custom_action_param=dict(macro='source-macro'))), next=['Blocked', 'Returned']),
        Returned=node('returned', next=['Success'], post_delay=350), Success=node())
    nav = dict(version=1, targets=['board'], max_steps=10, max_seconds=20, settle_ms=300, locate_attempts=2,
               abort={'Blocked': '用户需要处理异常'}, nodes=[
        dict(id='map', label='地图', recognition=['MapProbe']), dict(id='hub', label='大厅', recognition=['HubProbe']),
        dict(id='board', label='已接任务', recognition=['BoardProbe'], exclude=['PauseProbe'])], edges=[
        dict(id='map_hub', source='map', target='hub', label='进入大厅', entry='Edge_MapHub', cost=1, timeout_ms=1000, max_attempts=1),
        dict(id='hub_board', source='hub', target='board', label='打开任务', entry='Edge_HubBoard', cost=1, timeout_ms=1000, max_attempts=1)])
    return dict(version=1, source_size=[1175, 737], pipeline=pipeline, navigation=nav, entry='Enter',
                success_node='Success', failure_nodes={'Blocked': '用户需要处理异常'}, iterations=iterations,
                macros={'source-macro': 'raw_recordings/source-macro.json'}, max_seconds=30,
                recovery=dict(enabled=True, save_slot=1, note='metadata only'))


class FakeClock:
    def __init__(self): self.value = 100.0
    def __call__(self): return self.value
    def wait(self, seconds): self.value += max(0, seconds)


class FakeImage:
    def __init__(self, state, size=(1175, 737)):
        self.state, self.shape = state, (size[1], size[0], 3)
    def copy(self): return FakeImage(self.state, (self.shape[1], self.shape[0]))


class FakeController:
    def __init__(self, context):
        self.context = context
        self.info = dict(type='win32', hwnd=42, keyboard_method=1, mouse_method=1)
        self.resolution = (1175, 737)
        self.image_size = (1175, 737)
        self.screenshots = 0
        self.pending = False
    def post_screencap(self):
        self.screenshots += 1
        return SimpleNamespace(done=not self.pending, succeeded=True)
    @property
    def cached_image(self): return FakeImage(self.context.state, self.image_size)


class FakeContext:
    def __init__(self, clock):
        self.clock, self.state = clock, 'map'
        self.pipeline, self.inputs, self.reco_images, self.replay_waits = {}, [], [], []
        self.body_calls = 0
        self.skip_success = self.fail_replay = self.fail_return = self.stop_replay = False
        self.block_body = False
        self.landing = 'hub'
        self.override_ok = True
        self.tasker = SimpleNamespace(stopping=False, controller=FakeController(self), post_stop=self.stop)
        self.stopped = threading.Event()
    def stop(self):
        self.tasker.stopping = True
        self.stopped.set()
        return SimpleNamespace(done=True, succeeded=True)
    def override_pipeline(self, pipeline):
        self.pipeline.update(deepcopy(pipeline))
        return self.override_ok
    def run_recognition(self, name, pixels):
        self.reco_images.append((name, id(pixels), pixels.state))
        return SimpleNamespace(hit=self.hit(self.pipeline[name], pixels.state))
    def hit(self, value, state):
        recognition = value.get('recognition', {})
        if recognition.get('type') == 'DirectHit': return True
        return state in recognition.get('param', {}).get('expected', [])
    def run_task(self, wrapper):
        current = wrapper
        for _ in range(30):
            value = self.pipeline[current]
            action = value.get('action', {})
            kind = action.get('type', 'DoNothing')
            if kind == 'Click':
                self.inputs.append(current)
                if current.endswith('Edge_MapHub'): self.state = self.landing
                elif current.endswith('Edge_HubBoard'): self.state = 'board'
                elif current.endswith('Enter'):
                    self.body_calls += 1
                    self.state = 'blocked' if self.block_body else 'ready'
            elif kind == 'Custom':
                param = action['param']
                if param['custom_action'] == 'battle_play':
                    if self.fail_replay: return SimpleNamespace(status=SimpleNamespace(succeeded=False))
                    data = json.loads(Path(param['custom_action_param']['file']).read_text(encoding='utf-8'))
                    for item in data['actions']:
                        self.replay_waits.append(item['t'])
                        self.clock.wait(item['t']/1000)
                    tail = data['duration_ms'] - sum(item['t'] for item in data['actions'])
                    self.replay_waits.append(tail)
                    self.clock.wait(tail/1000)
                    if self.stop_replay: self.stop()
                    self.state = 'unknown' if self.fail_return else 'returned'
                elif param['custom_action'] == runtime.OUTCOME_ACTION:
                    if self.skip_success and param['custom_action_param'].get('success'):
                        return SimpleNamespace(status=SimpleNamespace(succeeded=True))
                    result = runtime.MigratedTaskOutcome().run(self, SimpleNamespace(
                        custom_action_param=json.dumps(param['custom_action_param'])))
                    if not result: return SimpleNamespace(status=SimpleNamespace(succeeded=False))
            self.clock.wait(value.get('post_delay', 0)/1000)
            candidates = value.get('next', [])
            if not candidates: return SimpleNamespace(status=SimpleNamespace(succeeded=True))
            current = next((name for name in candidates if self.hit(self.pipeline[name], self.state)), None)
            if current is None: return SimpleNamespace(status=SimpleNamespace(succeeded=False))
        raise AssertionError('fake graph did not terminate')


class FakeGuard:
    def __init__(self, context, clock):
        self.context, self.clock = context, clock
        self.deadline = None
        self.closed = self.started = False
        self.focus = True
    def check(self):
        if self.context.tasker.stopping or not self.focus: raise runtime.TaskStopped('fake stop/focus')
        if self.deadline is not None and self.clock() >= self.deadline: raise TimeoutError('fake deadline')
    def start(self): self.started = True
    def close(self): self.closed = True
    def await_foreground(self, **_kwargs): self.check()


class MigratedTaskTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.directory = self.root / 'tasks' / '11111111-1111-4111-8111-111111111111'
        (self.directory / 'raw_recordings').mkdir(parents=True)
        self.path = self.directory / 'bundle.json'
        self.macro = dict(version=1, name='私有原尺寸录制', base=[1175, 737], duration_ms=250,
                          actions=[dict(t=10, op='keydown', key=65), dict(t=100, op='keyup', key=65)])
        self.macro_path = self.directory / 'raw_recordings' / 'source-macro.json'
        self.write(self.macro_path, self.macro)
        self.write(self.path, bundle_data())
        self.clock = FakeClock()
        self.context = FakeContext(self.clock)
        self.guard = FakeGuard(self.context, self.clock)
        self.logs = []
    @staticmethod
    def write(path, data): path.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
    def make_runner(self, data=None):
        if data is not None: self.write(self.path, data)
        return runtime.MigratedTaskRunner(self.context, runtime.load_bundle(self.path, self.root),
            guard=self.guard, clock=self.clock, wait=self.clock.wait, log=self.logs.append)

    def test_actual_navigation_body_return_and_tail_are_required_before_counting(self):
        runner = self.make_runner()
        self.assertEqual(runner.run(), 1)
        self.assertEqual(self.context.body_calls, 1)
        self.assertEqual(self.context.replay_waits, [10, 100, 140])
        self.assertEqual(len(self.context.inputs), 3)  # Two navigation clicks + one entry; no extra exit.
        self.assertTrue(self.guard.closed)
        self.assertNotIn(runner.runtime.token, runtime._RUNS)
        self.assertEqual(self.macro_path.read_text(encoding='utf-8'), json.dumps(self.macro, ensure_ascii=False))

    def test_default_one_call_does_not_multiply_source_repeat_metadata(self):
        data = bundle_data()
        data['source_request'] = dict(iterations=999, enabled=True)
        runner = self.make_runner(data)
        self.assertEqual(runner.run(), 1)
        self.assertEqual(runner.completed, 1)

    def test_explicit_iteration_count_relocalizes_and_only_counts_confirmed_rounds(self):
        # The end image in a real bundle is the next round's hub. Let this fake
        # return page be recognized as a supported hub on the second round.
        data = bundle_data(iterations=2)
        data['pipeline']['HubProbe']['recognition']['param']['expected'].append('returned')
        data['pipeline']['Edge_HubBoard']['recognition']['param']['expected'].append('returned')
        runner = self.make_runner(data)
        self.assertEqual(runner.run(), 2)
        self.assertEqual(self.context.body_calls, 2)

    def test_missing_success_outcome_is_not_a_success_even_if_native_task_succeeded(self):
        self.context.skip_success = True
        runner = self.make_runner()
        with self.assertRaisesRegex(RuntimeError, '未确认'):
            runner.run(999)
        self.assertEqual(runner.completed, 0)
        self.assertEqual(self.context.body_calls, 1)

    def test_replay_failure_or_unconfirmed_return_never_starts_a_second_round(self):
        for mode in ('fail_replay', 'fail_return'):
            with self.subTest(mode=mode):
                self.context.state = 'map'
                self.context.body_calls = 0
                self.context.fail_replay = self.context.fail_return = False
                setattr(self.context, mode, True)
                runner = self.make_runner()
                with self.assertRaises(RuntimeError): runner.run(999)
                self.assertEqual(runner.completed, 0)
                self.assertEqual(self.context.body_calls, 1)

    def test_global_abort_or_unknown_screen_sends_no_input(self):
        for state in ('blocked', 'unknown', 'pause'):
            with self.subTest(state=state):
                self.context.state = state
                runner = self.make_runner()
                with self.assertRaises(NavigationError): runner.run()
                self.assertFalse(self.context.inputs)
                self.assertEqual(runner.completed, 0)

    def test_cancellation_interrupts_navigation_settle_and_macro_completion(self):
        runner = self.make_runner()
        def cancel_wait(seconds):
            self.clock.wait(seconds)
            self.context.stop()
        runner.wait = cancel_wait
        with self.assertRaises(runtime.TaskStopped): runner.run()
        self.assertEqual(len(self.context.inputs), 1)
        self.assertEqual(runner.completed, 0)
        self.context.tasker.stopping = False
        self.context.state = 'map'
        self.context.stop_replay = True
        runner = self.make_runner()
        with self.assertRaises(runtime.TaskStopped): runner.run(999)
        self.assertEqual(runner.completed, 0)
        self.assertEqual(self.context.body_calls, 1)

    def test_scaled_controller_frame_is_rejected_before_navigation_input(self):
        self.context.tasker.controller.image_size = (1148, 720)
        runner = self.make_runner()
        with self.assertRaisesRegex(ValueError, '原尺寸控制器'): runner.run()
        self.assertFalse(self.context.inputs)

    def test_720_macro_copy_is_rejected_before_pipeline_install(self):
        self.macro['base'] = [1148, 720]
        self.write(self.macro_path, self.macro)
        with self.assertRaisesRegex(ValueError, '720p'): self.make_runner()
        self.assertFalse(self.context.pipeline)

    def test_private_path_escape_and_unbalanced_recordings_are_rejected(self):
        data = bundle_data()
        outside = self.root.parent / 'outside-recording.json'
        data['macros']['source-macro'] = str(outside)
        with self.assertRaisesRegex(ValueError, '私有目录以外'): self.make_runner(data)
        self.macro['actions'].pop()
        self.write(self.macro_path, self.macro)
        with self.assertRaisesRegex(ValueError, '修复'): self.make_runner(bundle_data())

    def test_prefixes_and_template_paths_are_isolated_without_mutating_bundle(self):
        image = self.directory / 'image' / '中文模板.png'
        image.parent.mkdir()
        image.write_bytes(b'fake-template')
        data = bundle_data()
        data['pipeline']['Template'] = dict(recognition=dict(type='TemplateMatch', param=dict(template='中文模板.png', roi=[1, 2, 3, 4])), action=dict(type='DoNothing'))
        self.write(self.path, data)
        bundle = runtime.load_bundle(self.path, self.root)
        before = deepcopy(bundle.data)
        first, second = runtime.prepare_bundle(bundle), runtime.prepare_bundle(bundle)
        self.assertFalse(set(first.pipeline) & set(second.pipeline))
        template = next(value for key, value in first.pipeline.items() if key.endswith('_Template'))
        self.assertEqual(template['recognition']['param'], dict(template=str(image), roi=[1, 2, 3, 4]))
        self.assertEqual(bundle.data, before)

    def test_same_frame_probe_results_are_reused_for_each_location_pass(self):
        runner = self.make_runner()
        runner.run()
        images = {}
        for name, image, state in self.context.reco_images:
            images.setdefault((image, state), []).append(name)
        self.assertTrue(images)
        self.assertTrue(all(len(names) == len(set(names)) for names in images.values()))

    def test_readonly_foreground_watchdog_stops_tasker_without_sending_input(self):
        class Windows:
            foreground = True
            def inspect(self, _hwnd): return dict(pid=10, class_name='Game', width=1175, height=737)
            def is_foreground(self, _hwnd): return self.foreground
        windows = Windows()
        guard = runtime.ForegroundGuard(self.context, [1175, 737], window_api=windows)
        guard.start()
        windows.foreground = False
        self.assertTrue(self.context.stopped.wait(.5))
        guard.close()
        with self.assertRaisesRegex(runtime.TaskStopped, '焦点'): guard.check()
        self.assertFalse(self.context.inputs)

    def test_source_graph_pruning_preserves_business_dependencies_and_rejects_ambiguity(self):
        data = bundle_data()
        data['pipeline'].update(Unreachable=node('other'), UnusedAction=node('other', action=dict(type='Click')))
        data['navigation']['nodes'].append(dict(id='unreachable', label='不可达', recognition=['Unreachable']))
        pipeline, nav = prune_navigation(data['pipeline'], data['navigation'], data['entry'], data['success_node'], data['failure_nodes'])
        self.assertNotIn('Unreachable', pipeline)
        self.assertNotIn('UnusedAction', pipeline)
        self.assertIn('Replay', pipeline)
        self.assertEqual(len(nav['nodes']), 3)
        self.assertEqual([edge['id'] for edge in shortest_path(nav, 'map')], ['map_hub', 'hub_board'])
        with self.assertRaises(NavigationError): select_state(nav, {'MapProbe', 'HubProbe'})

    def test_business_failure_outcome_records_reason_and_stops_without_counting(self):
        self.context.block_body = True
        runner = self.make_runner()
        with self.assertRaisesRegex(RuntimeError, '用户需要处理异常'): runner.run(999)
        self.assertEqual(runner.completed, 0)
        self.assertEqual(self.context.body_calls, 1)

    def test_route_is_replanned_from_actual_landing_screen(self):
        self.context.landing = 'board'
        runner = self.make_runner()
        self.assertEqual(runner.run(), 1)
        self.assertEqual(len(self.context.inputs), 2)
        self.assertFalse(any(name.endswith('Edge_HubBoard') for name in self.context.inputs))

    def test_route_attempt_limit_prevents_looping_when_click_does_not_navigate(self):
        self.context.landing = 'map'
        runner = self.make_runner()
        with self.assertRaises(NavigationError): runner.run(999)
        self.assertEqual(len(self.context.inputs), 1)
        self.assertEqual(runner.completed, 0)

    def test_pruning_preserves_transitive_overlay_disambiguation(self):
        data = bundle_data()
        data['pipeline']['Intermediate'] = node('intermediate')
        data['navigation']['nodes'][2]['obscures'] = ['intermediate']
        data['navigation']['nodes'].append(dict(id='intermediate', label='中间面板',
                                               recognition=['Intermediate'], obscures=['map']))
        _, nav = prune_navigation(data['pipeline'], data['navigation'], data['entry'])
        self.assertNotIn('intermediate', {node['id'] for node in nav['nodes']})
        self.assertEqual(select_state(nav, {'MapProbe', 'BoardProbe'}), 'board')

    def make_foreground_guard(self):
        class Windows:
            foreground = False
            width = 1175
            def inspect(self, _hwnd): return dict(pid=10, class_name='Game', width=self.width, height=737)
            def is_foreground(self, _hwnd): return self.foreground
        windows = Windows()
        guard = runtime.ForegroundGuard(self.context, [1175, 737], window_api=windows, clock=self.clock)
        return guard, windows

    def test_start_waits_for_manual_focus_without_sending_input_or_stealing_focus(self):
        guard, windows = self.make_foreground_guard()
        notifications = []
        def wait(seconds):
            self.clock.wait(seconds)
            if self.clock() >= 100.15: windows.foreground = True
        guard.await_foreground(wait=wait, emit=self.logs.append, notify=lambda *args: notifications.append(args))
        self.assertLess(self.clock(), 101)
        self.assertTrue(notifications)
        self.assertTrue(self.logs)
        self.assertFalse(self.context.inputs)
        self.assertFalse(self.context.tasker.stopping)

    def test_focus_start_wait_has_timeout_and_stop_checks(self):
        guard, _windows = self.make_foreground_guard()
        with self.assertRaisesRegex(runtime.TaskStopped, '获焦超时'):
            guard.await_foreground(timeout=1, wait=self.clock.wait, notify=lambda *_args: None)
        self.assertAlmostEqual(self.clock(), 101)
        self.context.tasker.stopping = True
        with self.assertRaises(runtime.TaskStopped):
            guard.await_foreground(wait=self.clock.wait, notify=lambda *_args: None)
        self.assertFalse(self.context.inputs)

    def test_start_wait_keeps_validating_target_identity_and_dimensions(self):
        guard, windows = self.make_foreground_guard()
        def changed_window(seconds):
            self.clock.wait(seconds)
            windows.width = 1200
        with self.assertRaisesRegex(runtime.TaskStopped, '尺寸已改变'):
            guard.await_foreground(wait=changed_window, notify=lambda *_args: None)
        self.assertFalse(self.context.inputs)

    def test_message_input_controller_is_rejected_before_window_api_creation(self):
        self.context.tasker.controller.info['mouse_method'] = 2
        with self.assertRaisesRegex(ValueError, 'Seize'):
            runtime.ForegroundGuard(self.context, [1175, 737])
        self.assertFalse(self.context.inputs)


if __name__ == '__main__': unittest.main()
