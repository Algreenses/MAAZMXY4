"""Regression for idle preview exhausting Maa 5.12 Agent event reply queues.

Set MAA_AGENT_NATIVE_TESTS=1 and use the embedded Python for native IPC tests.
These tests use memory pixels and private Agent processes, never a game window.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import textwrap
import unittest

ROOT = Path(__file__).resolve().parents[1]
PATCHES = ROOT / 'frontend/patches'


def patch(name):
    return json.loads((PATCHES / name).read_text(encoding='utf8'))


def native_case(forward_events):
    import numpy as np
    from maa.agent_client import AgentClient
    from maa.controller import CustomController
    from maa.resource import Resource
    from maa.tasker import Tasker

    class Pixels(CustomController):
        def connect(self): return True
        def request_uuid(self): return 'isolated-agent-preview'
        def screencap(self): return np.full((32, 32, 3), 100, dtype=np.uint8)
        def forbidden(self, *args): raise AssertionError('No device input permitted')
        click = swipe = touch_down = touch_up = key_down = key_up = input_text = forbidden

    directory = Path(tempfile.mkdtemp(prefix='agent-preview-'))
    Tasker.set_log_dir(directory / 'client')
    server = directory / 'server.py'
    server.write_text(textwrap.dedent('''\
        import sys
        from maa.agent.agent_server import AgentServer
        from maa.custom_action import CustomAction
        @AgentServer.custom_action('probe')
        class Probe(CustomAction):
            def run(self, context, argv):
                # Resource-only binding must still allow reverse controller calls.
                return context.tasker.controller.post_screencap().wait().succeeded
        AgentServer.start_up(sys.argv[1])
        AgentServer.join()
        AgentServer.shut_down()
        '''), encoding='utf8')
    controller, resource, tasker = Pixels(), Resource(), Tasker()
    assert controller.post_connection().wait().succeeded
    assert controller.set_screenshot_use_raw_size(True)
    (directory / 'pipeline').mkdir()
    (directory / 'pipeline/probe.json').write_text(json.dumps({'Probe': {
        'action': {'type': 'Custom', 'param': {'custom_action': 'probe'}}, 'post_delay': 0}}))
    assert resource.post_bundle(directory).wait().succeeded
    assert tasker.bind(resource, controller)
    client = AgentClient()
    client.set_timeout(1500)  # Bound the intentionally broken control case.
    assert client.bind(resource)
    if forward_events:
        assert client.register_sink(resource, controller, tasker)
    with (directory / 'server.log').open('wb') as output:
        process = subprocess.Popen([sys.executable, '-E', '-s', '-u', str(server), client.identifier],
                                   cwd=directory, stdout=output, stderr=output)
        try:
            assert client.connect()
            first_unavailable = None
            for number in range(7000):
                assert controller.post_screencap().wait().succeeded
                if not client.alive:
                    first_unavailable = number + 1
                    break
            alive = client.alive
            succeeded = tasker.post_task('Probe').wait().succeeded
            result = dict(frames=number + 1, alive=alive, succeeded=succeeded,
                          first_unavailable=first_unavailable)
            print('RESULT ' + json.dumps(result), flush=True)
        finally:
            process.terminate()
            process.wait(timeout=5)
    # Native objects can have callbacks in flight in the deliberately broken case.
    # This isolated child owns every handle; process exit closes them together.
    import shutil
    shutil.rmtree(directory, ignore_errors=True)
    os._exit(0)


class AgentPreviewPatchTests(unittest.TestCase):
    def test_frontend_build_installs_callbacks_only_for_both_transports(self):
        manifest = json.loads((ROOT / 'frontend/upstream.json').read_text(encoding='utf8'))
        for name in ('09-agent-callbacks-only.json', '10-agent-connection-health.json'):
            self.assertIn('frontend/patches/' + name, manifest['patches'])
        factory = patch('09-agent-callbacks-only.json')['replace']
        self.assertIn('MaaAgentClient.CreateTcp(tasker.Resource)', factory)
        self.assertIn('MaaAgentClient.Create(identifier, tasker.Resource)', factory)
        self.assertNotIn('MaaAgentClient.Create(identifier, tasker);', factory)


@unittest.skipUnless(os.environ.get('MAA_AGENT_NATIVE_TESTS') == '1', 'opt-in native Agent transport test')
class AgentPreviewNativeTests(unittest.TestCase):
    def run_case(self, forward_events):
        env = dict(os.environ, MAAFW_BINARY_PATH=str(ROOT / 'runtimes/win-x64/native'))
        result = subprocess.run([sys.executable, '-X', 'utf8', str(Path(__file__).resolve()),
                                 '--native-case', 'events' if forward_events else 'callbacks'],
                                env=env, capture_output=True, text=True, encoding='utf8', timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout[-2000:])
        lines = [line[7:] for line in result.stdout.splitlines() if line.startswith('RESULT ')]
        self.assertEqual(len(lines), 1, result.stderr + result.stdout[-2000:])
        return json.loads(lines[0])

    def test_legacy_event_forwarding_reproduces_blocked_channel(self):
        result = self.run_case(True)
        self.assertFalse(result['alive'], result)
        self.assertFalse(result['succeeded'], result)
        self.assertLess(result['first_unavailable'], 7000)

    def test_long_preview_then_action_and_reverse_screenshot_succeed(self):
        result = self.run_case(False)
        self.assertEqual(result, dict(frames=7000, alive=True, succeeded=True, first_unavailable=None))

    def test_actual_idle_reuse_predicate_checks_agent_without_interrupting_running_jobs(self):
        # Compile the exact production method from the host patch. Only its
        # dependencies are fakes; the lifecycle predicate is not reimplemented.
        method = patch('10-agent-connection-health.json')['replace']
        program = '''
using System;
using System.Collections.Generic;
using System.Linq;
var cases = new[] {
    (false, true, false, false, false, 0, false, true),
    (true, true, true, false, false, 1, true, true),
    (true, true, true, false, false, 1, false, false),
    (true, true, true, false, false, 0, false, false),
    (true, true, false, false, false, 1, true, false),
    (true, true, true, true, false, 1, false, true),
    (true, true, true, false, true, 1, false, true),
    (false, false, false, false, false, 0, false, false)
};
foreach (var (configured, connected, started, running, stopping, count, alive, expected) in cases)
    if (new Probe(configured, connected, started, running, stopping, count, alive).Check() != expected)
        throw new Exception("Incorrect session reuse decision");
Console.WriteLine("8 lifecycle cases passed");
class Probe {
    Tasker? MaaTasker;
    Interface? Interface;
    bool _agentStarted;
    List<Context> _agentContexts;
    public Probe(bool configured, bool connected, bool started, bool running, bool stopping, int count, bool alive) {
        MaaTasker = new() { Controller = new() { IsConnected = connected }, IsRunning = running, IsStopping = stopping };
        Interface = new() { Agent = configured };
        _agentStarted = started;
        _agentContexts = Enumerable.Range(0, count).Select(_ => new Context { Client = new() { IsAlive = alive } }).ToList();
    }
    public bool Check() => HasReusableMainTasker();
    METHOD
}
class Tasker { public Controller? Controller; public bool IsRunning; public bool IsStopping; }
class Controller { public bool IsConnected; }
class Interface { public bool Agent; }
class Context { public Client? Client; }
class Client { public bool IsAlive; }
static class AgentHelper { public static bool HasAgentConfigs(bool? value) => value == true; }
static class LoggerHelper { public static void Warning(string text) { } }
'''.replace('METHOD', method)
        with tempfile.TemporaryDirectory(prefix='agent-health-') as temporary:
            folder = Path(temporary)
            (folder / 'Program.cs').write_text(program, encoding='utf8')
            (folder / 'Probe.csproj').write_text('<Project Sdk="Microsoft.NET.Sdk"><PropertyGroup><OutputType>Exe</OutputType><TargetFramework>net10.0</TargetFramework><Nullable>enable</Nullable></PropertyGroup></Project>')
            sdk = ROOT / '.runtime-cache/dotnet-sdk/dotnet.exe'
            result = subprocess.run([str(sdk) if sdk.exists() else 'dotnet', 'run', '--project', str(folder / 'Probe.csproj')],
                                    capture_output=True, text=True, encoding='utf8', errors='replace', timeout=60)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn('8 lifecycle cases passed', result.stdout)


if __name__ == '__main__':
    if '--native-case' in sys.argv:
        native_case(sys.argv[-1] == 'events')
    else:
        unittest.main()
