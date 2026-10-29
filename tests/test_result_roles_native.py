"""Complete offline SWF result screens; a memory controller rejects all input."""
import json
import os
from pathlib import Path
import sys
import unittest

ROOT=Path(__file__).resolve().parents[1]
FIXTURES=ROOT/'tests/fixtures/navigation/result-roles'


@unittest.skipUnless(os.environ.get('MAA_RESULT_UI_TESTS')=='1','opt-in memory-only result recognition')
class ResultRoleNativeTests(unittest.TestCase):
    def test_complete_character_screens_and_return_buttons(self):
        from maa.controller import CustomController
        from maa.custom_action import CustomAction
        from maa.resource import Resource
        from maa.tasker import Tasker
        from fixture_pixels import read_png_bgr
        sys.path.insert(0,str(ROOT/'agent'))
        from battle_ui import NoRevivePrompt
        cases=json.loads((FIXTURES/'cases.json').read_text(encoding='utf8'))['cases']
        checked=[];errors=[]
        class Pixels(CustomController):
            def connect(self):return True
            def request_uuid(self):return 'result-role-memory-only'
            def screencap(self):return read_png_bgr(FIXTURES/cases[0]['file'])
            def forbidden(self,*args):raise AssertionError('No desktop input allowed')
            click=swipe=touch_down=touch_up=touch_move=key_down=key_up=input_text=start_app=stop_app=forbidden
        class Inspect(CustomAction):
            def run(self,context,argv):
                for case in cases:
                    frame=read_png_bgr(FIXTURES/case['file'])
                    for outcome in ('Failed','Succeeded'):
                        expected=case['outcome']==outcome.lower()
                        for suffix in ('','Return'):
                            name='Result_'+outcome+suffix
                            actual=context.run_recognition(name,frame)
                            if actual is None or bool(actual.hit)!=expected:
                                errors.append((case['file'],name,expected,None if actual is None else actual.hit))
                    checked.append(case['file'])
                return True
        log=Path(os.environ.get('MAA_RESULT_TEST_LOG',str(ROOT/'.runtime-cache/result-role-native')))
        log.mkdir(parents=True,exist_ok=True);Tasker.set_log_dir(log)
        resource,controller,tasker=Resource(),Pixels(),Tasker()
        self.assertTrue(controller.post_connection().wait().succeeded)
        controller.set_screenshot_use_raw_size(True)
        self.assertTrue(resource.post_bundle(ROOT/'assets/resource/base').wait().succeeded)
        resource.register_custom_recognition('battle_no_revive',NoRevivePrompt())
        resource.register_custom_action('__inspect_result_roles',Inspect())
        resource.override_pipeline({'__ResultRoles':{'action':{'type':'Custom','param':{'custom_action':'__inspect_result_roles'}}}})
        self.assertTrue(tasker.bind(resource,controller))
        self.assertTrue(tasker.post_task('__ResultRoles').wait().succeeded)
        self.assertEqual(len(checked),42)
        self.assertEqual(errors,[])


if __name__=='__main__':unittest.main()
