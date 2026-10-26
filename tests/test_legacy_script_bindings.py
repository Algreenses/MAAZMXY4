from copy import deepcopy
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'agent'))
from legacy_script_bindings import plan_legacy_bindings
from task_catalog import task_key


class LegacyBindingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root/'recordings').mkdir()
        for name in ['mine.json','other.json']:
            (self.root/'recordings'/name).write_text(json.dumps(dict(version=1,name=name,base=[1148,720],duration_ms=200,
                actions=[dict(t=0,op='keydown',key=65),dict(t=200,op='keyup',key=65)])), encoding='utf8')
        self.old = dict(task=[dict(name='挑战',entry='Start',option=['回放文件'])],option={'回放文件':dict(cases=[
            {'name': name, 'pipeline_override': {'Fight': {'action': {'param': {'custom_action_param': {'file': name}}}}}}
            for name in ['mine.json','other.json','@latest','missing.json']])})
        self.tasks = [dict(name='挑战',entry='Start',option=[])]
        self.nodes = dict(Start=dict(next=['Fight']),Fight=dict(action=dict(type='Custom',param=dict(custom_action='battle_play'))))
        self.instance = dict(TaskItems=[dict(name='挑战',entry='Start',option=[dict(name='回放文件',index=0)],repeat_count=999)])
        self.ident = task_key('挑战','Start')

    def tearDown(self): self.temp.cleanup()

    def plan(self, instances=None, bindings=None):
        return plan_legacy_bindings(self.root,self.old,self.tasks,self.nodes,
            instances or [('config/instances/one.json',self.instance)], bindings or {},self.nodes)

    def test_selected_file_is_migrated_without_modifying_script_or_configuration(self):
        before = (self.root/'recordings/mine.json').read_bytes()
        bindings, report = self.plan()
        self.assertEqual(bindings[self.ident],dict(file='mine.json',mode='pipeline'))
        self.assertEqual(len(report),1)
        self.assertEqual((self.root/'recordings/mine.json').read_bytes(),before)
        self.assertEqual(self.instance['TaskItems'][0]['repeat_count'],999)
        self.assertFalse((self.root/'recordings/.bindings.json').exists())

    def test_existing_binding_is_never_replaced_by_legacy_choice(self):
        old = {self.ident:dict(file='other.json',mode='replace')}
        self.assertEqual(self.plan(bindings=old),(old,[]))

    def test_conflicting_instances_are_reported_before_any_write(self):
        second = deepcopy(self.instance); second['TaskItems'][0]['option'][0]['index']=1
        with self.assertRaisesRegex(ValueError,'不同配置'):
            self.plan([('one',self.instance),('two',second)])
        self.assertFalse((self.root/'recordings/.bindings.json').exists())

    def test_missing_explicit_script_does_not_fall_back_to_another_recording(self):
        self.instance['TaskItems'][0]['option'][0]['index']=3
        with self.assertRaises(FileNotFoundError): self.plan()

    def test_latest_is_resolved_before_default_templates_are_seeded(self):
        self.instance['TaskItems'][0]['option'][0]['index']=2
        bindings, report=self.plan()
        self.assertIn(bindings[self.ident]['file'],('mine.json','other.json'))
        self.assertEqual(report[0]['previous_reference'],'@latest')


if __name__ == '__main__': unittest.main()
