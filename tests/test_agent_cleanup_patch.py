"""Verify frontend Agent cleanup respects Maa's ownership of the process object."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
PATCH = 'frontend/patches/22-agent-owned-process-cleanup.json'
RETRY_PATCH = 'frontend/patches/23-agent-retry-process-ownership.json'


class AgentCleanupPatchTests(unittest.TestCase):
    def test_cleanup_patch_is_part_of_the_frontend_build(self):
        manifest = json.loads((ROOT / 'frontend/upstream.json').read_text(encoding='utf8'))
        for path in (PATCH, RETRY_PATCH):
            self.assertIn(path, manifest['patches'])
            patch = json.loads((ROOT / path).read_text(encoding='utf8'))
            self.assertNotEqual(patch['find'], patch['replace'])

    @unittest.skipUnless(os.environ.get('AGENT_CLEANUP_PATCH_TESTS') == '1', 'opt-in .NET Agent cleanup test')
    def test_owned_exited_process_is_released_by_its_client_first(self):
        patch = json.loads((ROOT / PATCH).read_text(encoding='utf8'))
        source = r'''
using System;
using Process = FakeProcess;
static void Check(bool value, string message) { if (!value) throw new Exception(message); }

var oldProcess = new FakeProcess();
var oldClient = new FakeClient(oldProcess);
Check(Cleanup.Old(oldClient, oldProcess, true), "baseline process exits");
bool oldThrows = false;
try { oldClient.Dispose(); } catch (InvalidOperationException) { oldThrows = true; }
Check(oldThrows, "baseline must reproduce disposing a client after its owned process");

var owned = new FakeProcess();
var owner = new FakeClient(owned);
Check(Cleanup.Fixed(owner, owned, true), "finished Agent cleanup should succeed");
Check(owner.Disposed && owned.Disposed, "the owner and its exited process must both be released");
owner.Dispose();

var external = new FakeProcess();
var unrelated = new FakeProcess();
var otherClient = new FakeClient(unrelated);
Check(Cleanup.Fixed(otherClient, external, true), "unowned host process should still be released");
Check(external.Disposed && !unrelated.Disposed && !otherClient.Disposed, "do not dispose an unrelated client's process");

var standalone = new FakeProcess();
Check(Cleanup.Fixed(null, standalone, true) && standalone.Disposed, "a process without a client must be released");
var unmanaged = new FakeProcess();
var noOwnedProcess = new FakeClient(null);
Check(Cleanup.Fixed(noOwnedProcess, unmanaged, true) && unmanaged.Disposed, "an unmanaged client must preserve existing cleanup");

var timedOut = new FakeProcess();
var timedOutClient = new FakeClient(timedOut);
Check(!Cleanup.Fixed(timedOutClient, timedOut, false), "a process timeout must remain a failure");
Check(!timedOutClient.Disposed && timedOut.Disposed, "do not enter the client's potentially blocking disposal after timeout");

var failing = new FakeProcess();
var failingClient = new FakeClient(failing) { ThrowOnDispose = true };
Check(!Cleanup.Fixed(failingClient, failing, true) && failing.Disposed, "owner cleanup failure must remain a failure");
Console.WriteLine("PASS Agent process ownership and failure paths");

static class LoggerHelper { public static void Warning(string _) { } }
sealed class FakeProcess {
    public bool Disposed;
    public void Dispose() => Disposed = true;
}
sealed class FakeClient {
    readonly FakeProcess? _owned;
    public bool Disposed, ThrowOnDispose;
    public FakeClient(FakeProcess? owned) => _owned = owned;
    public FakeProcess AgentServerProcess => _owned ?? throw new InvalidOperationException("not managed");
    public void Dispose() {
        if (Disposed) return;
        if (ThrowOnDispose) throw new Exception("cleanup failed");
        if (_owned?.Disposed == true) throw new InvalidOperationException("No process is associated with this object.");
        _owned?.Dispose();
        Disposed = true;
    }
}
static class Cleanup {
    public static bool Old(FakeClient? agentClient, FakeProcess agentProcess, bool processExited) {
@@OLD@@
        return processExited;
    }
    public static bool Fixed(FakeClient? agentClient, FakeProcess agentProcess, bool processExited) {
@@NEW@@
        return processExited;
    }
}
'''.replace('@@OLD@@', patch['find']).replace('@@NEW@@', patch['replace'])
        dotnet = os.environ.get('AGENT_CLEANUP_DOTNET') or shutil.which('dotnet')
        self.assertTrue(dotnet, '.NET 10 SDK is required for AGENT_CLEANUP_PATCH_TESTS')
        with tempfile.TemporaryDirectory(prefix='agent-cleanup-') as temporary:
            folder = Path(temporary)
            (folder / 'Program.cs').write_text(source, encoding='utf8')
            (folder / 'Probe.csproj').write_text(
                '<Project Sdk="Microsoft.NET.Sdk"><PropertyGroup><OutputType>Exe</OutputType>'
                '<TargetFramework>net10.0</TargetFramework><Nullable>enable</Nullable>'
                '</PropertyGroup></Project>', encoding='utf8')
            result = subprocess.run([dotnet, 'run', '--project', str(folder / 'Probe.csproj'), '-c', 'Release'],
                capture_output=True, text=True, encoding='utf8', errors='replace', timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('PASS Agent process ownership and failure paths', result.stdout)

    @unittest.skipUnless(os.environ.get('AGENT_CLEANUP_PATCH_TESTS') == '1', 'opt-in .NET Agent cleanup test')
    def test_retry_preserves_owned_process_and_ignores_stale_exit(self):
        patch = json.loads((ROOT / RETRY_PATCH).read_text(encoding='utf8'))

        def fragment(text, start, end):
            first = text.index(start)
            return text[first:text.index(end, first)]

        old_retry = fragment(patch['find'], '                    if (ctx.Process != null && !ctx.Process.HasExited)', '\n                }\n\n')
        new_retry = fragment(patch['replace'], '                    Process? previousProcess;', '\n                }\n\n')
        old_exit = fragment(patch['find'], '                ctx.Process.Exited +=', '\n\n                BindProcessLifetime')
        new_exit = fragment(patch['replace'], '                startedProcess.Exited +=', '\n\n                BindProcessLifetime')
        source = r'''
using System;
using Process = FakeProcess;
static void Check(bool value, string message) { if (!value) throw new Exception(message); }

var oldProcess = new FakeProcess();
var oldContext = new Context { Process = oldProcess };
Retry.Old(oldContext);
bool oldThrows = false;
try { new FakeClient(oldProcess).LinkStart(); } catch (InvalidOperationException) { oldThrows = true; }
Check(oldThrows, "baseline must reproduce the retry failure after host disposal");

var previous = new FakeProcess();
var context = new Context { Process = previous };
Retry.Fixed(context);
var client = new FakeClient(previous);
client.LinkStart();
Check(client.Started && previous.Disposed && context.Process == null, "client must be able to dispose its old process and retry");

var slow = new FakeProcess { ExitsOnWait = false };
var timeoutContext = new Context { Process = slow };
bool timedOut = false;
try { Retry.Fixed(timeoutContext); } catch (TimeoutException) { timedOut = true; }
Check(timedOut && !slow.Disposed && ReferenceEquals(timeoutContext.Process, slow), "timeout must not pretend the old process exited");

var oldCallbackProcess = new FakeProcess();
var oldCallbackContext = new Context { Process = oldCallbackProcess };
Retry.AttachOld(oldCallbackContext, oldCallbackProcess);
oldCallbackContext.Process = new FakeProcess();
oldCallbackProcess.FireExit();
Check(oldCallbackContext.Process == null && oldCallbackContext.StreamStops == 1, "baseline must reproduce stale exit clearing the replacement");

var stale = new FakeProcess();
var replacement = new FakeProcess();
var callbackContext = new Context { Process = stale };
Retry.AttachFixed(callbackContext, stale);
callbackContext.Process = replacement;
stale.FireExit();
Check(ReferenceEquals(callbackContext.Process, replacement) && callbackContext.StreamStops == 0, "stale exit must not clear the replacement or its streams");
Retry.AttachFixed(callbackContext, replacement);
replacement.FireExit();
Check(callbackContext.Process == null && callbackContext.StreamStops == 1, "current exit must still release its streams");
Console.WriteLine("PASS Agent retry and stale-exit ownership");

sealed class Context { public FakeProcess? Process; public object ReadLock = new(); public int StreamStops; }
sealed class FakeProcess {
    public bool Disposed, ExitsOnWait = true, ExitedFlag;
    public bool HasExited => !Disposed ? ExitedFlag : throw new InvalidOperationException("disposed");
    public event EventHandler? Exited;
    public void Kill(bool _) { }
    public bool WaitForExit(int _) { if (ExitsOnWait) ExitedFlag = true; return ExitsOnWait; }
    public void Dispose() => Disposed = true;
    public void FireExit() => Exited?.Invoke(this, EventArgs.Empty);
}
sealed class FakeClient {
    readonly FakeProcess _owned;
    public bool Started;
    public FakeClient(FakeProcess owned) => _owned = owned;
    public void LinkStart() { if (_owned.HasExited) { _owned.Dispose(); Started = true; } }
}
static class LoggerHelper { public static void Info(string _) { } public static void Warning(string _) { } }
static class Retry {
    static void StopReadStreams(Context ctx) => ctx.StreamStops++;
    public static void Old(Context ctx) {
@@OLD_RETRY@@
    }
    public static void Fixed(Context ctx) {
@@NEW_RETRY@@
    }
    public static void AttachOld(Context ctx, FakeProcess startedProcess) {
@@OLD_EXIT@@
    }
    public static void AttachFixed(Context ctx, FakeProcess startedProcess) {
@@NEW_EXIT@@
    }
}
'''
        for key, value in (('@@OLD_RETRY@@', old_retry), ('@@NEW_RETRY@@', new_retry),
                           ('@@OLD_EXIT@@', old_exit), ('@@NEW_EXIT@@', new_exit)):
            source = source.replace(key, value)
        dotnet = os.environ.get('AGENT_CLEANUP_DOTNET') or shutil.which('dotnet')
        self.assertTrue(dotnet, '.NET 10 SDK is required for AGENT_CLEANUP_PATCH_TESTS')
        with tempfile.TemporaryDirectory(prefix='agent-retry-') as temporary:
            folder = Path(temporary)
            (folder / 'Program.cs').write_text(source, encoding='utf8')
            (folder / 'Probe.csproj').write_text(
                '<Project Sdk="Microsoft.NET.Sdk"><PropertyGroup><OutputType>Exe</OutputType>'
                '<TargetFramework>net10.0</TargetFramework><Nullable>enable</Nullable>'
                '</PropertyGroup></Project>', encoding='utf8')
            result = subprocess.run([dotnet, 'run', '--project', str(folder / 'Probe.csproj'), '-c', 'Release'],
                capture_output=True, text=True, encoding='utf8', errors='replace', timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('PASS Agent retry and stale-exit ownership', result.stdout)


if __name__ == '__main__':
    unittest.main()
