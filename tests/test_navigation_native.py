"""Opt-in Maa tests on recorded pixels and a memory-only input controller."""
import json
import os
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(os.environ.get('NAVIGATION_NATIVE_TESTS') == '1', 'opt-in Maa memory-controller test')
class NativeNavigationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import sys
        sys.path.insert(0, str(ROOT/'agent'))
        os.environ.setdefault('MAAFW_BINARY_PATH', str(ROOT/'runtimes/win-x64/native'))
        from maa.tasker import Tasker
        cls.log = ROOT/'.runtime-cache/native-navigation'
        cls.log.mkdir(parents=True, exist_ok=True)
        Tasker.set_log_dir(cls.log)

    def test_stage_overlays_world_and_legacy_pause_use_guarded_real_pixels(self):
        from maa.controller import CustomController
        from maa.custom_action import CustomAction
        from maa.resource import Resource
        from maa.tasker import Tasker
        from fixture_pixels import read_png_bgr
        from navigation import NavigationError
        from navigation.catalog import task_graph
        from navigation.runtime import GraphNavigator
        frames = {name:read_png_bgr(ROOT/'tests/fixtures/navigation'/(name+'.png'))
                  for name in ('profile','quest-taizong-real','map-a','map-b','world','daily-expanded')}
        frames['paused-map'] = frames['map-a'].copy()
        label = read_png_bgr(ROOT/'assets/resource/base/image/bounty-candle/pause.png')
        h,w = label.shape[:2]
        # This pause-map case is an explicit composite; the other frames are
        # source screenshots. It does not replace real foreground testing.
        frames['paused-map'][342:342+h,381:381+w] = label
        events, checks = [], []
        stage_parameters = dict(navigation=dict(kind='stage',label='朱电（琉璃宫）',
            target_node='朱电（琉璃宫）-关卡入口'))

        class Pixels(CustomController):
            frame = 'profile'
            world_case = False
            def connect(self): return True
            def request_uuid(self): return 'stage-overlay-memory-only'
            def screencap(self): return frames[self.frame].copy()
            def touch_down(self,contact,x,y,pressure):
                events.append((self.frame,x,y)); return True
            def touch_up(self,contact):
                if self.world_case:
                    self.frame='map-a' if self.frame=='world' else 'quest-taizong-real'
                else:
                    self.frame='map-a'
                return True
            def touch_move(self,*args): return True
            def forbidden(self,*args): raise AssertionError('Unexpected device input')
            click=swipe=key_down=key_up=input_text=start_app=stop_app=forbidden
        controller = Pixels()

        class Navigate(CustomAction):
            def run(self,context,argv):
                if controller.world_case:
                    graph = task_graph(context,dict(navigation=dict(kind='bounty',title='守卫太宗',
                        target_node='悬赏（守卫太宗）-点击前往副本')))
                    actual = GraphNavigator(context,graph,capture=controller.screencap,check=lambda:None).run()
                    assert actual=='task_destination' and [item[0] for item in events]==['world','map-a'],events
                    assert 520<=events[0][1]<=630 and 275<=events[0][2]<=360,events[0]
                    checks.append(dict(case='world to accepted bounty',state=actual,clicks=len(events)))
                    return True
                if controller.frame == 'map-b':
                    graph = task_graph(context,stage_parameters)
                    try:
                        GraphNavigator(context,graph,capture=controller.screencap,check=lambda:None).run()
                    except NavigationError as error:
                        assert error.reason=='unreachable' and not events
                        checks.append(dict(case='wrong map',reason=error.reason,clicks=0))
                        return True
                    raise AssertionError('Wrong stage map must not succeed')
                graph = task_graph(context,stage_parameters)
                actual = GraphNavigator(context,graph,capture=controller.screencap,check=lambda:None).run()
                assert actual=='task_destination' and len(events)==1,events
                checks.append(dict(case=events[0][0],state=actual,clicks=len(events)))
                return True

        class Inspect(CustomAction):
            def run(self,context,argv):
                for name in ('world','profile','quest-taizong-real','map-a','map-b','daily-expanded'):
                    home=context.run_recognition('Nav_Probe_home_entry',frames[name])
                    close=context.run_recognition('Nav_CloseQuest',frames[name])
                    assert bool(home and home.hit)==(name=='world'),name
                    assert bool(close and close.hit)==(name=='quest-taizong-real'),name
                pause=context.run_recognition('Nav_Probe_paused_overlay',frames['daily-expanded'])
                assert pause and pause.hit,'Legacy pause caption was missed'
                state=GraphNavigator(context,task_graph(context,{}),capture=lambda:frames['daily-expanded'],
                    check=lambda:None,log=lambda _:None).locate()
                assert state in ('daily_expanded','paused_task_map'),state
                checks.append(dict(case='legacy daily pause',state=state))
                return True

        resource,tasker=Resource(),Tasker()
        self.assertTrue(controller.post_connection().wait().succeeded)
        controller.set_screenshot_use_raw_size(True)
        self.assertTrue(resource.post_bundle(ROOT/'assets/resource/base').wait().succeeded)
        resource.register_custom_action('__stage_nav',Navigate())
        resource.register_custom_action('__extended_inspect',Inspect())
        resource.override_pipeline({
            '__StageNav':{'action':{'type':'Custom','param':{'custom_action':'__stage_nav'}}},
            '__ExtendedInspect':{'action':{'type':'Custom','param':{'custom_action':'__extended_inspect'}}},
        })
        self.assertTrue(tasker.bind(resource,controller))
        for name in ('profile','quest-taizong-real','paused-map','map-b'):
            with self.subTest(frame=name):
                controller.frame=name; events.clear()
                self.assertTrue(tasker.post_task('__StageNav').wait().succeeded)
        controller.frame='world'; controller.world_case=True; events.clear()
        self.assertTrue(tasker.post_task('__StageNav').wait().succeeded)
        self.assertTrue(tasker.post_task('__ExtendedInspect').wait().succeeded)
        (self.log/'extended-navigation-checks.json').write_text(json.dumps(checks,ensure_ascii=False,indent=2),encoding='utf8')

    def test_known_paused_map_resumes_before_opening_the_quest_panel(self):
        from maa.controller import CustomController
        from maa.custom_action import CustomAction
        from maa.resource import Resource
        from maa.tasker import Tasker
        from fixture_pixels import read_png_bgr
        from navigation.catalog import task_graph
        from navigation.runtime import GraphNavigator
        normal=read_png_bgr(ROOT/'tests/fixtures/bounty-candle/quest-map.png')
        paused=normal.copy()
        label=read_png_bgr(ROOT/'assets/resource/base/image/bounty-candle/pause.png')
        h,w=label.shape[:2]
        paused[342:342+h,381:381+w]=label
        detail=read_png_bgr(ROOT/'tests/fixtures/navigation/quest-taizong-real.png')
        events=[]
        class Pixels(CustomController):
            stage=0
            def connect(self):return True
            def request_uuid(self):return 'paused-map-memory'
            def screencap(self):return [paused,normal,detail][self.stage].copy()
            def touch_down(self,contact,x,y,pressure):
                events.append((self.stage,x,y))
                return True
            def touch_up(self,contact):
                self.stage+=1
                return True
            def touch_move(self,*args):return True
            def forbidden(self,*args):raise AssertionError('Unexpected input')
            click=swipe=key_down=key_up=input_text=start_app=stop_app=forbidden
        controller=Pixels()
        class Navigate(CustomAction):
            def run(self,context,argv):
                graph=task_graph(context,dict(navigation=dict(kind='bounty',title='守卫太宗',target_node='悬赏（守卫太宗）-点击前往副本')))
                nav=GraphNavigator(context,graph,capture=lambda:controller.screencap(),check=lambda:None)
                return nav.run()=='task_destination'
        resource,tasker=Resource(),Tasker()
        self.assertTrue(controller.post_connection().wait().succeeded)
        controller.set_screenshot_use_raw_size(True)
        self.assertTrue(resource.post_bundle(ROOT/'assets/resource/base').wait().succeeded)
        resource.register_custom_action('__paused_nav',Navigate())
        resource.override_pipeline({'__PausedNav':{'action':{'type':'Custom','param':{'custom_action':'__paused_nav'}}}})
        self.assertTrue(tasker.bind(resource,controller))
        self.assertTrue(tasker.post_task('__PausedNav').wait().succeeded)
        self.assertEqual([event[0] for event in events],[0,1])
        self.assertEqual(controller.stage,2)

    def test_real_templates_and_navigation_identify_one_screen_without_input(self):
        from maa.controller import CustomController
        from maa.custom_action import CustomAction
        from maa.resource import Resource
        from maa.tasker import Tasker
        from fixture_pixels import read_png_bgr
        from navigation.catalog import task_graph
        from navigation.runtime import GraphNavigator

        frames = {path.stem:read_png_bgr(path) for path in (ROOT/'tests/fixtures/navigation').glob('*.png')}
        checks = []
        class PixelsOnly(CustomController):
            def connect(self): return True
            def request_uuid(self): return 'navigation-memory-readonly'
            def screencap(self): return frames['task-map-real'].copy()
            def forbidden(self, *args): raise AssertionError('Desktop input is forbidden')
            touch_down = touch_move = touch_up = click = swipe = start_app = stop_app = forbidden
            key_down = key_up = click_key = input_text = forbidden

        class Inspect(CustomAction):
            def run(self, context, argv):
                for frame_name in ('challenge-failed', 'challenge-succeeded', 'task-map-real', 'quest-taizong-real'):
                    for suffix in ('Failed', 'Succeeded', 'FailedReturn', 'SucceededReturn'):
                        result = context.run_recognition('Result_'+suffix, frames[frame_name])
                        expected = frame_name == ('challenge-failed' if suffix.startswith('Failed') else 'challenge-succeeded')
                        checks.append(dict(check=frame_name+':'+suffix, actual=bool(result and result.hit), expected=expected))
                # The button alone must never authorize a result-screen click.
                for kind, suffix in [('failed','Failed'),('succeeded','Succeeded')]:
                    pixels = frames['challenge-'+kind].copy()
                    pixels[120:500,200:980] = 0
                    result = context.run_recognition('Result_'+suffix+'Return', pixels)
                    checks.append(dict(check=kind+':button without title', actual=bool(result and result.hit), expected=False))
                graph = task_graph(context, {})
                for name, expected in [('task-map-real','map_b'), ('map-a','map_a'), ('map-b','map_b'),
                    ('world','world'), ('activity','activity'), ('profile','profile'), ('profile-sell','profile_sell'),
                    ('warehouse','warehouse'), ('treasure','treasure'), ('settings-menu','settings_menu'),
                    ('alliance','alliance'), ('alliance-contribution','alliance_contribution'),
                    ('survival-week','survival_week'), ('survival-rewards','survival_rewards'),
                    ('bounty','bounty_board')]:
                    try:
                        actual = GraphNavigator(context, graph, capture=lambda:frames[name], check=lambda:None,
                            wait=lambda _seconds:None, log=lambda _line:None).locate()
                    except Exception as error:
                        actual = str(error)
                    checks.append(dict(check='locate:'+name, actual=actual, expected=expected))
                graph = task_graph(context, dict(navigation=dict(kind='bounty', title='守卫太宗',
                    target_node='悬赏（守卫太宗）-点击前往副本')))
                actual = GraphNavigator(context,graph,capture=lambda:frames['quest-taizong-real'],
                    check=lambda:None,wait=lambda _seconds:None,log=lambda _line:None).locate()
                checks.append(dict(check='accepted real Taizong detail', actual=actual, expected='task_destination'))
                return True

        controller, resource, tasker = PixelsOnly(), Resource(), Tasker()
        self.assertTrue(controller.post_connection().wait().succeeded)
        controller.set_screenshot_use_raw_size(True)
        self.assertTrue(resource.post_bundle(ROOT/'assets/resource/base').wait().succeeded)
        resource.register_custom_action('__inspect_navigation', Inspect())
        resource.override_pipeline({'__InspectNavigation':{'action':{'type':'Custom','param':{'custom_action':'__inspect_navigation'}}}})
        self.assertTrue(tasker.bind(resource,controller))
        self.assertTrue(tasker.post_task('__InspectNavigation').wait().succeeded)
        (self.log/'recognition-checks.json').write_text(json.dumps(checks,ensure_ascii=False,indent=2),encoding='utf8')
        self.assertEqual([item for item in checks if item['actual'] != item['expected']], [])

    def test_failed_attempt_returns_retries_and_does_not_consume_two_round_quota(self):
        import importlib.util
        import sys
        import tempfile
        from types import ModuleType
        from unittest.mock import patch
        from maa.controller import CustomController
        from maa.custom_action import CustomAction
        from maa.resource import Resource
        from maa.tasker import Tasker
        from fixture_pixels import read_png_bgr

        registration = ModuleType('maa.agent.agent_server')
        registration.AgentServer = type('RegistrationOnly', (), {'custom_action':staticmethod(lambda _name:lambda cls:cls)})
        def load(name, filename):
            spec=importlib.util.spec_from_file_location(name, ROOT/'agent'/filename)
            module=importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            return module
        with patch.dict(sys.modules, {'maa.agent':ModuleType('maa.agent'),'maa.agent.agent_server':registration}):
            state = load('navigation_native_state', 'script_task.py')
            player = load('navigation_native_player', 'battle_player.py')
        names = dict(map='task-map-real', detail='quest-taizong-real', failed='challenge-failed', won='challenge-succeeded')
        frames = {key:read_png_bgr(ROOT/'tests/fixtures/navigation'/(value+'.png')) for key,value in names.items()}
        frames['battle'] = read_png_bgr(ROOT/'tests/fixtures/bounty-candle/candle-battle-ready.png')[:,:1147].copy()
        events, quotas = [], []
        class Game(CustomController):
            stage, attempts, successes, failed_returns = 'detail', 0, 0, 0
            def connect(self): return True
            def request_uuid(self): return 'navigation-failure-quota-memory'
            def screencap(self): return frames[self.stage].copy()
            def touch_down(self, contact,x,y,pressure):
                events.append(('down',self.stage,x,y))
                self.last_down=(x,y)
                return True
            def touch_move(self, *args): return True
            def touch_up(self, contact):
                events.append(('up',self.stage))
                if self.stage == 'map': self.stage='detail'
                elif self.stage == 'detail':
                    self.stage='battle'
                    self.attempts+=1
                elif self.stage in ('failed','won'):
                    x,y=self.last_down
                    if self.stage=='failed' and 600<=x<=780 and 490<=y<=550:
                        self.failed_returns+=1
                        self.stage='map'
                    elif self.stage=='won' and 810<=x<=1020 and 530<=y<=595:
                        self.successes+=1
                        self.stage='map'
                elif self.stage!='battle': raise AssertionError('Unexpected mouse input')
                return True
            def key_down(self, key):
                events.append(('key_down',self.stage,key))
                if self.attempts == 1: self.stage='failed'
                return True
            def key_up(self, key):
                events.append(('key_up',self.stage,key))
                if self.stage == 'battle': self.stage='won'
                return True
            def forbidden(self,*args): raise AssertionError('Unexpected controller API')
            click = swipe = click_key = input_text = start_app = stop_app = forbidden
        class Count(CustomAction):
            def run(self,context,argv):
                quotas.append(len(events))
                return True

        with tempfile.TemporaryDirectory(prefix='nav-memory-') as temporary:
            directory=Path(temporary).resolve()
            library=directory/'recordings'
            library.mkdir()
            (library/'memory.json').write_text(json.dumps(dict(version=1,name='Memory only',base=[1147,720],duration_ms=1600,
                actions=[dict(t=0,op='down',button='left',x=650,y=520),dict(t=50,op='keydown',key=65),
                         dict(t=800,op='keyup',key=65),dict(t=0,op='up',button='left',x=650,y=520)])),encoding='utf8')
            from script_library import bind_task_recording
            from task_catalog import task_key
            task=json.loads((ROOT/'assets/interface.json').read_text(encoding='utf8'))['task']
            task=next(t for t in task if t['name']=='悬赏（守卫太宗）')
            import hashlib
            bind_task_recording(library, task_key(task['name'],task['entry']), 'memory.json',
                expected_revision=hashlib.sha256((library/'memory.json').read_bytes()).hexdigest())
            state.ROOT, state.RECORDINGS, state.STATUS_DIRECTORY=directory,library,directory/'debug/task-runs'
            player.RECORDING_DIR=str(library)
            player._toast=lambda *_args:None
            controller, resource, tasker=Game(),Resource(),Tasker()
            self.assertTrue(controller.post_connection().wait().succeeded)
            controller.set_screenshot_use_raw_size(True)
            self.assertTrue(resource.post_bundle(ROOT/'assets/resource/base').wait().succeeded)
            resource.register_custom_action('script_task_state',state.ScriptTaskState())
            resource.register_custom_action('battle_play',player.BattlePlay())
            resource.register_custom_action('loop_count',Count())
            self.assertTrue(tasker.bind(resource,controller))
            prefix=task['name']+'-'
            pipeline=json.loads((ROOT/'assets/resource/base/pipeline'/(task['name']+'.json')).read_text(encoding='utf8'))
            # The existing scene-specific guard has its own fixtures. This test
            # covers real quest/result recognition and the new shared flow.
            scene=next(n for n in pipeline if pipeline[n].get('next')==[prefix+'回放战斗'])
            overrides={prefix+'刷取次数':{'max_hit':2}, scene:{'recognition':{'type':'DirectHit'}},
                prefix+'回放战斗':{'timeout':2000},
                '脚本任务设置':{'action':{'param':{'custom_action_param':{'failure_retry':{'enabled':True,'max_retries':2}}}}}}
            result=tasker.post_task(task['entry'],overrides).wait()
            checkpoints=[json.loads(p.read_text(encoding='utf8')) for p in state.STATUS_DIRECTORY.glob('*.json')]
            (self.log/'quota-result.json').write_text(json.dumps(dict(succeeded=result.succeeded,attempts=controller.attempts,
                successes=controller.successes,quota_hits=len(quotas),checkpoints=checkpoints,events=events),ensure_ascii=False,indent=2),encoding='utf8')
            self.assertTrue(result.succeeded)
            self.assertEqual((controller.attempts, controller.successes, len(quotas)),(3,2,2))
            self.assertEqual(controller.failed_returns,1)
            self.assertEqual([p['status'] for p in checkpoints],['success','success'])
            self.assertEqual(sorted(p['failure_retries'] for p in checkpoints),[0,1])
            self.assertFalse(state._rounds)


if __name__ == '__main__': unittest.main()
