using System;
using System.IO;
using System.Collections.Generic;
using System.Linq;
using System.Globalization;
using System.Text.Json.Nodes;
using System.Threading;
using System.Threading.Tasks;

namespace MFAAvalonia.Services;

public enum CandleAttemptStatus { Success, Ready, Fatal, Recoverable, Cancelled }

public sealed record CandleAttempt(CandleAttemptStatus Status, string Reason = "")
{
    public static CandleAttempt Fatal(string reason) => new(CandleAttemptStatus.Fatal, reason);
}

public interface ICandleRestartLifecycle
{
    Task StopInputsAndAgentAsync(CancellationToken token);
    Task StopSelectedProcessAsync(CancellationToken token);
    Task StartSelectedProcessAsync(CancellationToken token);
    Task BindUniqueWindowAsync(CancellationToken token);
    Task ReconnectAsync(CancellationToken token);
    Task<CandleAttempt> ResumeAsync(int slot, CancellationToken token);
}

/// <summary>A recovery retry is still the same round and must not adopt a newly edited recording.</summary>
public sealed class CandleRecordingRevision
{
    public string? FileName { get; private set; }
    public string? Sha256 { get; private set; }

    public void Observe(JsonObject checkpoint)
    {
        if (checkpoint["recording_file"] == null && checkpoint["recording_sha256"] == null) return;
        var file = checkpoint["recording_file"]?.GetValue<string>();
        var hash = checkpoint["recording_sha256"]?.GetValue<string>();
        if (string.IsNullOrWhiteSpace(file) || Path.GetFileName(file) != file || file.IndexOfAny(new[] { '/', '\\', ':' }) >= 0
            || string.IsNullOrEmpty(hash) || hash.Length != 64 || !hash.All(Uri.IsHexDigit))
            throw new InvalidDataException("本轮录制版本标识无效。");
        if (FileName != null && (FileName != file || !string.Equals(Sha256, hash, StringComparison.OrdinalIgnoreCase)))
            throw new InvalidDataException("recording_changed：本轮恢复期间脚本或绑定已修改，请重新开始任务。");
        FileName = file;
        Sha256 = hash.ToLowerInvariant();
    }

    public void Apply(JsonObject parameters)
    {
        if (FileName == null) return;
        parameters["expected_recording_file"] = FileName;
        parameters["expected_recording_sha256"] = Sha256;
    }
}

public sealed record BountyCandleRecoveryOptions(bool Enabled, int MaxRetries, int SaveSlot, string ExePath)
{
    public static BountyCandleRecoveryOptions Read(JsonObject parameters)
    {
        var policy = parameters["recovery"] as JsonObject;
        var enabled = policy?["enabled"]?.GetValue<bool>() ?? false;
        var retries = ReadInputInteger(policy?["max_retries"], 3);
        var slot = ReadInputInteger(policy?["save_slot"], 1);
        var path = policy?["exe_path"]?.GetValue<string>()?.Trim() ?? "";
        if (retries is < 1 or > 3 || slot is < 1 or > 4)
            throw new InvalidDataException("宝烛恢复次数必须为1～3，存档必须为1～4。");
        if (enabled && (string.IsNullOrWhiteSpace(path) || !Path.IsPathFullyQualified(path)
            || !path.EndsWith(".exe", StringComparison.OrdinalIgnoreCase)))
            throw new InvalidDataException("启用宝烛恢复前，请填写游戏窗口所属程序的完整.EXE路径。");
        return new(enabled, retries, slot, path);
    }

    private static int ReadInputInteger(JsonNode? value, int fallback)
    {
        if (value == null) return fallback;
        if (value is JsonValue item)
        {
            if (item.TryGetValue<int>(out var numeric)) return numeric;
            if (item.TryGetValue<string>(out var text)
                && int.TryParse(text, NumberStyles.Integer, CultureInfo.InvariantCulture, out numeric)) return numeric;
        }
        throw new InvalidDataException("宝烛恢复次数和存档必须是整数。");
    }
}

public sealed record CandleProcessIdentity(int Pid, long StartedUtcTicks, string ExePath)
{
    public void RequireSame(CandleProcessIdentity current)
    {
        if (Pid != current.Pid || StartedUtcTicks != current.StartedUtcTicks
            || !string.Equals(Path.GetFullPath(ExePath), Path.GetFullPath(current.ExePath), StringComparison.OrdinalIgnoreCase))
            throw new InvalidOperationException("游戏进程身份已变化，拒绝结束或替换其它进程。");
    }

    public void RequireConfiguredPath(string configuredPath)
    {
        if (!string.Equals(Path.GetFullPath(ExePath), Path.GetFullPath(configuredPath), StringComparison.OrdinalIgnoreCase))
            throw new InvalidOperationException("恢复程序路径与所选游戏窗口所属进程不一致，请指定实际游戏EXE。");
    }
}

public readonly record struct CandleWindowSize(int Width, int Height)
{
    public void Validate()
    {
        if (Width is < 1 or > 32768 || Height is < 1 or > 32768)
            throw new InvalidOperationException("游戏窗口尺寸无效，自动恢复已停止。");
    }

    public CandleWindowSize OuterSizeFor(CandleWindowSize currentClient, CandleWindowSize currentOuter)
    {
        Validate(); currentClient.Validate(); currentOuter.Validate();
        if (currentOuter.Width < currentClient.Width || currentOuter.Height < currentClient.Height)
            throw new InvalidOperationException("游戏窗口边框尺寸无效。");
        var target = new CandleWindowSize(checked(currentOuter.Width + Width - currentClient.Width),
            checked(currentOuter.Height + Height - currentClient.Height));
        target.Validate();
        return target;
    }
}

/// <summary>One outer MFA iteration owns its recovery budget; failures never complete an iteration.</summary>
public static class BountyCandleRecoveryPolicy
{
    /// <summary>MFA serializes ordered option overrides as an array, even when Maa accepts one object.</summary>
    public static JsonObject MergePipelineOverrides(string? json)
    {
        var parsed = JsonNode.Parse(string.IsNullOrWhiteSpace(json) ? "{}" : json);
        if (parsed is JsonObject single) return (JsonObject)single.DeepClone();
        if (parsed is not JsonArray options)
            throw new InvalidDataException("宝烛任务参数必须是JSON对象或有序选项数组。");
        var result = new JsonObject();
        foreach (var option in options)
        {
            if (option is not JsonObject values)
                throw new InvalidDataException("宝烛任务的每个选项覆盖必须是JSON对象。");
            MergeObject(result, values);
        }
        return result;
    }

    private static void MergeObject(JsonObject destination, JsonObject source)
    {
        foreach (var item in source)
        {
            if (destination[item.Key] is JsonObject current && item.Value is JsonObject next)
                MergeObject(current, next);
            else
                destination[item.Key] = item.Value?.DeepClone(); // Lists such as next replace, never concatenate.
        }
    }

    public static void CompleteParameters(JsonObject parameters, BountyCandleRecoveryOptions options, bool resume)
    {
        parameters["phase"] = resume ? "recovery_begin" : "begin";
        parameters["file"] ??= "@task:bounty-candle";
        parameters["exit_mode"] ??= "recorded_return";
        parameters["recovery"] = new JsonObject
        {
            ["enabled"] = options.Enabled, ["max_retries"] = options.MaxRetries,
            ["save_slot"] = options.SaveSlot, ["exe_path"] = options.ExePath
        };
        if (resume) parameters["save_slot"] = options.SaveSlot;
    }

    public static async Task<CandleAttempt> RestartAsync(ICandleRestartLifecycle lifecycle, int slot, CancellationToken token)
    {
        token.ThrowIfCancellationRequested();
        await lifecycle.StopInputsAndAgentAsync(token);
        token.ThrowIfCancellationRequested();
        await lifecycle.StopSelectedProcessAsync(token);
        token.ThrowIfCancellationRequested();
        await lifecycle.StartSelectedProcessAsync(token);
        token.ThrowIfCancellationRequested();
        await lifecycle.BindUniqueWindowAsync(token);
        token.ThrowIfCancellationRequested();
        await lifecycle.ReconnectAsync(token);
        token.ThrowIfCancellationRequested();
        var result = await lifecycle.ResumeAsync(slot, token);
        token.ThrowIfCancellationRequested();
        return result;
    }

    public static void RequireExclusivePid(int pid, IEnumerable<int> otherRunningPids)
    {
        if (otherRunningPids.Contains(pid))
            throw new InvalidOperationException("其它运行实例也在使用该游戏进程，自动恢复已停止。");
    }

    public static (bool Found, T Window) SelectUniqueWindow<T>(IEnumerable<T> windows, Func<T, int> pid, int expectedPid)
    {
        var matching = windows.Where(window => pid(window) == expectedPid).Take(2).ToArray();
        if (matching.Length > 1)
            throw new InvalidOperationException("重启后匹配到多个游戏窗口，拒绝猜测目标。");
        return matching.Length == 1 ? (true, matching[0]) : (false, default!);
    }

    public static async Task<CandleAttempt> RunRoundAsync(
        BountyCandleRecoveryOptions options,
        Func<CancellationToken, Task<CandleAttempt>> runRound,
        Func<int, int, CancellationToken, Task<CandleAttempt>> recover,
        CancellationToken token)
    {
        token.ThrowIfCancellationRequested();
        var outcome = await runRound(token);
        var restarts = 0;
        while (outcome.Status == CandleAttemptStatus.Recoverable && options.Enabled)
        {
            token.ThrowIfCancellationRequested();
            if (outcome.Reason is not ("white_screen" or "window_lost"))
                return CandleAttempt.Fatal("不允许自动恢复的故障：" + outcome.Reason);
            if (restarts >= options.MaxRetries)
                return CandleAttempt.Fatal("本轮自动恢复次数已用尽。");
            restarts++;
            outcome = await recover(options.SaveSlot, restarts, token);
            token.ThrowIfCancellationRequested();
            if (outcome.Status == CandleAttemptStatus.Ready)
                outcome = await runRound(token);
        }
        token.ThrowIfCancellationRequested();
        return outcome;
    }

    public static CandleAttempt ReadCheckpoint(JsonObject state, string runId, string instanceId, bool resume)
    {
        if (!CheckpointBelongsTo(state, runId, instanceId))
            return CandleAttempt.Fatal("任务状态标识不匹配，未确认本轮完成。");
        var reason = state["reason"]?.GetValue<string>() ?? "";
        return state["status"]?.GetValue<string>() switch
        {
            "success" when !resume => new(CandleAttemptStatus.Success),
            "ready" when resume => new(CandleAttemptStatus.Ready),
            "recoverable" when reason is "white_screen" or "window_lost" => new(CandleAttemptStatus.Recoverable, reason),
            "cancelled" => new(CandleAttemptStatus.Cancelled, reason),
            "fatal" => CandleAttempt.Fatal(reason),
            _ => CandleAttempt.Fatal("任务没有留下可验证的成功状态。")
        };
    }

    public static bool CheckpointBelongsTo(JsonObject state, string runId, string instanceId) =>
        state["version"]?.GetValue<int>() == 1 && state["run_id"]?.GetValue<string>() == runId
        && state["instance_id"]?.GetValue<string>() == instanceId;

    public static bool IsCaptureLossCandidate(JsonObject state, string runId, string instanceId) =>
        CheckpointBelongsTo(state, runId, instanceId)
        && (state["status"]?.GetValue<string>() == "running"
            || (state["status"]?.GetValue<string>() == "fatal" && state["reason"]?.GetValue<string>() == "capture_unavailable"));
}
