"""Exercise the real three-item package and updater in isolated installation roots."""
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT/'tools'))
from build_update import collect
import updater


class ReleasePackageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.package_temp = tempfile.TemporaryDirectory()
        cls.package = Path(cls.package_temp.name)/'update.zip'
        with zipfile.ZipFile(cls.package,'w') as archive:
            for source,relative in collect(str(ROOT)): archive.write(source,relative)

    @classmethod
    def tearDownClass(cls): cls.package_temp.cleanup()

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.root=Path(self.temp.name)

    def tearDown(self): self.temp.cleanup()

    def write(self,name,raw):
        path=self.root/name
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_bytes(raw)

    def list_from_installed_worker(self):
        result=subprocess.run([sys.executable,'-E','-s','-u','-X','utf8',str(self.root/'agent/script_studio_service.py'),'--root',str(self.root)],
            input=json.dumps(dict(id=1,method='list'))+'\n',capture_output=True,text=True,encoding='utf8',timeout=20)
        self.assertEqual(result.returncode,0,result.stderr)
        reply=json.loads(result.stdout.strip())
        self.assertTrue(reply['ok'],reply)
        return reply['result']

    def apply(self):
        with patch.object(updater,'BASE',str(self.root)),patch.object(updater,'LOG_PATH',str(self.root/'temp/updater.log')):
            return updater.apply_update(str(self.package),{},lambda *_:None)

    def test_clean_package_worker_loads_eight_defaults_without_assets(self):
        with zipfile.ZipFile(self.package) as archive: archive.extractall(self.root)
        self.assertFalse((self.root/'assets').exists())
        listing=self.list_from_installed_worker()
        self.assertEqual(sum(row['is_default'] for row in listing['items']),8)
        self.assertEqual(set(listing['task_bindings']),{'bounty-candle'})
        for task in listing['tasks']:
            for parameters in task['playback_parameters'].values():
                if str(parameters.get('file','')).startswith('@task:'):
                    self.assertEqual(parameters['file'],'@task:'+task['id'])
        help_result=subprocess.run([sys.executable,str(self.root/'agent/task_asset_sync.py'),'--help'],capture_output=True,timeout=20)
        self.assertEqual(help_result.returncode,0,help_result.stderr)

    def test_resource_update_preserves_user_files_and_does_not_claim_to_update_frontend(self):
        self.write('interface.json',b'{"version":"2.2"}')
        self.write('agent/old.py',b'# old agent')
        self.write('resource/old.txt',b'old resource')
        template=next(path for path in (ROOT/'agent/recordings').glob('*.json') if path.name!='manifest.json')
        protected={
            'recordings/personal.json':template.read_bytes(),
            'recordings/.bindings.json':b'{"version":2,"tasks":{"bounty-candle":{"file":"personal.json","mode":"pipeline"}}}',
            'config/instances/personal.json':b'{"TaskItems":[],"repeat_count":999}',
            'MFAAvalonia.exe':b'old frontend sentinel; never executed',
        }
        for name,raw in protected.items(): self.write(name,raw)
        self.assertTrue(self.apply()[0])
        listing=self.list_from_installed_worker()
        self.assertEqual(len(listing['items']),9)
        for name,raw in protected.items(): self.assertEqual((self.root/name).read_bytes(),raw,name)
        self.assertTrue((self.root/'agent/recordings/manifest.json').is_file())

    def test_failed_resource_replacement_restores_the_old_installation(self):
        old={'interface.json':b'{"version":"2.2"}','agent/main.py':b'# old agent','resource/old.txt':b'old resource'}
        for name,raw in old.items(): self.write(name,raw)
        real_move=shutil.move
        failed=False
        def fail_once(source,destination,*args,**kwargs):
            nonlocal failed
            if not failed and Path(source).name=='agent' and Path(source).parent.name=='extract':
                failed=True
                raise OSError('injected replacement failure')
            return real_move(source,destination,*args,**kwargs)
        with patch.object(updater.shutil,'move',side_effect=fail_once):
            result=self.apply()
        self.assertFalse(result[0])
        self.assertTrue(failed)
        for name,raw in old.items(): self.assertEqual((self.root/name).read_bytes(),raw,name)


if __name__=='__main__': unittest.main()
