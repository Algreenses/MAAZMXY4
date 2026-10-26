"""Fake-controller playback checks: never load Maa native libraries or send input."""
from contextlib import redirect_stdout
import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def load_player():
    modules = {name: ModuleType(name) for name in
               ("maa", "maa.agent", "maa.agent.agent_server", "maa.custom_action", "toast")}
    class AgentServer:
        @staticmethod
        def custom_action(_name):
            return lambda implementation: implementation
    modules["maa.agent.agent_server"].AgentServer = AgentServer
    modules["maa.custom_action"].CustomAction = object
    modules["toast"].toast = lambda *args, **kwargs: None
    spec = importlib.util.spec_from_file_location("isolated_battle_player", ROOT/"agent/battle_player.py")
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(ROOT/"agent"))
    try:
        with patch.dict(sys.modules, modules):
            spec.loader.exec_module(module)
    finally:
        sys.path.remove(str(ROOT/"agent"))
    return module


player = load_player()


class FakeJob:
    def __init__(self, succeeded=True):
        self.succeeded = succeeded
    def wait(self):
        return self


class FakeController:
    def __init__(self):
        self.calls = []
        self.fail = set()
        self.fail_status = set()
        self.after_call = None
    def __getattr__(self, name):
        if not name.startswith("post_"):
            raise AttributeError(name)
        def invoke(*args, **kwargs):
            self.calls.append((name,args,kwargs))
            if self.after_call:
                self.after_call(name,args,kwargs)
            if name in self.fail:
                raise RuntimeError("fake failure")
            return FakeJob(name not in self.fail_status)
        return invoke


class BattlePlayerStudioTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)
        self.controller = FakeController()
        self.tasker = SimpleNamespace(controller=self.controller,stopping=False)
        self.context = SimpleNamespace(tasker=self.tasker)
        self.logs, self.waits, self.notifications = [], [], []
        self.clock = 100.0
        self.patches = [
            patch.object(player,"RECORDING_DIR",str(self.directory)),
            patch.object(player,"_toast",lambda *args,**kwargs: self.notifications.append(args)),
            patch.object(player.BattlePlay,"_log",lambda _self,message: self.logs.append(message)),
            patch.object(player.time,"monotonic",lambda: self.clock),
            patch.object(player,"_sleep_until",self.wait_until)]
        for mocked in self.patches: mocked.start()
    def tearDown(self):
        for mocked in reversed(self.patches): mocked.stop()
        self.temp.cleanup()
    def wait_until(self, target, stopping=None):
        self.waits.append(target)
        if stopping and stopping(): return False
        self.clock = target
        return True
    def write(self, actions, *, name="战斗", filename="record.json", **extra):
        data=dict(version=1,name=name,base=[1147,720],actions=actions)
        data.update(extra)
        (self.directory/filename).write_text(json.dumps(data,ensure_ascii=False),encoding="utf8")
        return data
    def run_recording(self, file="record.json", **params):
        return player.BattlePlay().run(self.context,SimpleNamespace(custom_action_param=dict(file=file,**params)))

    def test_legacy_actions_keep_order_leading_wait_and_absolute_schedule(self):
        self.write([dict(t=100,op="key",key=74),dict(t=20,op="click",x=10,y=20),
                    dict(t=30,op="text",text="中文"),dict(t=40,op="scroll",dx=-1,dy=2)])
        self.assertTrue(self.run_recording(speed=2))
        self.assertEqual([c[0] for c in self.controller.calls],
                         ["post_click_key","post_click","post_input_text","post_scroll"])
        self.assertAlmostEqual(self.waits[0],100.05)
        self.assertAlmostEqual(self.waits[-1],100.095)

    def test_right_middle_buttons_and_moves_use_contact_numbers(self):
        self.write([dict(t=0,op="down",button="right",x=10,y=20),
                    dict(t=10,op="down",button="middle",x=10,y=20),
                    dict(t=10,op="move",x=20,y=30),dict(t=10,op="up",button="right"),
                    dict(t=10,op="up",button="middle")])
        self.assertTrue(self.run_recording())
        self.assertEqual([c[2]["contact"] for c in self.controller.calls],[1,2,1,2,1,2])

    def test_mouse_release_retains_new_capture_final_drag_endpoint(self):
        self.write([dict(t=0,op="down",button="right",x=10,y=20),
                    dict(t=10,op="up",button="right",x=30,y=40)])
        self.assertTrue(self.run_recording())
        self.assertEqual(self.controller.calls[1],("post_touch_move",(30,40),{"contact":1}))
        self.assertEqual(self.controller.calls[2],("post_touch_up",(),{"contact":1}))

    def test_legacy_standalone_move_clears_implicit_left_button_flag_on_exit(self):
        self.write([dict(t=0,op="move",x=10,y=20)])
        self.assertTrue(self.run_recording())
        self.assertEqual(self.controller.calls[-1],("post_touch_up",(),{"contact":0}))

    def test_duration_preserves_trailing_empty_interval_and_speed(self):
        self.write([dict(t=100,op="key",key=74)],duration_ms=1000)
        self.assertTrue(self.run_recording(speed=2))
        self.assertAlmostEqual(self.waits[-1],100.5)
        self.assertEqual(len(self.controller.calls),1)

    def test_stop_after_keydown_releases_held_key_without_later_action(self):
        self.write([dict(t=0,op="keydown",key=74),dict(t=100000,op="keyup",key=74)])
        self.controller.after_call=lambda *_: setattr(self.tasker,"stopping",True)
        self.assertFalse(self.run_recording())
        self.assertEqual([c[0] for c in self.controller.calls],["post_key_down","post_key_up"])

    def test_stop_during_wait_does_not_dispatch_due_action(self):
        self.write([dict(t=100000,op="key",key=74)])
        def interrupted_wait(target, stopping=None):
            self.tasker.stopping=True
            return True  # Cancellation arriving after a wait also needs checking.
        with patch.object(player,"_sleep_until",interrupted_wait):
            self.assertFalse(self.run_recording())
        self.assertEqual(self.controller.calls,[])

    def test_normal_completion_of_legacy_missing_releases_warns_and_cleans(self):
        self.write([dict(t=10,op="keydown",key=74),dict(t=10,op="down",button="middle",x=5,y=6)])
        with redirect_stdout(io.StringIO()):
            self.assertTrue(self.run_recording())
        self.assertTrue(any("配对" in message for args in self.notifications for message in args))
        self.assertEqual(self.controller.calls[-2:],[("post_key_up",(74,),{}),
                                                    ("post_touch_up",(),{"contact":2})])

    def test_failed_press_still_releases_other_keys_and_buttons(self):
        self.write([dict(t=0,op="keydown",key=74),dict(t=1,op="down",button="right",x=1,y=2),
                    dict(t=1,op="keyup",key=74),dict(t=1,op="up",button="right")])
        self.controller.fail.add("post_touch_down")
        self.assertFalse(self.run_recording())
        self.assertEqual(self.controller.calls[-2:],[("post_key_up",(74,),{}),
                                                    ("post_touch_up",(),{"contact":1})])

    def test_failed_release_does_not_skip_other_cleanup(self):
        self.write([dict(t=0,op="keydown",key=74),dict(t=1,op="down",x=1,y=2)])
        self.controller.fail.add("post_key_up")
        with redirect_stdout(io.StringIO()):
            self.assertFalse(self.run_recording())
        self.assertEqual(self.controller.calls[-1],("post_touch_up",(),{"contact":0}))
        self.assertTrue(any("清理未完成" in message for message in self.logs))

    def test_failed_job_status_is_reported_and_cleanup_attempted(self):
        self.write([dict(t=0,op="keydown",key=74),dict(t=1,op="keyup",key=74)])
        self.controller.fail_status.add("post_key_down")
        self.assertFalse(self.run_recording())
        self.assertEqual(self.controller.calls[-1],("post_key_up",(74,),{}))

    def test_invalid_numbers_and_coordinate_scroll_fail_before_any_input(self):
        cases=[dict(actions=[dict(t=-1,op="key",key=74)]),
               dict(actions=[dict(t=0,op="key",key=True)]),
               dict(actions=[dict(t=0,op="click",x=-1,y=0)]),
               dict(actions=[dict(t=0,op="key",key=74)],duration_ms=float("nan")),
               dict(actions=[dict(t=10,op="key",key=74)],duration_ms=5),
               dict(actions=[dict(t=0,op="scroll",dx=0,dy=1,x=1,y=2)]),
               dict(actions=[dict(t=0,op="未释放")])]
        for case in cases:
            with self.subTest(case=case):
                self.write(**case)
                self.assertFalse(self.run_recording())
                self.assertEqual(self.controller.calls,[])

    def test_stop_just_before_dispatch_does_not_send_a_provisional_press_or_release(self):
        self.write([dict(t=0,op="keydown",key=74),dict(t=10,op="keyup",key=74)])
        class StoppingTasker:
            controller=self.controller
            reads=0
            @property
            def stopping(self):
                self.reads+=1
                return self.reads>=4
        self.context.tasker=StoppingTasker()
        self.assertFalse(self.run_recording())
        self.assertEqual(self.controller.calls,[])

    def test_invalid_speed_fails_without_input(self):
        self.write([dict(t=0,op="key",key=74)])
        for speed in (float("nan"),float("inf"),-1,0,True,"invalid",{},None):
            with self.subTest(speed=speed):
                self.assertFalse(self.run_recording(speed=speed))
                self.assertEqual(self.controller.calls,[])

    def test_live_selected_resolution_does_not_require_client_restart(self):
        self.write([dict(t=0,op="key",key=74)],filename="one.json")
        self.write([dict(t=0,op="key",key=75)],filename="two.json")
        selection=self.directory/".selected.json"
        for filename,key in (("one.json",74),("two.json",75)):
            selection.write_text(json.dumps(dict(file=filename)),encoding="utf8")
            self.assertTrue(self.run_recording(file="@selected"))
            self.assertEqual(self.controller.calls[-1],("post_click_key",(key,),{}))

    def test_unselected_falls_back_to_latest_but_stale_selection_never_does(self):
        self.write([dict(t=0,op="key",key=74)])
        self.assertTrue(self.run_recording(file="@selected"))
        self.controller.calls.clear()
        (self.directory/".selected.json").write_text(json.dumps(dict(file="missing.json")),encoding="utf8")
        self.assertFalse(self.run_recording(file="@selected"))
        self.assertEqual(self.controller.calls,[])
        self.assertTrue(any("已不存在" in message for message in self.logs))

    def test_malformed_and_traversal_selection_explicitly_fail(self):
        self.write([dict(t=0,op="key",key=74)])
        for selected in ("broken",json.dumps([]),json.dumps(dict(file="../outside.json")),
                         json.dumps(dict(file=5))):
            with self.subTest(selected=selected):
                (self.directory/".selected.json").write_text(selected,encoding="utf8")
                self.assertFalse(self.run_recording(file="@selected"))
                self.assertEqual(self.controller.calls,[])

    def test_hidden_bookkeeping_files_never_appear_as_recordings(self):
        self.write([dict(t=0,op="key",key=74)])
        (self.directory/".selected.json").write_text('{}',encoding="utf8")
        self.assertEqual(player.list_recordings(),["record"])
        self.assertEqual(player.newest_recording(),"record")

    def test_interface_selected_case_is_first_default_and_covers_all_replays(self):
        interface=json.loads((ROOT/"assets/interface.json").read_text(encoding="utf8"))
        option=interface["option"]["回放文件"]
        self.assertEqual(option["default_case"],"（脚本工具所选）")
        first=option["cases"][0]
        self.assertEqual(first["name"],option["default_case"])
        latest=next(case for case in option["cases"] if case["name"]=="（最新录制）")
        self.assertEqual(set(first["pipeline_override"]),set(latest["pipeline_override"]))
        self.assertEqual(len(first["pipeline_override"]),11)
        for node in first["pipeline_override"].values():
            self.assertEqual(node["action"]["param"]["custom_action_param"]["file"],"@selected")

    def test_timestamp_wait_checks_stop_instead_of_sleeping_entire_gap(self):
        # Unpatch only the timing helper; all OS timer and clock access stays fake.
        elapsed=[0.0]; sleeps=[]
        def fake_sleep(seconds):
            sleeps.append(seconds); elapsed[0]+=seconds
        def monotonic():
            elapsed[0]+=.0001
            return elapsed[0]
        original_wait = next(p for p in self.patches if p.attribute=="_sleep_until").temp_original
        with patch.object(player,"_boost_timer",lambda:None), \
             patch.object(player.time,"monotonic",monotonic),patch.object(player.time,"sleep",fake_sleep):
            self.assertFalse(original_wait(999,lambda:elapsed[0]>=.05))
        self.assertTrue(sleeps)
        self.assertLessEqual(max(sleeps),.02)
        self.assertLess(elapsed[0],.08)


if __name__ == "__main__": unittest.main()
