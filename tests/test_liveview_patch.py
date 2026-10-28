"""Exercise the actual frontend patch fragments against connection races.

The opt-in part compiles the C# fragments with a simulated controller: it
does not create a window, connect a device, or send input.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
PATCHES = ROOT / 'frontend/patches'
CONNECTING = '18-liveview-connecting.json'
SUCCESS = '19-liveview-success-reset.json'
MISSING_STATE = '20-liveview-missing-frame-state.json'
MISSING_WARNING = '21-liveview-missing-frame-warning.json'


def load_patch(name):
    return json.loads((PATCHES / name).read_text(encoding='utf8'))


class LiveViewPatchTests(unittest.TestCase):
    def test_pinned_frontend_build_includes_liveview_patches(self):
        manifest = json.loads((ROOT / 'frontend/upstream.json').read_text(encoding='utf8'))
        for name in (CONNECTING, SUCCESS, MISSING_STATE, MISSING_WARNING):
            self.assertIn('frontend/patches/' + name, manifest['patches'])
            patch = load_patch(name)
            self.assertNotEqual(patch['find'], patch['replace'])

    @unittest.skipUnless(os.environ.get('LIVEVIEW_PATCH_TESTS') == '1', 'opt-in .NET live-view test')
    def test_connection_and_completed_capture_failure_streak(self):
        connection, success = load_patch(CONNECTING), load_patch(SUCCESS)
        source = r'''
using System;

static void Check(bool value, string text) { if (!value) throw new Exception(text); }

var oldTimer = new OldTimer();
oldTimer.Processor.IsConnecting = true;
for (int i = 0; i < 20; i++) oldTimer.Tick();
Check(!oldTimer.IsConnected, "baseline must reproduce the premature disconnect");

var fixedTimer = new FixedTimer();
fixedTimer.Processor.IsConnecting = true;
for (int i = 0; i < 20; i++) fixedTimer.Tick();
Check(fixedTimer.IsConnected && fixedTimer.Processor.Polls == 0, "connecting must not poll");
fixedTimer.Processor.IsConnecting = false;
for (int i = 0; i < 20; i++) fixedTimer.Tick();
Check(!fixedTimer.IsConnected && fixedTimer.Processor.Polls == 20, "real consecutive failures must still disconnect");

var oldCounter = new OldCounter();
var fixedCounter = new FixedCounter();
for (int i = 0; i < 19; i++) { oldCounter.Record(MaaJobStatus.Failed); fixedCounter.Record(MaaJobStatus.Failed); }
oldCounter.Record(MaaJobStatus.Succeeded); fixedCounter.Record(MaaJobStatus.Succeeded);
oldCounter.Record(MaaJobStatus.Failed); fixedCounter.Record(MaaJobStatus.Failed);
Check(oldCounter.Failures == 20, "baseline must reproduce accumulated nonconsecutive failures");
Check(fixedCounter.Failures == 1, "a completed success must reset the failure streak");
for (int i = 0; i < 19; i++) fixedCounter.Record(MaaJobStatus.Failed);
Check(fixedCounter.Failures == 20, "20 consecutive failures must remain detectable");
Console.WriteLine("PASS startup guard and completed-frame failure streak");

sealed class FakeProcessor {
    public bool IsConnecting;
    public int Polls;
    public bool PollFailed() => ++Polls >= 20;
}
sealed class OldTimer {
    public bool IsLiveViewExpanded = true, EnableLiveView = true, IsConnected = true;
    public FakeProcessor Processor = new();
    public void Tick() {
@@OLD_TIMER@@
        { if (Processor.PollFailed()) IsConnected = false; }
    }
}
sealed class FixedTimer {
    public bool IsLiveViewExpanded = true, EnableLiveView = true, IsConnected = true;
    public FakeProcessor Processor = new();
    void ResetLiveViewNoImageWarning() { }
    public void Tick() {
@@NEW_TIMER@@
        { if (Processor.PollFailed()) IsConnected = false; }
    }
}
enum MaaJobStatus { Succeeded, Failed, Invalid }
sealed class OldCounter {
    object? _liveViewScreencapJob = new();
    public int Failures;
    void ResetActionFailedCount() => Failures = 0;
    MaaJobStatus? Completed(MaaJobStatus status) {
@@OLD_COUNTER@@
        return null;
    }
    public void Record(MaaJobStatus status) {
        var result = Completed(status);
        if (result is MaaJobStatus.Failed or MaaJobStatus.Invalid) Failures++;
    }
}
sealed class FixedCounter {
    object? _liveViewScreencapJob = new();
    public int Failures;
    void ResetActionFailedCount() => Failures = 0;
    MaaJobStatus? Completed(MaaJobStatus status) {
@@NEW_COUNTER@@
        return null;
    }
    public void Record(MaaJobStatus status) {
        var result = Completed(status);
        if (result is MaaJobStatus.Failed or MaaJobStatus.Invalid) Failures++;
    }
}
'''
        for marker, text in (('@@OLD_TIMER@@', connection['find']),
                             ('@@NEW_TIMER@@', connection['replace']),
                             ('@@OLD_COUNTER@@', success['find']),
                             ('@@NEW_COUNTER@@', success['replace'])):
            self.assertEqual(source.count(marker), 1)
            source = source.replace(marker, text)
        dotnet = os.environ.get('LIVEVIEW_DOTNET') or shutil.which('dotnet')
        self.assertTrue(dotnet, '.NET 10 SDK is required for LIVEVIEW_PATCH_TESTS')
        with tempfile.TemporaryDirectory(prefix='liveview-patch-') as temporary:
            folder = Path(temporary)
            (folder / 'Program.cs').write_text(source, encoding='utf8')
            (folder / 'Probe.csproj').write_text(
                '<Project Sdk="Microsoft.NET.Sdk"><PropertyGroup><OutputType>Exe</OutputType>'
                '<TargetFramework>net10.0</TargetFramework><Nullable>enable</Nullable>'
                '</PropertyGroup></Project>', encoding='utf8')
            result = subprocess.run([dotnet, 'run', '--project', str(folder / 'Probe.csproj'), '-c', 'Release'],
                capture_output=True, text=True, encoding='utf8', errors='replace', timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('PASS startup guard and completed-frame failure streak', result.stdout)

    @unittest.skipUnless(os.environ.get('LIVEVIEW_PATCH_TESTS') == '1', 'opt-in .NET live-view test')
    def test_missing_frame_warning_waits_for_persistent_absence_and_resets(self):
        connecting = load_patch(CONNECTING)
        state, warning = load_patch(MISSING_STATE), load_patch(MISSING_WARNING)
        page = r'''
sealed class @@NAME@@ {
    bool _liveViewNoImageLogged;
    public bool IsLiveViewExpanded = true, EnableLiveView = true, IsConnected = true;
    public bool IsLiveViewVisible => IsConnected;
    public int Warnings;
    public FakeProcessor Processor = new();
    public MaaControllerTypes CurrentController = MaaControllerTypes.Win32;
    public void OnPropertyChanged(string _) { }
    public void AddLog(string _, IBrush? __) => Warnings++;
    public Task UpdateLiveViewImageAsync(object? _) => Task.CompletedTask;
@@STATE@@
    }
    public void ChangeConnected(bool value) { IsConnected = value; OnIsConnectedChanged(value); }
    public void Tick() {
@@GUARD@@
            {
@@WARNING@@
    }
}
'''
        def make_page(name, guard, state_head, warning_fragment):
            # The production method is generated partial; the test invokes it directly.
            return (page.replace('@@NAME@@', name)
                .replace('@@STATE@@', state_head.replace('partial void OnIsConnectedChanged', 'void OnIsConnectedChanged'))
                .replace('@@GUARD@@', guard)
                .replace('@@WARNING@@', warning_fragment))

        old_page = make_page('OldPage', connecting['previous_replacements'][-1], state['find'], warning['find'])
        fixed_page = make_page('FixedPage', connecting['replace'], state['replace'], warning['replace'])
        source = r'''
using System;
using System.Threading;
using System.Threading.Tasks;
static void Check(bool value, string text) { if (!value) throw new Exception(text); }

Environment.Now = 100;
var old = new OldPage();
old.Tick();
Check(old.Warnings == 1, "baseline must reproduce warning before first frame");

var page = new FixedPage();
page.Tick();
Check(page.Warnings == 0, "a pending first frame must not warn immediately");
Environment.Now = 5099; page.Tick();
Check(page.Warnings == 0, "a short empty cache must not warn");
Environment.Now = 5100; page.Tick();
Check(page.Warnings == 1, "persistent missing frames must warn after five seconds");
Environment.Now = 8000; page.Tick();
Check(page.Warnings == 1, "one missing period must not flood warnings");
page.Processor.Buffer = new object(); Environment.Now = 8100; page.Tick();
page.Processor.Buffer = null; Environment.Now = 8200; page.Tick();
Environment.Now = 13199; page.Tick();
Check(page.Warnings == 1, "a successful frame must reset the waiting period");
Environment.Now = 13200; page.Tick();
Check(page.Warnings == 2, "a later persistent absence must warn again");
page.EnableLiveView = false; Environment.Now = 13201; page.Tick();
page.EnableLiveView = true; Environment.Now = 19000; page.Tick();
Check(page.Warnings == 2, "disabled time must not count as missing frames");
Environment.Now = 24000; page.Tick();
Check(page.Warnings == 3, "persistent absence after re-enabling must warn");
page.Processor.IsConnecting = true; Environment.Now = 24001; page.Tick();
page.Processor.IsConnecting = false; Environment.Now = 32000; page.Tick();
Check(page.Warnings == 3, "connecting time must not count as missing frames");
page.ChangeConnected(false); page.ChangeConnected(true);
Environment.Now = 38000; page.Tick();
Check(page.Warnings == 3, "reconnection must reset the waiting period");
Environment.Now = 43000; page.Tick();
Check(page.Warnings == 4, "a reconnected but still empty stream must warn");
page.IsLiveViewExpanded = false; page.Tick();
page.IsLiveViewExpanded = true; Environment.Now = 50000; page.Tick();
Check(page.Warnings == 4, "collapsed time must not count as missing frames");
Console.WriteLine("PASS missing-frame grace and lifecycle resets");

static class Environment { public static long Now; public static long TickCount64 => Now; }
interface IBrush { }
enum MaaControllerTypes { Adb, Win32 }
static class LoggerHelper { public static void Warning(string _) { } }
static class LangKeys {
    public const string LiveViewNoImageReasonAdb = "adb", LiveViewNoImageReasonWindow = "window", LiveViewNoImageWarning = "warning";
}
static class LangExtensions {
    public static string ToLocalization(this string s) => s;
    public static string ToLocalizationFormatted(this string s, bool _, string __, string ___) => s;
}
sealed class FakeProcessor {
    public bool IsConnecting;
    public object? Buffer;
    public object? GetLiveViewBuffer(bool _) => Buffer;
    public string ScreenshotType() => "FramePool";
}
@@OLD_PAGE@@
@@FIXED_PAGE@@
'''.replace('@@OLD_PAGE@@', old_page).replace('@@FIXED_PAGE@@', fixed_page)
        dotnet = os.environ.get('LIVEVIEW_DOTNET') or shutil.which('dotnet')
        self.assertTrue(dotnet, '.NET 10 SDK is required for LIVEVIEW_PATCH_TESTS')
        with tempfile.TemporaryDirectory(prefix='liveview-frame-') as temporary:
            folder = Path(temporary)
            (folder / 'Program.cs').write_text(source, encoding='utf8')
            (folder / 'Probe.csproj').write_text(
                '<Project Sdk="Microsoft.NET.Sdk"><PropertyGroup><OutputType>Exe</OutputType>'
                '<TargetFramework>net10.0</TargetFramework><Nullable>enable</Nullable>'
                '</PropertyGroup></Project>', encoding='utf8')
            result = subprocess.run([dotnet, 'run', '--project', str(folder / 'Probe.csproj'), '-c', 'Release'],
                capture_output=True, text=True, encoding='utf8', errors='replace', timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('PASS missing-frame grace and lifecycle resets', result.stdout)


if __name__ == '__main__': unittest.main()
