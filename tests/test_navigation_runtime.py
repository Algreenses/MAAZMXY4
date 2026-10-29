"""Same-frame planning and bounded transitions; no Maa or desktop input."""
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'agent'))
from navigation import NavigationError
from navigation.runtime import GraphNavigator


def graph():
    return dict(version=1, targets=['goal'], max_steps=8, max_seconds=10,
        settle_ms=10, locate_attempts=3,
        nodes=[dict(id=n, label=n, recognition=[n]) for n in ('start', 'other', 'goal')],
        edges=[dict(id=a+'_'+b, source=a, target=b, label=a+' to '+b,
                    entry='click_'+b, cost=1, timeout_ms=1000, max_attempts=2)
               for a,b in [('start','goal'),('other','goal')]])


class MemoryContext:
    def __init__(self):
        self.hits = {'start'}
        self.recognitions, self.actions, self.frames = [], [], []
        self.click = lambda entry: setattr(self, 'hits', {entry.removeprefix('click_')})
    def capture(self):
        frame = frozenset(self.hits)
        self.frames.append(frame)
        return frame
    def run_recognition(self, name, frame):
        self.recognitions.append((name, frame))
        return SimpleNamespace(hit=name in frame)
    def run_task(self, entry, overrides):
        self.actions.append((entry, deepcopy(overrides)))
        self.click(overrides[entry]['next'][0])
        return SimpleNamespace(status=SimpleNamespace(succeeded=True))


class NavigationRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.context, self.clock = MemoryContext(), 0
        self.graph = graph()
        self.cancelled = False
    def check(self):
        if self.cancelled: raise InterruptedError('cancelled')
    def navigator(self):
        def wait(duration): self.clock += duration
        return GraphNavigator(self.context, self.graph, capture=self.context.capture,
            check=self.check, wait=wait, clock=lambda:self.clock, log=lambda _line:None)
    def test_arriving_at_target_has_no_input(self):
        self.context.hits = {'goal'}
        self.assertEqual(self.navigator().run(), 'goal')
        self.assertFalse(self.context.actions)
    def test_first_click_is_a_recognized_next_not_an_unchecked_entry(self):
        self.assertEqual(self.navigator().run(), 'goal')
        entry, override = self.context.actions[0]
        self.assertEqual(override[entry]['action']['type'], 'DoNothing')
        self.assertEqual(override[entry]['next'], ['click_goal'])
        self.assertNotEqual(entry, 'click_goal')
    def test_missed_clicks_are_bounded_without_using_task_counter(self):
        self.context.click = lambda _entry:None
        with self.assertRaises(NavigationError) as error:
            self.navigator().run()
        self.assertEqual(error.exception.reason, 'unreachable')
        self.assertEqual(len(self.context.actions), 2)
        self.assertNotIn('刷取次数', str(self.context.actions))
    def test_actual_unexpected_known_screen_is_replanned(self):
        def click(_entry):
            self.context.hits = {'other'} if len(self.context.actions) == 1 else {'goal'}
        self.context.click = click
        self.assertEqual(self.navigator().run(), 'goal')
        self.assertEqual(len(self.context.actions), 2)
    def test_unknown_and_ambiguous_screens_stop_without_click(self):
        for hits, reason, attempts in [(set(), 'unknown', 3), ({'start','other'}, 'ambiguous', 1)]:
            with self.subTest(reason=reason):
                self.context = MemoryContext()
                self.context.hits = hits
                with self.assertRaises(NavigationError) as error:
                    self.navigator().run()
                self.assertEqual(error.exception.reason, reason)
                self.assertEqual(len(self.context.frames), attempts)
                self.assertFalse(self.context.actions)
    def test_obscuring_panel_wins_and_probes_share_one_frame(self):
        self.graph['nodes'][1]['obscures'] = ['start']
        self.graph['nodes'][1]['recognition'].append('shared')
        self.graph['nodes'][0]['recognition'].append('shared')
        self.context.hits = {'start', 'other', 'shared'}
        self.assertEqual(self.navigator().locate(), 'other')
        self.assertEqual(sum(name == 'shared' for name,_ in self.context.recognitions), 1)
        self.assertEqual(len({id(frame) for _,frame in self.context.recognitions}), 1)
    def test_cancellation_and_blocking_dialog_never_click(self):
        self.cancelled = True
        with self.assertRaises(InterruptedError): self.navigator().run()
        self.cancelled = False
        self.graph['abort'] = {'captcha':'blocked'}
        self.context.hits.add('captcha')
        with self.assertRaises(NavigationError) as error: self.navigator().run()
        self.assertEqual(error.exception.reason, 'blocked')
        self.assertFalse(self.context.actions)
    def test_monotonic_deadline_applies_during_recognition(self):
        original = self.context.run_recognition
        def slow(name, frame):
            self.clock += 11
            return original(name, frame)
        self.context.run_recognition = slow
        with self.assertRaises(TimeoutError): self.navigator().run()
        self.assertFalse(self.context.actions)

    def test_relocalizing_after_recovery_does_not_reset_the_total_deadline(self):
        self.context.hits={'goal'}
        nav=self.navigator()
        self.assertEqual(nav.run(),'goal')
        self.clock=11
        with self.assertRaises(TimeoutError):nav.run()

    def test_confirmed_modal_skips_only_the_states_it_obscures(self):
        self.graph['nodes'].append(dict(id='modal',label='modal',recognition=['modal'],
                                       obscures=['start','other','goal']))
        self.context.hits={'modal','start','goal'}
        self.assertEqual(self.navigator().locate(),'modal')
        self.assertEqual([name for name,_ in self.context.recognitions],['modal'])

    def test_unrelated_overlay_remains_ambiguous(self):
        self.graph['nodes'] += [dict(id=n,label=n,recognition=[n],obscures=['start','goal'])
                                for n in ('modal_a','modal_b')]
        self.context.hits={'modal_a','modal_b','start'}
        with self.assertRaises(NavigationError) as error:self.navigator().locate()
        self.assertEqual(error.exception.reason,'ambiguous')

    def test_excluded_modal_must_not_suppress_the_background(self):
        self.graph['nodes'].append(dict(id='modal',label='modal',recognition=['modal'],
            exclude=['not_modal'],obscures=['start','other','goal']))
        self.context.hits={'modal','not_modal','start'}
        self.assertEqual(self.navigator().locate(),'start')


if __name__ == '__main__': unittest.main()
