"""Opt-in recognition on packaged pixels; every input callback is forbidden."""
import json
import os
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
PREFIX = '悬赏（守卫宝烛）-'


@unittest.skipUnless(os.environ.get('CANDLE_NATIVE_TESTS') == '1', 'opt-in Maa recognition test')
class NativeCandleRecognitionTests(unittest.TestCase):
    def test_current_ocr_and_native_pipeline_on_packaged_fixtures(self):
        os.environ.setdefault('MAAFW_BINARY_PATH', str(ROOT / 'runtimes/win-x64/native'))
        import numpy as np
        from maa.controller import CustomController
        from maa.custom_action import CustomAction
        from maa.resource import Resource
        from maa.tasker import Tasker
        from fixture_pixels import read_png_bgr

        directory = ROOT / 'tests/fixtures/bounty-candle'
        frames = {path.stem: read_png_bgr(path) for path in directory.glob('*.png')}
        pipeline = json.loads((ROOT / 'assets/resource/base/pipeline/悬赏（守卫宝烛）.json').read_text(encoding='utf8'))
        results = []

        class PixelsOnly(CustomController):
            def connect(self): return True
            def request_uuid(self): return 'native-candle-offline-fixtures'
            def screencap(self): return frames['world'].copy()
            def forbidden(self, *args): raise AssertionError('Real input is forbidden')
            start_app = stop_app = click = swipe = touch_down = touch_move = touch_up = forbidden
            click_key = input_text = key_down = key_up = forbidden

        class Inspect(CustomAction):
            def run(self, context, argv):
                def hit(suffix, pixels):
                    value = context.run_recognition(PREFIX + suffix, pixels)
                    if value is None:
                        raise AssertionError('Recognition did not start: ' + suffix)
                    return value.hit

                def check(label, actual, expected):
                    results.append(dict(check=label, actual=bool(actual), expected=bool(expected), ok=bool(actual)==bool(expected)))
                    print('[candle native] ' + json.dumps(results[-1], ensure_ascii=True), flush=True)

                for name in ('candle-board', 'candle-board-refreshed'):
                    check(name + ': accepted card', hit('核对目标卡片', frames[name]), True)
                wrong = frames['candle-board'].copy()
                wrong[614:684, 253:392] = 0
                wrong[333:403, 794:933] = frames['candle-board-refreshed'][333:403, 794:933]
                check('guard on a different card is rejected', hit('核对目标卡片', wrong), False)
                scale = 720 / 737
                sx, sy, width, height = (round(value * scale) for value in (741,125,253,280))
                card = frames['candle-board-refreshed'][sy:sy+height, sx:sx+width].copy()
                for raw_x, raw_y in ((187,125),(464,125),(741,125),(187,416)):
                    x, y = round(raw_x * scale), round(raw_y * scale)
                    moved = frames['candle-board-refreshed'].copy()
                    moved[sy:sy+height, sx:sx+width] = 0
                    moved[y:y+height, x:x+width] = card
                    result = context.run_recognition(PREFIX + '核对目标卡片', moved)
                    check(f'accepted card moves to {x},{y}', result and result.hit
                          and x <= result.box.x < x+width and y <= result.box.y < y+height, True)
                for name in ('bounty', 'world', 'candle-loading', 'saves'):
                    check(name + ': no accepted card', hit('核对目标卡片', frames[name]), False)
                check('unaccepted board detected', hit('未接目标悬赏', frames['bounty']), True)
                check('world entry', hit('进入须弥山', frames['world']), True)
                check('map-a return', hit('洲地图A返回世界', frames['map-a']), True)
                check('map-b return', hit('洲地图B返回世界', frames['map-b']), True)
                for name in ('candle-hub-paused', 'candle-hub-cloudy-paused'):
                    check(name + ': known paused hub', hit('暂停大厅', frames[name]), True)
                    check(name + ': dimmed modal rejected', hit('暂停大厅', (frames[name] * .55).astype('uint8')), False)
                for name in ('candle-loading', 'bounty', 'saves', 'world'):
                    check(name + ': no cave playback', hit('回放战斗', frames[name]), False)
                    check(name + ': no return completion', hit('返回复检', frames[name]), False)
                check('login entry', hit('恢复登录页', frames['login']), True)
                check('save list', hit('恢复存档页', frames['saves']), True)
                for populated in range(1, 5):
                    frame = frames['saves' if populated == 1 else 'saves-slot' + str(populated)]
                    for selected in range(1, 5):
                        check(f'save slot {selected} with slots 1/{populated} populated',
                              hit('恢复存档' + str(selected), frame), selected in (1, populated))
                # Priority is part of the task contract: a persistent pause must
                # win over a still-visible cave before any playback can start.
                for current, frame, expected in (
                    ('等待场内', 'candle-battle-paused', '场内恢复'),
                    ('场内初检', 'candle-battle-paused', '场内暂停中止'),
                    ('等待返回', 'candle-hub-paused', '返回恢复'),
                    ('返回初检', 'candle-hub-paused', '返回暂停中止')):
                    first = next((node for node in pipeline[PREFIX + current]['next']
                                  if hit(node[len(PREFIX):], frames[frame])), None)
                    check(current + ': pause priority', first == PREFIX + expected, True)
                check('black frame cannot complete', hit('返回复检', np.zeros_like(frames['world'])), False)
                check('ready cave can start playback', hit('回放战斗', frames['candle-battle-ready']), True)
                check('ready hub can finish return', hit('返回复检', frames['candle-hub-ready']), True)
                return True

        log = ROOT / '.runtime-cache/native-candle-recognition'
        log.mkdir(parents=True, exist_ok=True)
        Tasker.set_log_dir(log)
        controller, resource, tasker = PixelsOnly(), Resource(), Tasker()
        self.assertTrue(controller.post_connection().wait().succeeded)
        self.assertTrue(controller.set_screenshot_use_raw_size(True))
        self.assertTrue(resource.post_bundle(ROOT / 'assets/resource/base').wait().succeeded)
        self.assertTrue(resource.override_pipeline({'__NativeCandleInspect': {
            'action': {'type': 'Custom', 'param': {'custom_action': '__inspect_candle'}}}}))
        self.assertTrue(resource.register_custom_action('__inspect_candle', Inspect()))
        self.assertTrue(tasker.bind(resource, controller))
        self.assertTrue(tasker.post_task('__NativeCandleInspect').wait().succeeded)
        (log / 'checks.json').write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding='utf8')
        self.assertGreaterEqual(len(results), 40)
        self.assertEqual([item for item in results if not item['ok']], [])

    def test_native_round_executes_saved_library_file_and_confirms_return(self):
        """Run the real engine and actions against a simulated screen/controller."""
        import importlib.util
        import sys
        import tempfile
        from types import ModuleType
        from unittest.mock import patch
        from uuid import uuid4

        os.environ.setdefault('MAAFW_BINARY_PATH', str(ROOT / 'runtimes/win-x64/native'))
        from maa.controller import CustomController
        from maa.resource import Resource
        from maa.tasker import Tasker
        from fixture_pixels import read_png_bgr

        registration = ModuleType('maa.agent.agent_server')
        registration.AgentServer = type('RegistrationOnly', (), {
            'custom_action': staticmethod(lambda _name: lambda implementation: implementation)})
        def load(name, filename):
            spec = importlib.util.spec_from_file_location(name, ROOT / 'agent' / filename)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            return module
        with patch.dict(sys.modules, {'maa.agent': ModuleType('maa.agent'), 'maa.agent.agent_server': registration}), \
                patch.object(sys, 'path', [str(ROOT / 'agent'), *sys.path]):
            state_actions = load('native_fixture_candle_actions', 'script_task.py')
            playback = load('native_fixture_battle_player', 'battle_player.py')
            from script_library import bind_task_recording

        frames = {name: read_png_bgr(ROOT / 'tests/fixtures/bounty-candle' / (name + '.png'))
                  for name in ('candle-board', 'candle-battle-ready', 'candle-hub-ready', 'login', 'saves-slot4', 'world')}
        events = []

        class SimulatedGame(CustomController):
            stage = 'candle-board'
            def connect(self): return True
            def request_uuid(self): return 'native-candle-round-simulation'
            def screencap(self): return frames[self.stage].copy()
            def touch_down(self, contact, x, y, pressure):
                events.append(('mouse_down', contact, x, y))
                self.last_down = (x, y)
                return True
            def touch_move(self, contact, x, y, pressure): return True
            def touch_up(self, contact):
                events.append(('mouse_up', contact))
                if self.stage == 'candle-board': self.stage = 'candle-battle-ready'
                elif self.stage == 'login': self.stage = 'saves-slot4'
                elif self.stage == 'saves-slot4':
                    x, y = self.last_down
                    if not (560 < x < 900 and 260 < y < 390):
                        raise AssertionError('Recovery clicked a different save slot')
                    self.stage = 'world'
                return True
            def key_down(self, keycode):
                events.append(('key_down', keycode))
                return True
            def key_up(self, keycode):
                events.append(('key_up', keycode))
                self.stage = 'candle-hub-ready'
                return True
            def forbidden(self, *args): raise AssertionError('Unexpected simulated device action')
            start_app = stop_app = click = swipe = click_key = input_text = forbidden

        with tempfile.TemporaryDirectory(prefix='native-candle-round-') as temporary:
            directory = Path(temporary)
            library, checkpoints = directory / 'recordings', directory / 'debug/task-runs'
            library.mkdir()
            checkpoints.mkdir(parents=True)
            filename = 'native-round.json'
            script = dict(version=1, name='Memory controller round', base=[1148,720], duration_ms=3,
                          actions=[dict(t=0,op='keydown',key=65),dict(t=1,op='keyup',key=65)])
            raw = json.dumps(script).encode('utf8')
            (library / filename).write_bytes(raw)
            import hashlib
            bind_task_recording(library, 'bounty-candle', filename, expected_revision=hashlib.sha256(raw).hexdigest())
            state_actions.ROOT = directory
            state_actions.RECORDINGS, state_actions.STATUS_DIRECTORY = library, checkpoints
            playback.RECORDING_DIR = str(library)
            playback._toast = lambda *_args: None
            run_id = str(uuid4())
            parameters = dict(phase='begin', file='@task:bounty-candle', exit_mode='recorded_return',
                              run_id=run_id, instance_id='memory-test', status_path=str(checkpoints / (run_id + '.json')))
            canonical = json.loads((ROOT/'assets/resource/base/pipeline/悬赏（守卫宝烛）.json').read_text(encoding='utf8'))
            parameters = {**canonical[PREFIX+'初始化']['action']['param']['custom_action_param'], **parameters}
            controller, resource, tasker = SimulatedGame(), Resource(), Tasker()
            self.assertTrue(controller.post_connection().wait().succeeded)
            self.assertTrue(controller.set_screenshot_use_raw_size(True))
            self.assertTrue(resource.post_bundle(ROOT / 'assets/resource/base').wait().succeeded)
            self.assertTrue(resource.register_custom_action('script_task_state', state_actions.ScriptTaskState()))
            self.assertTrue(resource.register_custom_action('battle_play', playback.BattlePlay()))
            self.assertTrue(tasker.bind(resource, controller))
            overrides = {PREFIX + '初始化': {'action': {'param': {'custom_action_param': parameters}}}}
            self.assertTrue(tasker.post_task(PREFIX + '初始化', overrides).wait().succeeded)
            checkpoint = json.loads((checkpoints / (run_id + '.json')).read_text(encoding='utf8'))
            self.assertEqual(checkpoint['status'], 'success')
            self.assertEqual(checkpoint['recording_file'], filename)
            self.assertEqual([event for event in events if event[0].startswith('key_')], [('key_down',65),('key_up',65)])
            self.assertEqual(len([event for event in events if event[0]=='mouse_down']), 1)
            # Save an edit to the same file, without rebinding or creating a copy.
            script['actions'] = [dict(t=0,op='keydown',key=66),dict(t=1,op='keyup',key=66)]
            (library / filename).write_text(json.dumps(script), encoding='utf8')
            events.clear()
            controller.stage = 'candle-board'
            run_id = str(uuid4())
            parameters.update(run_id=run_id, status_path=str(checkpoints / (run_id + '.json')))
            self.assertTrue(tasker.post_task(PREFIX + '初始化', overrides).wait().succeeded)
            checkpoint = json.loads((checkpoints / (run_id + '.json')).read_text(encoding='utf8'))
            self.assertEqual(checkpoint['status'], 'success')
            self.assertEqual([event for event in events if event[0].startswith('key_')], [('key_down',66),('key_up',66)])
            events.clear()
            controller.stage = 'login'
            run_id = str(uuid4())
            parameters.update(canonical[PREFIX+'恢复登录']['action']['param']['custom_action_param'])
            parameters.update(phase='recovery_begin', save_slot=4, run_id=run_id,
                              status_path=str(checkpoints / (run_id + '.json')))
            resume_overrides = {PREFIX + '恢复登录': {'action': {'param': {'custom_action_param': parameters}}}}
            self.assertTrue(tasker.post_task(PREFIX + '恢复登录', resume_overrides).wait().succeeded)
            checkpoint = json.loads((checkpoints / (run_id + '.json')).read_text(encoding='utf8'))
            self.assertEqual(checkpoint['status'], 'ready')
            self.assertEqual(checkpoint['save_slot'], 4)
            self.assertEqual(controller.stage, 'world')
            self.assertEqual(len([event for event in events if event[0]=='mouse_down']), 2)


if __name__ == '__main__':
    unittest.main()
