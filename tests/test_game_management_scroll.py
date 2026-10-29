"""Bounded viewport restoration using public SWF pixels, never a desktop window."""
import hashlib
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'agent'))
from navigation.stage_view import DATA, restore_stage_view


class StageScrollTests(unittest.TestCase):
    def test_horizontal_targets_keep_opposite_native_match_relative_drags(self):
        data = json.loads(DATA.read_text(encoding='utf8'))
        nodes = json.loads((ROOT / 'assets/resource/base/pipeline/game_management_scroll.json').read_text(encoding='utf8'))
        recipes = {item['task']: item for item in data['maps']}
        for task, dx in [('伽岚（万世冥阁）', -440), ('无冥（孤魂岭）', 440)]:
            node = nodes[recipes[task]['drag_node']]
            guard = node['recognition']['param']
            self.assertEqual(guard['all_of'][:2], ['Nav_TaskMap', 'GameManagement_NormalMap'])
            self.assertEqual(guard['all_of'][3], 'Result_NoRevivePrompt')
            self.assertEqual(guard['box_index'], 2)
            action = node['action']['param']
            self.assertIs(action['begin'], True)
            self.assertIs(action['end'], True)
            self.assertEqual(action['end_offset'], [dx, 0, 0, 0])
            x, y, w, h = nodes[guard['all_of'][2]]['recognition']['param']['roi']
            self.assertGreaterEqual(x + dx, 0)
            self.assertLessEqual(x + w + dx, 1101)
            self.assertLess(y + h, 600)

    def test_cancellation_after_recognition_prevents_pending_drag(self):
        recipe = json.loads(DATA.read_text(encoding='utf8'))['maps'][0]
        cancelled = False
        def check():
            if cancelled: raise InterruptedError('cancelled during recognition')
        def recognize(name, frame):
            nonlocal cancelled
            cancelled = name == recipe['drag_node']
            return SimpleNamespace(hit=cancelled)
        actions = []
        context = SimpleNamespace(run_recognition=recognize, run_task=lambda *args: actions.append(args))
        navigator = SimpleNamespace(clock=lambda: 0, _check=check, locate=lambda: 'task_map',
                                    capture=lambda: None, log=lambda _: None, _settle=lambda: None)
        with self.assertRaises(InterruptedError): restore_stage_view(context, navigator, recipe['target'])
        self.assertFalse(actions)

    def test_relocalizes_after_drag_and_never_uses_a_saved_target_box(self):
        recipe = json.loads(DATA.read_text(encoding='utf8'))['maps'][0]
        frames = iter(['before', 'after'])
        seen, actions = [], []
        def recognize(name, frame):
            seen.append((name, frame))
            return SimpleNamespace(hit=name == recipe['drag_node'] or frame == 'after')
        context = SimpleNamespace(run_recognition=recognize, run_task=lambda *args:
            actions.append(args) or SimpleNamespace(status=SimpleNamespace(succeeded=True)))
        navigator = SimpleNamespace(clock=lambda: 0, _check=lambda: None, locate=lambda: 'task_map',
                                    capture=lambda: next(frames), log=lambda _: None, _settle=lambda: None)
        self.assertTrue(restore_stage_view(context, navigator, recipe['target']))
        self.assertEqual(len(actions), 1)
        self.assertEqual(seen[-1], (recipe['target'], 'after'))

    def test_landmarks_exclude_clickable_stages_and_have_pinned_sources(self):
        data = json.loads(DATA.read_text(encoding='utf8'))
        for asset in data['assets']:
            path = ROOT / 'assets/resource/base/image' / asset['path']
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), asset['sha256'])
            self.assertEqual(len(asset['source_sha256']), 64)
        for recipe in data['maps']:
            self.assertLessEqual(recipe['max_steps'], 12)
            for crop in recipe['crops']:
                self.assertFalse(any(crop[0] < b[2] and crop[2] > b[0] and crop[1] < b[3] and crop[3] > b[1]
                                     for b in recipe['excluded_stage_bounds']))

    def test_no_drag_on_unknown_map_missing_guard_or_cancellation_and_bounded_failure(self):
        recipe = json.loads(DATA.read_text(encoding='utf8'))['maps'][0]
        target = recipe['target']
        actions = []
        context = SimpleNamespace(
            run_recognition=lambda name, frame: SimpleNamespace(hit=name == recipe['drag_node']),
            run_task=lambda name, patch: actions.append((name, patch)) or SimpleNamespace(status=SimpleNamespace(succeeded=True)))
        navigator = SimpleNamespace(clock=lambda: 0, _check=lambda: None, locate=lambda: 'profile',
                                    capture=lambda: None, log=lambda _: None, _settle=lambda: None)
        self.assertFalse(restore_stage_view(context, navigator, target))
        self.assertEqual(actions, [])
        navigator.locate = lambda: 'task_map'
        context.run_recognition = lambda *_: SimpleNamespace(hit=False)
        self.assertFalse(restore_stage_view(context, navigator, target))
        self.assertEqual(actions, [])
        def cancelled(): raise InterruptedError('cancelled')
        navigator._check = cancelled
        with self.assertRaises(InterruptedError): restore_stage_view(context, navigator, target)
        self.assertEqual(actions, [])
        navigator._check = lambda: None
        context.run_recognition = lambda name, _: SimpleNamespace(hit=name == recipe['drag_node'])
        self.assertFalse(restore_stage_view(context, navigator, target))
        self.assertEqual(len(actions), recipe['max_steps'])
        self.assertTrue(all(p[n]['next'] == [recipe['drag_node']] for n, p in actions))


@unittest.skipUnless(os.environ.get('NAVIGATION_NATIVE_TESTS') == '1', 'opt-in Maa memory controller')
class StageScrollNativeTests(unittest.TestCase):
    def test_scroll_targets_restore_and_reject_wrong_mode_map_and_prompt(self):
        import numpy as np
        from maa.controller import CustomController
        from maa.custom_action import CustomAction
        from maa.resource import Resource
        from maa.tasker import Tasker
        from battle_ui import NoRevivePrompt
        from fixture_pixels import read_png_bgr
        from navigation.catalog import task_graph
        from navigation.runtime import GraphNavigator
        fixtures = ROOT / 'tests/fixtures/game-management/scroll'
        recipes = json.loads(DATA.read_text(encoding='utf8'))['maps']
        maps = {r['town']: read_png_bgr(fixtures / r['fixture']) for r in recipes}
        mode = read_png_bgr(ROOT / 'assets/resource/base/image/navigation/scroll/normal-mode.png')
        task = read_png_bgr(ROOT / 'assets/resource/base/image/任务.png')
        checks, errors = [], []

        class Pixels(CustomController):
            recipe = recipes[0]
            offset = 0
            gestures = []
            def connect(self): return True
            def request_uuid(self): return 'scroll-memory-only'
            def screencap(self):
                x, y = (self.offset, 0) if self.recipe['axis'] == 'x' else (0, self.offset)
                frame = maps[self.recipe['town']][y:y+720, x:x+1147].copy()
                frame[620:620+task.shape[0], 800:800+task.shape[1]] = task
                # Green-mask pixels are replaced with the actual map; the fixture
                # map uses an independently resampled whole SWF frame.
                region = frame[617:617+mode.shape[0], 1036:1036+mode.shape[1]]
                mask = np.any(mode != (0, 255, 0), axis=2)
                region[mask] = mode[mask]
                return frame
            def touch_down(self, contact, x, y, pressure):
                self.start = self.end = (x, y)
                return True
            def touch_move(self, contact, x, y, pressure):
                self.end = (x, y)
                return True
            def touch_up(self, contact):
                self.gestures.append((self.start, self.end))
                axis = 0 if self.recipe['axis'] == 'x' else 1
                delta = self.start[axis] - self.end[axis]
                maximum = round(((1500-940) if axis == 0 else (2714-590))*720/590)
                self.offset = min(maximum, max(0, self.offset + delta))
                return True
            def forbidden(self, *args): raise AssertionError('Unexpected input')
            click = swipe = key_down = key_up = input_text = start_app = stop_app = forbidden

        controller = Pixels()
        class Inspect(CustomAction):
            def run(self, context, argv):
                try:
                    for recipe in recipes:
                        controller.recipe = recipe
                        controller.offset = round((1500-940)*720/590) if recipe['drag_node'].endswith('Back') else 0
                        controller.gestures = []
                        frame = controller.screencap()
                        assert not context.run_recognition(recipe['target'], frame).hit, recipe['town']
                        assert context.run_recognition(recipe['drag_node'], frame).hit, ('initial guard', recipe['town'])
                        wrong_mode = frame.copy(); wrong_mode[580:720, 980:1147] = 0
                        assert not context.run_recognition(recipe['drag_node'], wrong_mode).hit, 'wrong mode'
                        other = next(r for r in recipes if r['town'] != recipe['town'])
                        assert not context.run_recognition(other['drag_node'], frame).hit, 'wrong map'
                        prompt = read_png_bgr(ROOT / 'tests/fixtures/navigation/revival/normal-p1-up.png')
                        # Preserve recognized map/mode/terrain under the modal.
                        modal = frame.copy(); modal[100:550, 270:880] = prompt[100:550, 270:880]
                        assert not context.run_recognition(recipe['drag_node'], modal).hit, 'revival prompt'
                        graph = task_graph(context, dict(navigation=dict(kind='stage', target_node=recipe['target'])))
                        navigator = GraphNavigator(context, graph, capture=controller.screencap, check=lambda: None)
                        assert restore_stage_view(context, navigator, recipe['target']), (recipe['town'], controller.offset)
                        assert 0 < len(controller.gestures) <= recipe['max_steps'], controller.gestures
                        assert context.run_recognition(recipe['target'], controller.screencap()).hit
                        checks.append((recipe['town'], controller.offset, len(controller.gestures)))
                    return True
                except Exception as error:
                    import traceback
                    errors.append(traceback.format_exc())
                    return False
        logs = ROOT / '.runtime-cache/native-scroll'; logs.mkdir(parents=True, exist_ok=True)
        Tasker.set_log_dir(logs)
        resource, tasker = Resource(), Tasker()
        resource.register_custom_recognition('battle_no_revive', NoRevivePrompt())
        self.assertTrue(controller.post_connection().wait().succeeded)
        controller.set_screenshot_use_raw_size(True)
        self.assertTrue(resource.post_bundle(ROOT / 'assets/resource/base').wait().succeeded)
        resource.register_custom_action('__scroll_inspect', Inspect())
        resource.override_pipeline({'__ScrollInspect': dict(action=dict(type='Custom', param=dict(custom_action='__scroll_inspect')))})
        self.assertTrue(tasker.bind(resource, controller))
        self.assertTrue(tasker.post_task('__ScrollInspect').wait().succeeded, '\n'.join(errors))
        self.assertEqual(errors, [])
        self.assertEqual(len(checks), len(recipes), checks)
        print('SCROLL', checks)


if __name__ == '__main__': unittest.main()
