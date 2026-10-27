"""Exercise the real three-item package and updater in isolated installation roots."""
import hashlib
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
import build_release
from build_release import client_files, validate_client, validate_client_resources
import updater
import setup_runtime


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

    def test_clean_package_worker_loads_only_unbound_example_without_assets(self):
        with zipfile.ZipFile(self.package) as archive: archive.extractall(self.root)
        self.assertFalse((self.root/'assets').exists())
        listing=self.list_from_installed_worker()
        self.assertEqual([row['file'] for row in listing['items']], ['示例-移动与攻击.json'])
        self.assertTrue(listing['items'][0]['is_default'])
        self.assertEqual(listing['task_bindings'], {})
        for task in listing['tasks']:
            for parameters in task['playback_parameters'].values():
                if str(parameters.get('file','')).startswith('@task:'):
                    self.assertEqual(parameters['file'],'@task:'+task['id'])
        help_result=subprocess.run([sys.executable,str(self.root/'agent/task_asset_sync.py'),'--help'],capture_output=True,timeout=20)
        self.assertEqual(help_result.returncode,0,help_result.stderr)

    def test_packaged_tasks_have_all_recorded_return_routes_and_reject_stale_resources(self):
        with zipfile.ZipFile(self.package) as archive: archive.extractall(self.root)
        checked = validate_client_resources(self.root)
        self.assertIn('烛时（钟晷极地）', checked)
        self.assertEqual(len(checked), 10)
        path = self.root/'resource/base/pipeline/烛时（钟晷极地）.json'
        data = json.loads(path.read_text(encoding='utf8'))
        del data['烛时（钟晷极地）-回放战斗']['action']['param']['custom_action_param']['script_binding']
        path.write_text(json.dumps(data),encoding='utf8')
        with self.assertRaisesRegex(ValueError, '缺少录制返回确认'):
            validate_client_resources(self.root)

    def client_fixture(self):
        frontend = self.root/'frontend'
        for name in build_release.FRONTEND_REQUIRED: self.write('frontend/'+name,b'fixture binary')
        for name in build_release.PYTHON_REQUIRED:
            if name in ('DLLs', 'Lib/site-packages/MaaAgentBinary'):
                (self.root/'python'/name).mkdir(parents=True, exist_ok=True)
            else:
                self.write('python/'+name,b'fixture interpreter')
        self.write('python/Lib/site-packages/MaaAgentBinary/fixture.dll', b'fixture binary')
        launcher = self.root/build_release.LAUNCHER_NAME
        self.write(build_release.LAUNCHER_NAME, b'fixture launcher; never executed')
        self.launcher_hash = hashlib.sha256(launcher.read_bytes()).hexdigest()
        return frontend, self.root/'python', launcher

    def test_full_client_collects_canonical_resources_and_crlf_setup_without_preview_data(self):
        frontend, runtime, launcher = self.client_fixture()
        self.write('frontend/libs/AGENTS.md', b'local frontend instructions')
        self.write('python/Lib/Agents.MD', b'local runtime instructions')
        for name in ['recordings/private.json','config/instances/private.json','logs/test.log','appsettings.json',
                     'resource/base/pipeline/old.json','interface.json']:
            self.write('frontend/'+name,b'private or stale preview data')
        self.write('python/Lib/__pycache__/ignored.pyc',b'cache')
        with patch.object(build_release, 'LAUNCHER_SHA256', self.launcher_hash):
            files = dict((relative,Path(source)) for source,relative in client_files(ROOT,frontend,runtime,launcher))
        self.assertIn('MAA造梦西游4.exe', files)
        self.assertIn('runtimes/python/pythonw.exe', files)
        self.assertEqual(files['interface.json'],ROOT/'assets/interface.json')
        self.assertEqual(files['resource/base/pipeline/烛时（钟晷极地）.json'],ROOT/'assets/resource/base/pipeline/烛时（钟晷极地）.json')
        self.assertFalse(any(name.startswith(('recordings/','config/','logs/')) or '__pycache__' in name for name in files))
        self.assertNotIn('appsettings.json',files)
        self.assertFalse(any(Path(name).name.casefold() == 'agents.md' for name in files))
        batch=files['DependencySetup_依赖库安装_win.bat'].read_bytes()
        self.assertIn(b'\r\n',batch)
        self.assertNotIn(b'\n',batch.replace(b'\r\n',b''))
        self.assertIn(b'VC_ARG=/install',batch)
        self.assertIn(b'DOTNET_ARG=/install',batch)

    def test_resource_package_excludes_nested_local_instructions(self):
        for name in ('agent/AGENTS.md', 'assets/resource/base/Agents.MD'):
            self.write('sample/'+name, b'local instructions')
        self.write('sample/agent/main.py', b'# public code')
        self.write('sample/assets/resource/base/public.json', b'{}')
        self.write('sample/assets/interface.json', b'{}')
        names = [relative for _, relative in collect(str(self.root/'sample'))]
        self.assertIn('agent/main.py', names)
        self.assertIn('resource/base/public.json', names)
        self.assertFalse(any(Path(name).name.casefold() == 'agents.md' for name in names))

    def test_client_rejects_unverified_launcher_and_incomplete_gui_runtime(self):
        frontend, runtime, launcher = self.client_fixture()
        with self.assertRaisesRegex(ValueError, 'SHA-256 不匹配'):
            client_files(ROOT, frontend, runtime, launcher)
        with patch.object(build_release, 'LAUNCHER_SHA256', self.launcher_hash):
            launcher.unlink()
            with self.assertRaisesRegex(ValueError, '缺少官方启动器'):
                client_files(ROOT, frontend, runtime, launcher)
            self.write(build_release.LAUNCHER_NAME, b'fixture launcher; never executed')
            for name in ('pythonw.exe', 'tcl/tcl8.6/init.tcl', 'Lib/tkinter/__init__.py'):
                raw = (runtime/name).read_bytes()
                (runtime/name).unlink()
                with self.assertRaisesRegex(ValueError, name.replace('.', r'\.')):
                    client_files(ROOT, frontend, runtime, launcher)
                (runtime/name).write_bytes(raw)

    def test_extracted_client_validation_checks_startup_files_as_well_as_resources(self):
        frontend, runtime, launcher = self.client_fixture()
        stage = self.root/'assembled'
        with patch.object(build_release, 'LAUNCHER_SHA256', self.launcher_hash):
            for source, relative in client_files(ROOT, frontend, runtime, launcher):
                destination = stage/relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, destination)
            self.assertEqual(len(validate_client(stage)), 10)
            (stage/'runtimes/python/pythonw.exe').unlink()
            with self.assertRaisesRegex(ValueError, 'pythonw.exe'):
                validate_client(stage)
            (stage/'runtimes/python/pythonw.exe').mkdir()
            with self.assertRaisesRegex(ValueError, 'pythonw.exe'):
                validate_client(stage)

    def test_runtime_rebuild_retains_updater_and_recording_dependencies(self):
        source = self.root/'system-python'
        required = ('Lib/tkinter/__init__.py', 'DLLs/_tkinter.pyd', 'DLLs/tcl86t.dll',
                    'DLLs/tk86t.dll', 'tcl/tcl8.6/init.tcl', 'tcl/tk8.6/tk.tcl',
                    'Lib/site-packages/pynput/__init__.py', 'Lib/site-packages/six.py')
        for name in required: self.write('system-python/'+name, b'tk dependency')
        (source/'Lib/site-packages').mkdir(parents=True, exist_ok=True)
        target = self.root/'rebuilt-python'
        with patch.object(setup_runtime, 'PY_DIR', str(target)), patch('builtins.print'):
            setup_runtime.build(str(source))
        for name in required:
            self.assertEqual((target/name).read_bytes(), b'tk dependency', name)

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
        self.assertEqual(len(listing['items']),2)
        for name,raw in protected.items(): self.assertEqual((self.root/name).read_bytes(),raw,name)
        self.assertTrue((self.root/'agent/recordings/manifest.json').is_file())

    def test_update_keeps_existing_retired_default_and_its_binding(self):
        template = ROOT/'agent/recordings/示例-移动与攻击.json'
        protected = {
            'recordings/旧用户脚本.json': template.read_bytes(),
            'recordings/.defaults.json': json.dumps({'version': 1, 'items': {
                'retired-template': {'file': '旧用户脚本.json', 'state': 'present'}},
                'bindings_initialized': True}).encode('utf8'),
            'recordings/.bindings.json': json.dumps({'version': 2, 'tasks': {
                'bounty-candle': {'file': '旧用户脚本.json', 'mode': 'pipeline'}}},
                ensure_ascii=False).encode('utf8'),
        }
        for name, raw in protected.items(): self.write(name, raw)
        self.assertTrue(self.apply()[0])
        listing = self.list_from_installed_worker()
        self.assertEqual({item['file'] for item in listing['items']},
                         {'旧用户脚本.json', '示例-移动与攻击.json'})
        for name, raw in protected.items():
            if name.endswith('.defaults.json'):
                self.assertEqual(json.loads((self.root/name).read_bytes())['items']['retired-template'],
                                 json.loads(raw)['items']['retired-template'])
            else:
                self.assertEqual((self.root/name).read_bytes(), raw, name)

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
