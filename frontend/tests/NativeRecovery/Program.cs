using System.Text.Json.Nodes;
using MFAAvalonia.Services;

var tests = new List<(string Name, Func<Task> Run)>();
var enabled = new BountyCandleRecoveryOptions(true, 3, 1, Path.GetFullPath("game.exe"));
CandleAttempt Good() => new(CandleAttemptStatus.Success);
CandleAttempt White() => new(CandleAttemptStatus.Recoverable, "white_screen");
CandleAttempt Ready() => new(CandleAttemptStatus.Ready);
void Require(bool value) { if (!value) throw new Exception("Assertion failed"); }
async Task Throws<T>(Func<Task> action) where T : Exception
{
    try { await action(); }
    catch (T) { return; }
    throw new Exception("Expected " + typeof(T).Name);
}
Task<CandleAttempt> NoRecovery(int slot, int attempt, CancellationToken token) => throw new Exception("Recovery must not run");
void Test(string name, Func<Task> run) => tests.Add((name, run));
void Sync(string name, Action action) => Test(name, () => { action(); return Task.CompletedTask; });

Test("success completes exactly one round", async () =>
{
    var calls = 0;
    var result = await BountyCandleRecoveryPolicy.RunRoundAsync(enabled, _ => { calls++; return Task.FromResult(Good()); }, NoRecovery, default);
    Require(result.Status == CandleAttemptStatus.Success && calls == 1);
});
Test("disabled recovery stops after first failure", async () =>
{
    var result = await BountyCandleRecoveryPolicy.RunRoundAsync(enabled with { Enabled = false }, _ => Task.FromResult(White()), NoRecovery, default);
    Require(result.Status == CandleAttemptStatus.Recoverable);
});
Test("fatal and unknown failures never restart", async () =>
{
    foreach (var outcome in new[] { CandleAttempt.Fatal("unaccepted"), new CandleAttempt(CandleAttemptStatus.Recoverable, "unknown_screen") })
    {
        var result = await BountyCandleRecoveryPolicy.RunRoundAsync(enabled, _ => Task.FromResult(outcome), NoRecovery, default);
        Require(result.Status == CandleAttemptStatus.Fatal);
    }
});
Test("recovery retries same round without extra completion", async () =>
{
    var runs = 0; var restarts = 0;
    var result = await BountyCandleRecoveryPolicy.RunRoundAsync(enabled,
        _ => Task.FromResult(++runs == 1 ? White() : Good()),
        (slot, attempt, _) => { Require(slot == 1 && attempt == 1); restarts++; return Task.FromResult(Ready()); }, default);
    Require(result.Status == CandleAttemptStatus.Success && runs == 2 && restarts == 1);
});
Test("three restart budget is enforced", async () =>
{
    var runs = 0; var restarts = 0;
    var result = await BountyCandleRecoveryPolicy.RunRoundAsync(enabled,
        _ => { runs++; return Task.FromResult(White()); },
        (_, attempt, _) => { Require(attempt == ++restarts); return Task.FromResult(Ready()); }, default);
    Require(result.Status == CandleAttemptStatus.Fatal && runs == 4 && restarts == 3);
});
Test("success resets the next MFA round budget", async () =>
{
    var restarts = 0;
    for (var round = 0; round < 2; round++)
    {
        var runs = 0;
        var result = await BountyCandleRecoveryPolicy.RunRoundAsync(enabled,
            _ => Task.FromResult(++runs < 4 ? White() : Good()),
            (_, attempt, _) => { Require(attempt is >= 1 and <= 3); restarts++; return Task.FromResult(Ready()); }, default);
        Require(result.Status == CandleAttemptStatus.Success);
    }
    Require(restarts == 6);
});
Test("cancellation before task prevents all work", async () =>
{
    using var cancel = new CancellationTokenSource(); cancel.Cancel();
    await Throws<OperationCanceledException>(() => BountyCandleRecoveryPolicy.RunRoundAsync(enabled,
        _ => throw new Exception("Task must not run"), NoRecovery, cancel.Token));
});
Test("stop during failed task prevents restart", async () =>
{
    using var cancel = new CancellationTokenSource();
    await Throws<OperationCanceledException>(() => BountyCandleRecoveryPolicy.RunRoundAsync(enabled,
        _ => { cancel.Cancel(); return Task.FromResult(White()); }, NoRecovery, cancel.Token));
});
Test("stop during restart prevents retry input", async () =>
{
    using var cancel = new CancellationTokenSource(); var calls = 0;
    await Throws<OperationCanceledException>(() => BountyCandleRecoveryPolicy.RunRoundAsync(enabled,
        _ => { calls++; return Task.FromResult(White()); },
        (_, _, _) => { cancel.Cancel(); return Task.FromResult(Ready()); }, cancel.Token));
    Require(calls == 1);
});
Test("startup failure ends recovery without another round", async () =>
{
    var calls = 0;
    await Throws<IOException>(() => BountyCandleRecoveryPolicy.RunRoundAsync(enabled,
        _ => { calls++; return Task.FromResult(White()); },
        (_, _, _) => throw new IOException("start failed"), default));
    Require(calls == 1);
});
Test("login fatal stops without replaying round", async () =>
{
    var calls = 0;
    var result = await BountyCandleRecoveryPolicy.RunRoundAsync(enabled,
        _ => { calls++; return Task.FromResult(White()); },
        (_, _, _) => Task.FromResult(CandleAttempt.Fatal("captcha")), default);
    Require(result.Status == CandleAttemptStatus.Fatal && calls == 1);
});
Test("white screen during login consumes remaining restart budget", async () =>
{
    var calls = 0; var retries = 0;
    var result = await BountyCandleRecoveryPolicy.RunRoundAsync(enabled,
        _ => { calls++; return Task.FromResult(White()); },
        (_, _, _) => { retries++; return Task.FromResult(White()); }, default);
    Require(result.Status == CandleAttemptStatus.Fatal && retries == 3 && calls == 1);
});
Sync("stale and cross-instance checkpoints are rejected", () =>
{
    JsonObject State(string run, string instance, string status) => new() { ["version"] = 1, ["run_id"] = run, ["instance_id"] = instance, ["status"] = status };
    Require(BountyCandleRecoveryPolicy.ReadCheckpoint(State("old", "a", "success"), "new", "a", false).Status == CandleAttemptStatus.Fatal);
    Require(BountyCandleRecoveryPolicy.ReadCheckpoint(State("new", "b", "success"), "new", "a", false).Status == CandleAttemptStatus.Fatal);
    Require(BountyCandleRecoveryPolicy.ReadCheckpoint(State("new", "a", "running"), "new", "a", false).Status == CandleAttemptStatus.Fatal);
    Require(BountyCandleRecoveryPolicy.ReadCheckpoint(State("new", "a", "ready"), "new", "a", false).Status == CandleAttemptStatus.Fatal);
    Require(BountyCandleRecoveryPolicy.ReadCheckpoint(State("new", "a", "success"), "new", "a", true).Status == CandleAttemptStatus.Fatal);
    Require(BountyCandleRecoveryPolicy.ReadCheckpoint(State("new", "a", "ready"), "new", "a", true).Status == CandleAttemptStatus.Ready);
});
Sync("only the selected PID and start identity can be ended", () =>
{
    var expected = new CandleProcessIdentity(12, 100, Path.GetFullPath("game.exe"));
    expected.RequireSame(expected);
    foreach (var wrong in new[] { expected with { Pid = 13 }, expected with { StartedUtcTicks = 101 }, expected with { ExePath = Path.GetFullPath("other.exe") } })
    {
        try { expected.RequireSame(wrong); throw new Exception("Identity should fail"); }
        catch (InvalidOperationException) { }
    }
});
Sync("configured executable cannot silently target a launcher", () =>
{
    var expected = new CandleProcessIdentity(12, 100, Path.GetFullPath("game.exe"));
    try { expected.RequireConfiguredPath(Path.GetFullPath("launcher.exe")); throw new Exception("Path should fail"); }
    catch (InvalidOperationException) { }
});
Sync("other running instance sharing the PID blocks restart", () =>
{
    BountyCandleRecoveryPolicy.RequireExclusivePid(12, new[] { 13, 14 });
    try { BountyCandleRecoveryPolicy.RequireExclusivePid(12, new[] { 12, 14 }); throw new Exception("Shared process should fail"); }
    catch (InvalidOperationException) { }
});
Sync("old process windows are rejected and new ambiguity stops", () =>
{
    Require(!BountyCandleRecoveryPolicy.SelectUniqueWindow(new[] { (Pid: 1, Handle: 11) }, w => w.Pid, 2).Found);
    var selected = BountyCandleRecoveryPolicy.SelectUniqueWindow(new[] { (Pid: 1, Handle: 11), (Pid: 2, Handle: 21) }, w => w.Pid, 2);
    Require(selected.Found && selected.Window.Handle == 21);
    try { BountyCandleRecoveryPolicy.SelectUniqueWindow(new[] { (Pid: 2, Handle: 21), (Pid: 2, Handle: 22) }, w => w.Pid, 2); throw new Exception("Ambiguity should fail"); }
    catch (InvalidOperationException) { }
});
Sync("policy rejects unreviewable paths and invalid limits", () =>
{
    foreach (var invalid in new[]
    {
        "{\"enabled\":true,\"exe_path\":\"game.exe\"}",
        "{\"enabled\":true,\"exe_path\":\"C:/game.cmd\"}",
        "{\"max_retries\":4}", "{\"save_slot\":0}"
    })
    {
        try { BountyCandleRecoveryOptions.Read(new() { ["recovery"] = JsonNode.Parse(invalid) }); throw new Exception("Policy should fail"); }
        catch (InvalidDataException) { }
    }
    Require(!BountyCandleRecoveryOptions.Read(new()).Enabled);
});
Sync("complete native parameters survive object replacement semantics", () =>
{
    var parameters = new JsonObject { ["file"] = "chosen.json", ["exit_mode"] = "battle_only" };
    BountyCandleRecoveryPolicy.CompleteParameters(parameters, enabled, false);
    parameters["run_id"] = "nonce";
    Require(parameters["phase"]!.GetValue<string>() == "begin");
    Require(parameters["file"]!.GetValue<string>() == "chosen.json" && parameters["exit_mode"]!.GetValue<string>() == "battle_only");
    Require(parameters["recovery"]!["enabled"]!.GetValue<bool>());
    var defaults = new JsonObject();
    BountyCandleRecoveryPolicy.CompleteParameters(defaults, enabled with { SaveSlot = 4 }, true);
    Require(defaults["phase"]!.GetValue<string>() == "recovery_begin");
    Require(defaults["file"]!.GetValue<string>() == "@task:bounty-candle");
    Require(defaults["exit_mode"]!.GetValue<string>() == "recorded_return");
    Require(defaults["save_slot"]!.GetValue<int>() == 4);
});
Sync("actual MaaToken ordered option array keeps file mode and enabled recovery inputs", () =>
{
    const string entry = "悬赏（守卫宝烛）-初始化";
    JsonObject Override(JsonObject parameters) => new()
    {
        [entry] = new JsonObject { ["action"] = new JsonObject { ["param"] = new JsonObject { ["custom_action_param"] = parameters } } }
    };
    var serialized = new JsonArray
    {
        Override(new() { ["file"] = "@selected:strict" }),
        Override(new() { ["exit_mode"] = "battle_only" }),
        Override(new() { ["recovery"] = new JsonObject { ["enabled"] = true } }),
        Override(new() { ["recovery"] = new JsonObject { ["exe_path"] = enabled.ExePath } }),
        Override(new() { ["recovery"] = new JsonObject { ["save_slot"] = "4", ["max_retries"] = "2" } })
    }.ToJsonString();
    var merged = BountyCandleRecoveryPolicy.MergePipelineOverrides(serialized);
    var parameters = merged[entry]!["action"]!["param"]!["custom_action_param"]!.AsObject();
    var options = BountyCandleRecoveryOptions.Read(parameters);
    BountyCandleRecoveryPolicy.CompleteParameters(parameters, options, false);
    Require(parameters["file"]!.GetValue<string>() == "@selected:strict");
    Require(parameters["exit_mode"]!.GetValue<string>() == "battle_only");
    Require(parameters["phase"]!.GetValue<string>() == "begin");
    Require(options.Enabled && options.SaveSlot == 4 && options.MaxRetries == 2 && options.ExePath == enabled.ExePath);
    Require(merged[entry]!["action"]!["param"]!["custom_action_param"]!["recovery"]!["enabled"]!.GetValue<bool>());
});
Sync("option merge respects order and replaces next lists", () =>
{
    var result = BountyCandleRecoveryPolicy.MergePipelineOverrides("[{\"node\":{\"next\":[\"old\"],\"flag\":1}},{\"node\":{\"next\":[\"new\"],\"flag\":2}}]");
    Require(result["node"]!["flag"]!.GetValue<int>() == 2);
    Require(result["node"]!["next"]!.AsArray().Count == 1 && result["node"]!["next"]![0]!.GetValue<string>() == "new");
    Require(BountyCandleRecoveryPolicy.MergePipelineOverrides("[]").Count == 0);
    Require(BountyCandleRecoveryPolicy.MergePipelineOverrides("{}").Count == 0);
    try { BountyCandleRecoveryPolicy.MergePipelineOverrides("[17]"); throw new Exception("Invalid option should fail"); }
    catch (InvalidDataException) { }
});
Sync("same round pins script filename and bytes across automatic recovery", () =>
{
    var first = new JsonObject { ["recording_file"] = "守卫宝烛.json", ["recording_sha256"] = new string('a', 64) };
    var revision = new CandleRecordingRevision();
    revision.Observe(first);
    var retry = new JsonObject(); revision.Apply(retry);
    Require(retry["expected_recording_file"]!.GetValue<string>() == "守卫宝烛.json");
    Require(retry["expected_recording_sha256"]!.GetValue<string>() == new string('a', 64));
    revision.Observe(new()); // Login ready has no new recording and cannot clear the pin.
    foreach (var changed in new[]
    {
        new JsonObject { ["recording_file"] = "other.json", ["recording_sha256"] = new string('a', 64) },
        new JsonObject { ["recording_file"] = "守卫宝烛.json", ["recording_sha256"] = new string('b', 64) }
    })
    {
        try { revision.Observe(changed); throw new Exception("Changed script should stop recovery"); }
        catch (InvalidDataException) { }
    }
    Require(revision.Sha256 == new string('a', 64));
});
Sync("next MFA round can adopt a newly saved script revision", () =>
{
    var next = new CandleRecordingRevision();
    next.Observe(new() { ["recording_file"] = "new.json", ["recording_sha256"] = new string('b', 64) });
    Require(next.FileName == "new.json" && next.Sha256 == new string('b', 64));
});
Sync("only matching capture failures may be checked for positive window loss", () =>
{
    JsonObject State(string status, string reason, string run = "new") => new()
    {
        ["version"] = 1, ["run_id"] = run, ["instance_id"] = "default", ["status"] = status, ["reason"] = reason
    };
    Require(BountyCandleRecoveryPolicy.IsCaptureLossCandidate(State("running", ""), "new", "default"));
    Require(BountyCandleRecoveryPolicy.IsCaptureLossCandidate(State("fatal", "capture_unavailable"), "new", "default"));
    foreach (var state in new[] { State("fatal", "unknown_screen"), State("fatal", "recording_changed"),
        State("fatal", "not_accepted"), State("cancelled", "capture_unavailable"), State("running", "", "old") })
        Require(!BountyCandleRecoveryPolicy.IsCaptureLossCandidate(state, "new", "default"));
});
Sync("client restoration accounts for borders without changing recording scale", () =>
{
    var original = new CandleWindowSize(1175, 737);
    var target = original.OuterSizeFor(new(1000, 600), new(1016, 639));
    Require(target == new CandleWindowSize(1191, 776));
    Require(original.OuterSizeFor(original, new(1191, 776)) == new CandleWindowSize(1191, 776));
    foreach (var invalid in new[] { new CandleWindowSize(0, 720), new CandleWindowSize(720, -1), new CandleWindowSize(40000, 720) })
    {
        try { invalid.OuterSizeFor(new(1000, 600), new(1016, 639)); throw new Exception("Invalid size should stop recovery"); }
        catch (InvalidOperationException) { }
    }
    try { original.OuterSizeFor(new(1000, 600), new(900, 639)); throw new Exception("Invalid border should stop recovery"); }
    catch (InvalidOperationException) { }
});

Test("old native input and Agent finish before process termination", async () =>
{
    var lifecycle = new FakeLifecycle();
    var result = await BountyCandleRecoveryPolicy.RestartAsync(lifecycle, 3, default);
    Require(result.Status == CandleAttemptStatus.Ready && lifecycle.Slot == 3);
    Require(string.Join(",", lifecycle.Calls) == "release,kill,start,bind,resize,connect,login");
});
Test("failed native input cleanup never kills the game", async () =>
{
    var lifecycle = new FakeLifecycle { FailAt = "release" };
    await Throws<IOException>(() => BountyCandleRecoveryPolicy.RestartAsync(lifecycle, 1, default));
    Require(string.Join(",", lifecycle.Calls) == "release");
});
Test("cancellation at every lifecycle phase prevents subsequent work", async () =>
{
    var phases = new[] { "release", "kill", "start", "bind", "resize", "connect", "login" };
    for (var i = 0; i < phases.Length; i++)
    {
        using var cancel = new CancellationTokenSource();
        var lifecycle = new FakeLifecycle { CancelAfter = phases[i], Cancellation = cancel };
        await Throws<OperationCanceledException>(() => BountyCandleRecoveryPolicy.RestartAsync(lifecycle, 1, cancel.Token));
        Require(lifecycle.Calls.SequenceEqual(phases.Take(i + 1)));
    }
});
Test("window ambiguity prevents reconnect and login input", async () =>
{
    var lifecycle = new FakeLifecycle { FailAt = "bind" };
    await Throws<IOException>(() => BountyCandleRecoveryPolicy.RestartAsync(lifecycle, 1, default));
    Require(string.Join(",", lifecycle.Calls) == "release,kill,start,bind");
});
Test("reconnect failure never attempts login", async () =>
{
    var lifecycle = new FakeLifecycle { FailAt = "connect" };
    await Throws<IOException>(() => BountyCandleRecoveryPolicy.RestartAsync(lifecycle, 1, default));
    Require(string.Join(",", lifecycle.Calls) == "release,kill,start,bind,resize,connect");
});

var passed = 0;
foreach (var test in tests)
{
    try { await test.Run(); Console.WriteLine("PASS " + test.Name); passed++; }
    catch (Exception error) { Console.Error.WriteLine("FAIL " + test.Name + ": " + error); Environment.ExitCode = 1; }
}
Console.WriteLine($"{passed}/{tests.Count} recovery checks passed; no processes or input devices were used.");

sealed class FakeLifecycle : ICandleRestartLifecycle
{
    public List<string> Calls { get; } = new();
    public string FailAt { get; init; } = "";
    public string CancelAfter { get; init; } = "";
    public CancellationTokenSource? Cancellation { get; init; }
    public int Slot { get; private set; }
    private Task Phase(string name)
    {
        Calls.Add(name);
        if (FailAt == name) throw new IOException(name + " failed");
        if (CancelAfter == name) Cancellation!.Cancel();
        return Task.CompletedTask;
    }
    public Task StopInputsAndAgentAsync(CancellationToken token) => Phase("release");
    public Task StopSelectedProcessAsync(CancellationToken token) => Phase("kill");
    public Task StartSelectedProcessAsync(CancellationToken token) => Phase("start");
    public async Task BindUniqueWindowAsync(CancellationToken token)
    {
        await Phase("bind");
        token.ThrowIfCancellationRequested();
        await Phase("resize");
    }
    public Task ReconnectAsync(CancellationToken token) => Phase("connect");
    public async Task<CandleAttempt> ResumeAsync(int slot, CancellationToken token)
    {
        Slot = slot;
        await Phase("login");
        return new(CandleAttemptStatus.Ready);
    }
}
