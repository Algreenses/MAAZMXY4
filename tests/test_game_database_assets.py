"""Offline database integrity and actual release-file collection boundaries."""
import hashlib
import json
from pathlib import Path
import sys
import unittest

ROOT=Path(__file__).resolve().parents[1]
DATA=ROOT/'agent/data/game_database'
sys.path.insert(0,str(ROOT/'tools'))
from build_update import collect


class GameDatabaseAssetsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.doc=json.loads((DATA/'catalog.json').read_text(encoding='utf8'))
        cls.entries=cls.doc['entries']

    def test_all_categories_are_offline_and_provenance_is_explicit(self):
        self.assertEqual(self.doc['version'],1)
        self.assertEqual({e['category'] for e in self.entries},{'装备','道具','Boss'})
        self.assertEqual(len({e['id'] for e in self.entries}),len(self.entries))
        for entry in self.entries:
            self.assertTrue(entry['source_url'].startswith('https://news.4399.com/gonglue/zmxy4/'))
            self.assertEqual(len(entry['source_sha256']),64)
            self.assertTrue(entry['facts'] or entry['skills'],entry['name'])
            if entry['category']=='Boss':self.assertTrue(entry['skills'],entry['name'])
            if entry['config_variants']:self.assertTrue(any(r['kind']=='client' for r in entry['references']))
        self.assertEqual(self.doc['wiki_snapshot']['license'],'MIT')
        self.assertTrue((DATA/'LICENSE-zmxy4-helpPages.txt').is_file())

    def test_official_recipe_skills_and_distinct_boss_forms_are_preserved(self):
        by_name={e['name']:e for e in self.entries}
        self.assertTrue(any('七彩晶' in f['value'] and '玉荧草' in f['value'] for f in by_name['朱电霹雳棍']['facts']))
        self.assertIn('九命狐狸',[s['name'] for s in by_name['九尾天狐']['skills']])
        self.assertIn('神荼 · 束无常',[s['name'] for s in by_name['东方鬼帝']['skills']])
        self.assertGreater(len(by_name['九尾天狐']['config_variants']),1)
        variants=by_name['时间祖巫']['config_variants']
        self.assertGreater(len({s['value'] for v in variants for s in v['stats'] if s['label']=='生命'}),1)

    def test_icons_are_contained_hashed_and_collected_in_resource_packages(self):
        manifest=json.loads((DATA/'icons.json').read_text(encoding='utf8'))['icons']
        icons={i['path']:i for i in manifest}
        packaged={name for _path,name in collect(str(ROOT))}
        self.assertIn('agent/data/game_database/catalog.json',packaged)
        self.assertIn('agent/data/game_database/LICENSE-zmxy4-helpPages.txt',packaged)
        for entry in self.entries:
            if 'icon' not in entry:continue
            relative=entry['icon'];path=(DATA/relative).resolve()
            self.assertTrue(path.is_relative_to(DATA.resolve()))
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(),icons[relative]['sha256'])
            self.assertIn('agent/data/game_database/'+relative,packaged)
        self.assertFalse(any(name.endswith('.swf') or 'official-catalog-cache' in name for name in packaged))


if __name__=='__main__':unittest.main()
