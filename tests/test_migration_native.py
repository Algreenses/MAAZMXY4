"""Opt-in Maa compatibility check using synthetic pixels and no input device."""
import json
import os
from pathlib import Path
import struct
import tempfile
import unittest
import zlib


@unittest.skipUnless(os.environ.get("ZAOMENG_NATIVE_TESTS") == "1", "opt-in native SDK test")
class NativeMigrationTests(unittest.TestCase):
    def test_raw_frame_absolute_template_and_composite_recognition(self):
        root = Path(__file__).resolve().parents[1]
        os.environ.setdefault("MAAFW_BINARY_PATH", str(root / "runtimes/win-x64/native"))
        import numpy as np
        from maa.controller import CustomController
        from maa.custom_action import CustomAction
        from maa.resource import Resource
        from maa.tasker import Tasker

        pixels = np.random.default_rng(27).integers(0, 256, (737, 1175, 3), dtype=np.uint8)

        class PixelsOnly(CustomController):
            def connect(self): return True
            def request_uuid(self): return "synthetic-migration-compatibility"
            def screencap(self): return pixels.copy()
            def forbidden(self, *args): raise AssertionError("Input is forbidden in this test")
            start_app = stop_app = click = swipe = touch_down = touch_move = touch_up = forbidden
            click_key = input_text = key_down = key_up = forbidden

        observed = {}

        class Inspect(CustomAction):
            def run(self, context, argv):
                for name, frame in (("both", pixels), ("either", pixels), ("both", np.zeros_like(pixels))):
                    result = context.run_recognition(name, frame)
                    observed[(name, bool(frame.any()))] = bool(result and result.hit)
                return True

        def png(path, bgr):
            rgb = bgr[:, :, ::-1]
            def chunk(kind, data):
                return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
            height, width = rgb.shape[:2]
            data = b"".join(b"\0" + row.tobytes() for row in rgb)
            path.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
                             + chunk(b"IDAT", zlib.compress(data)) + chunk(b"IEND", b""))

        with tempfile.TemporaryDirectory(prefix="迁移兼容-") as directory:
            temp = Path(directory)
            log_dir = root / ".runtime-cache/native-migration-test"
            log_dir.mkdir(parents=True, exist_ok=True)
            Tasker.set_log_dir(log_dir)
            template = temp / "模板.png"
            png(template, pixels[20:44, 30:62])
            recognition = {"type": "TemplateMatch", "param": {
                "template": str(template), "roi": [25, 15, 50, 40], "threshold": .99}}
            pipeline = {
                "both": {"recognition": {"type": "And", "param": {"all_of": [
                    {"recognition": recognition}, {"recognition": recognition}]}}},
                "either": {"recognition": {"type": "Or", "param": {"any_of": [
                    {"recognition": recognition}, {"recognition": {"type": "DirectHit"}}]}}},
                "inspect": {"action": {"type": "Custom", "param": {"custom_action": "inspect"}}},
            }
            (temp / "pipeline").mkdir()
            (temp / "pipeline/test.json").write_text(json.dumps(pipeline), encoding="utf-8")
            controller, resource, tasker = PixelsOnly(), Resource(), Tasker()
            self.assertTrue(controller.post_connection().wait().succeeded)
            self.assertTrue(controller.set_screenshot_use_raw_size(True))
            self.assertEqual(controller.post_screencap().wait().get().shape, (737, 1175, 3))
            self.assertTrue(resource.post_bundle(temp).wait().succeeded)
            self.assertTrue(resource.register_custom_action("inspect", Inspect()))
            self.assertTrue(tasker.bind(resource, controller))
            self.assertTrue(tasker.post_task("inspect").wait().succeeded)
            self.assertEqual(observed, {("both", True): True, ("either", True): True, ("both", False): False})


if __name__ == "__main__":
    unittest.main()
