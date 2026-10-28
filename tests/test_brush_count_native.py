"""Native unlimited max_hit and cancellation; never opens or controls a window."""
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest


@unittest.skipUnless(os.environ.get('BRUSH_COUNT_NATIVE_TESTS') == '1', 'opt-in Maa loop test')
class NativeBrushCountTests(unittest.TestCase):
    def test_finite_count_and_unlimited_until_stop(self):
        import numpy as np
        from maa.controller import CustomController
        from maa.custom_action import CustomAction
        from maa.resource import Resource
        from maa.tasker import Tasker

        class Memory(CustomController):
            def connect(self): return True
            def request_uuid(self): return 'brush-count-no-input'
            def screencap(self): return np.zeros((720, 1148, 3), dtype=np.uint8)
            def forbidden(self, *args): raise AssertionError('Unexpected device input')
            click = swipe = touch_down = touch_move = touch_up = key_down = key_up = input_text = forbidden

        counts, reached = [], threading.Event()
        class Count(CustomAction):
            def run(self, context, argv):
                counts.append(1)
                if len(counts) >= 8: reached.set()
                time.sleep(.01)
                return True

        with tempfile.TemporaryDirectory(prefix='brush-count-') as folder:
            root = Path(folder)
            (root/'pipeline').mkdir()
            pipeline = {
                'Start': {'next': ['Loop', 'Done'], 'post_delay': 0},
                'Loop': {'max_hit': 3, 'action': {'type': 'Custom', 'param': {'custom_action': 'count'}},
                         'next': ['Loop', 'Done'], 'post_delay': 0},
                'Done': {'post_delay': 0},
            }
            (root/'pipeline/main.json').write_text(json.dumps(pipeline), encoding='utf8')
            log_dir = Path(__file__).resolve().parents[1]/'.runtime-cache/native-brush-count'
            log_dir.mkdir(parents=True, exist_ok=True)
            Tasker.set_log_dir(log_dir)
            controller = Memory()
            self.assertTrue(controller.post_connection().wait().succeeded)
            resource = Resource()
            self.assertTrue(resource.post_bundle(root).wait().succeeded)
            resource.register_custom_action('count', Count())
            tasker = Tasker()
            self.assertTrue(tasker.bind(resource, controller))
            self.assertTrue(tasker.post_task('Start').wait().succeeded)
            self.assertEqual(len(counts), 3)
            counts.clear()
            job = tasker.post_task('Start', {'Loop': {'max_hit': 4294967295}})
            try:
                self.assertTrue(reached.wait(5), 'Unlimited loop stopped prematurely')
                self.assertFalse(job.status.done)
            finally:
                tasker.post_stop().wait()
            job.wait()
            final_count = len(counts)
            time.sleep(.05)
            self.assertEqual(len(counts), final_count)
            self.assertGreaterEqual(final_count, 8)
