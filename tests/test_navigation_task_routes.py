"""Injected task routes close known overlays, never guess an absent stage."""
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'agent'))
from navigation import NavigationError, shortest_path
from navigation.catalog import task_graph
from navigation.runtime import GraphNavigator
from task_catalog import custom_parameters


class FrameContext:
    def __init__(self, nodes):
        self.nodes = deepcopy(nodes)
        self.hits, self.actions = set(), []
        self.transition = lambda _entry: None

    def get_node_data(self, name): return self.nodes[name]
    def override_pipeline(self, definitions): self.nodes.update(definitions); return True
    def run_recognition(self, name, frame): return SimpleNamespace(hit=name in frame)
    def run_task(self, entry, overrides):
        actual = overrides[entry]['next'][0]
        self.actions.append(actual)
        self.transition(actual)
        return SimpleNamespace(status=SimpleNamespace(succeeded=True))


class TaskNavigationRoutesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.nodes = {name:node for path in (ROOT/'assets/resource/base/pipeline').glob('*.json')
                     for name,node in json.loads(path.read_text(encoding='utf-8-sig')).items()}
        cls.tasks = []
        for task in json.loads((ROOT/'assets/interface.json').read_text(encoding='utf-8-sig'))['task']:
            parameters = next((custom_parameters(node) for name,node in cls.nodes.items()
                if name.startswith(task['name']+'-') and custom_parameters(node).get('navigation')), None)
            if parameters is not None: cls.tasks.append((task['name'],parameters))

    def setUp(self): self.clock = 0

    def run_route(self, context, graph):
        def wait(duration): self.clock += duration
        return GraphNavigator(context, graph, capture=lambda:frozenset(context.hits),
            check=lambda:None, log=lambda _line:None, clock=lambda:self.clock, wait=wait).run()

    def test_all_seven_stages_can_close_a_panel_without_clicking_the_stage(self):
        stages = 0
        for name,parameters in self.tasks:
            if parameters['navigation']['kind'] != 'stage': continue
            stages += 1
            with self.subTest(task=name):
                context = FrameContext(self.nodes)
                graph = task_graph(context,parameters)
                by_id = {node['id']:node for node in graph['nodes']}
                context.hits = set(by_id['profile']['recognition'])
                def close(_entry):
                    context.hits = set(by_id['task_map']['recognition'] + by_id['task_destination']['recognition'])
                context.transition = close
                self.assertEqual(self.run_route(context,graph),'task_destination')
                self.assertEqual(len(context.actions),1)
                self.assertNotIn(parameters['navigation']['target_node'],context.actions)
        self.assertEqual(stages,7)

    def test_stage_loading_failure_never_clicks_an_unrecognized_map_point(self):
        for name, parameters in self.tasks:
            if parameters['navigation']['kind'] != 'stage': continue
            prefix = name + '-'
            fallback = self.nodes.get(prefix + '点击场外')
            if fallback is not None:
                self.assertEqual(fallback['action']['type'], 'DoNothing', name)
            for node_name, definition in self.nodes.items():
                if node_name.startswith(prefix):
                    self.assertNotIn(prefix + '点击场外', definition.get('on_error', []), node_name)

    def test_wrong_map_has_no_navigation_input_and_is_not_success(self):
        for name,parameters in self.tasks:
            if parameters['navigation']['kind'] != 'stage': continue
            with self.subTest(task=name):
                context = FrameContext(self.nodes)
                graph = task_graph(context,parameters)
                context.hits = {'Nav_Probe_hub_scene'}
                with self.assertRaises(NavigationError) as error: self.run_route(context,graph)
                self.assertEqual(error.exception.reason,'unreachable')
                self.assertFalse(context.actions)

    def test_common_dialog_blocks_background_but_known_sell_dialog_keeps_its_route(self):
        parameters=next(p for _,p in self.tasks if p['navigation']['kind']=='stage')
        context=FrameContext(self.nodes)
        graph=task_graph(context,parameters)
        by_id={n['id']:n for n in graph['nodes']}
        context.hits={'Nav_CommonConfirm','Nav_Probe_hub_scene',parameters['navigation']['target_node']}
        nav=GraphNavigator(context,graph,capture=lambda:frozenset(context.hits),check=lambda:None,log=lambda _:None)
        self.assertEqual(nav.locate(),'common_confirm')
        with self.assertRaises(NavigationError):nav.run()
        self.assertFalse(context.actions)
        context.hits.update(by_id['profile_sell']['recognition'])
        self.assertEqual(nav.locate(),'profile_sell')

    def test_panel_closing_to_a_wrong_map_does_not_start_a_detour(self):
        parameters = next(p for _name,p in self.tasks if p['navigation']['kind']=='stage')
        context = FrameContext(self.nodes)
        graph = task_graph(context,parameters)
        context.hits = set(next(node for node in graph['nodes'] if node['id']=='profile')['recognition'])
        context.transition = lambda _entry:setattr(context,'hits',{'Nav_Probe_hub_scene'})
        with self.assertRaises(NavigationError): self.run_route(context,graph)
        self.assertEqual(len(context.actions),1)

    def test_paused_stage_map_can_resume_then_recheck_the_entry(self):
        parameters = next(p for _name,p in self.tasks if p['navigation']['kind']=='stage')
        context = FrameContext(self.nodes)
        graph = task_graph(context,parameters)
        context.hits = {'Nav_PausedTaskMap','Nav_Probe_hub_scene',parameters['navigation']['target_node']}
        context.transition = lambda _entry:setattr(context,'hits',{'Nav_Probe_hub_scene',parameters['navigation']['target_node']})
        self.assertEqual(self.run_route(context,graph),'task_destination')
        self.assertEqual(context.actions,['Nav_ResumeTaskMap'])

    def test_quest_panel_close_is_guarded_and_routes_to_the_stage(self):
        close = self.nodes['Nav_CloseQuest']
        self.assertEqual(close['recognition']['type'],'And')
        self.assertEqual(close['action']['param']['target'],True)
        parameters = next(p for _name,p in self.tasks if p['navigation']['kind']=='stage')
        context = FrameContext(self.nodes)
        graph = task_graph(context,parameters)
        context.hits = {'Nav_QuestPanel'}
        context.transition = lambda _entry:setattr(context,'hits',{'Nav_Probe_hub_scene',parameters['navigation']['target_node']})
        self.assertEqual(self.run_route(context,graph),'task_destination')
        self.assertEqual(context.actions,['Nav_CloseQuest'])

    def test_general_world_and_daily_menu_have_routes_but_never_enter_bounty_board(self):
        for _name,parameters in self.tasks:
            if parameters['navigation']['kind'] != 'bounty': continue
            context = FrameContext(self.nodes)
            graph = task_graph(context,parameters)
            for start in ('world','daily_menu','profile','warehouse'):
                with self.subTest(task=parameters['navigation']['title'],start=start):
                    self.assertTrue(shortest_path(graph,start))
            self.assertFalse(any(edge['target']=='bounty_board' for edge in graph['edges']))


if __name__ == '__main__': unittest.main()
