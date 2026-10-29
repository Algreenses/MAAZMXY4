"""Public asset provenance and foreground guards across existing task routes."""
import hashlib
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


def read(path):
    return json.loads((ROOT / path).read_text(encoding='utf8'))


class NavigationUICoverageTests(unittest.TestCase):
    def test_new_sources_and_fixtures_have_exact_provenance(self):
        for name in ('common-ui', 'settings-ui'):
            data = read('agent/data/navigation/' + name + '.json')
            self.assertTrue(data['source']['url'].startswith('https://sda.4399.com/'))
            self.assertEqual(len(data['source']['sha256']), 64)
            for asset in data['assets']:
                path = ROOT / 'assets/resource/base/image' / asset['path']
                self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), asset['sha256'])
                self.assertEqual(len(asset['source_frame_sha256']), 64)
        for folder, count in [('common-dialog', 6), ('settings', 3), ('result-roles', 42)]:
            base = ROOT / 'tests/fixtures/navigation' / folder
            data = json.loads((base / 'cases.json').read_text(encoding='utf8'))
            self.assertEqual(len(data['cases']), count)
            self.assertEqual({p.name for p in base.glob('*.png')}, {c['file'] for c in data['cases']})
            for case in data['cases']:
                self.assertEqual(hashlib.sha256((base / case['file']).read_bytes()).hexdigest(), case['sha256'])

    def test_common_confirm_is_readonly_and_uses_three_opaque_anchored_patches(self):
        node = read('assets/resource/base/pipeline/navigation_common_ui.json')['Nav_CommonConfirm']
        self.assertEqual(node['action'], {'type': 'DoNothing'})
        self.assertFalse(node['next'])
        parts = node['recognition']['param']['all_of']
        self.assertEqual(len(parts), 3)
        for part in parts:
            self.assertEqual(part['recognition']['type'], 'TemplateMatch')
            self.assertFalse(part['recognition']['param'].get('green_mask'))
        for part in parts[1:]:
            self.assertEqual(part['recognition']['param']['roi'], parts[0]['sub_name'])

    def test_task_entry_settings_and_returns_keep_foreground_guards(self):
        stage_count = settings_count = 0
        for path in (ROOT / 'assets/resource/base/pipeline').glob('*.json'):
            for name, node in json.loads(path.read_text(encoding='utf8')).items():
                if name.endswith('-关卡入口'):
                    stage_count += 1
                    self.assertIn('Result_NoRevivePrompt', node['recognition']['param']['all_of'])
                    self.assertEqual(node['recognition']['param']['box_index'], 1)
                if name.endswith('-点击设置'):
                    settings_count += 1
                    self.assertIn('Result_NoRevivePrompt', node['recognition']['param']['all_of'])
                    self.assertEqual(node.get('pre_delay', 0), 0)
                if name.endswith('-返回地图'):
                    self.assertEqual(node['recognition'], dict(type='And', param=dict(all_of=['Nav_SettingsReturn'])))
        self.assertEqual(stage_count, 7)
        self.assertEqual(settings_count, 11)

    def test_shared_navigation_clicks_are_guarded_except_known_sell_cancel(self):
        nodes = read('assets/resource/base/pipeline/navigation.json')
        graph = read('agent/data/navigation/graph.json')
        exemptions = set()
        pending = [e['entry'] for e in graph['edges'] if e['source'] == 'profile_sell']
        while pending:
            name = pending.pop()
            if name not in exemptions:
                exemptions.add(name)
                pending.extend(nodes[name].get('next', []))
        for name, node in nodes.items():
            if node['action']['type'] == 'Click' and name not in exemptions:
                self.assertIn('Result_NoRevivePrompt', node['recognition']['param']['all_of'], name)


if __name__ == '__main__':
    unittest.main()
