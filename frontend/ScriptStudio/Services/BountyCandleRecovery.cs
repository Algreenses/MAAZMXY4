using System;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Runtime.InteropServices;
using System.Text.Json.Nodes;
using System.Text.RegularExpressions;
using System.Threading;
using System.Threading.Tasks;
using MaaFramework.Binding;
using MFAAvalonia.Extensions.MaaFW;
using MFAAvalonia.Helper;

namespace MFAAvalonia.Services;

/// <summary>Recover a single native candle round without replacing the MFA queue or its progress.</summary>
public static class BountyCandleRecovery
{
    public const string Entry = "悬赏（守卫宝烛）-初始化";
    public const string ResumeEntry = "悬赏（守卫宝烛）-恢复登录";
    public static bool IsNativeTask(string? entry) => entry == Entry;

    public static async Task<MaaJobStatus> RunAsync(MaaProcessor processor, string? entry, string? overrides,
        Func<string?, string?, CancellationToken, Task<MaaJobStatus>> execute, CancellationToken token)
    {
        if (!IsNativeTask(entry)) return await execute(entry, overrides, token);
        try
        {
            var pipeline = BountyCandleRecoveryPolicy.MergePipelineOverrides(overrides);
            var parameters = InitializerParameters(pipeline, Entry);
            var options = BountyCandleRecoveryOptions.Read(parameters);
            BountyCandleRecoveryPolicy.CompleteParameters(parameters, options, false);
            var session = new RecoverySession(processor, pipeline, options, execute);
            if (options.Enabled) session.CaptureTarget();
            var outcome = await BountyCandleRecoveryPolicy.RunRoundAsync(options,
                ct => session.RunEntryAsync(Entry, false, ct), session.RecoverAsync, token);
            if (outcome.Status == CandleAttemptStatus.Cancelled)
                throw new OperationCanceledException(outcome.Reason, token);
            if (outcome.Status == CandleAttemptStatus.Success) return MaaJobStatus.Succeeded;
            processor.AddLog("守卫宝烛已停止：" + outcome.Reason);
            return MaaJobStatus.Failed;
        }
        catch (OperationCanceledException) { throw; }
        catch (Exception error)
        {
            processor.AddLog("守卫宝烛恢复未执行：" + error.Message);
            LoggerHelper.Warning("守卫宝烛原生恢复停止：" + error.Message);
            return MaaJobStatus.Failed;
        }
    }

    private static JsonObject InitializerParameters(JsonObject pipeline, string nodeName)
    {
        if (pipeline[nodeName] is not JsonObject node) pipeline[nodeName] = node = new();
        if (node["action"] is not JsonObject action) node["action"] = action = new();
        if (action["param"] is not JsonObject param) action["param"] = param = new();
        if (param["custom_action_param"] is not JsonObject values) param["custom_action_param"] = values = new();
        return values;
    }

    private sealed class RecoverySession(MaaProcessor processor, JsonObject pipeline,
        BountyCandleRecoveryOptions options, Func<string?, string?, CancellationToken, Task<MaaJobStatus>> execute) : ICandleRestartLifecycle
    {
        private CandleProcessIdentity? _target;
        private IntPtr _window;
        private readonly CandleRecordingRevision _revision = new();
        private CandleWindowSize _clientSize;

        public void CaptureTarget()
        {
            if (!OperatingSystem.IsWindows() || processor.ViewModel?.CurrentController != MaaControllerTypes.Win32
                || processor.ViewModel.CurrentDevice is not DesktopWindowInfo selected || selected.Handle == IntPtr.Zero)
                throw new InvalidOperationException("自动恢复仅支持明确选择的Win32游戏窗口。");
            if (!File.Exists(options.ExePath)) throw new FileNotFoundException("游戏EXE不存在。", options.ExePath);
            _window = selected.Handle;
            var pid = WindowPid(_window);
            _target = Identity(pid);
            _target.RequireConfiguredPath(options.ExePath);
            EnsureUnshared(pid);
            _clientSize = ClientSize(_window);
            _clientSize.Validate();
        }

        public async Task<CandleAttempt> RunEntryAsync(string entry, bool resume, CancellationToken token)
        {
            token.ThrowIfCancellationRequested();
            var runId = Guid.NewGuid().ToString();
            var directory = Path.Combine(AppPaths.DataRoot, "debug", "bounty-candle");
            Directory.CreateDirectory(directory);
            var statusPath = Path.Combine(directory, runId + ".json");
            var copy = (JsonObject)pipeline.DeepClone();
            var parameters = InitializerParameters(copy, entry);
            // Maa replaces custom_action_param as a complete value. Explicitly
            // carry the full phase configuration rather than relying on the
            // resource node's parameters being merged by the native runtime.
            if (resume)
            {
                var roundParameters = InitializerParameters(copy, Entry);
                foreach (var item in roundParameters)
                    if (!parameters.ContainsKey(item.Key)) parameters[item.Key] = item.Value?.DeepClone();
            }
            BountyCandleRecoveryPolicy.CompleteParameters(parameters, options, resume);
            parameters["run_id"] = runId;
            parameters["instance_id"] = processor.InstanceId;
            parameters["status_path"] = statusPath;
            _revision.Apply(parameters);
            MaaJobStatus status;
            try { status = await execute(entry, copy.ToJsonString(), token); }
            catch (OperationCanceledException) { throw; }
            catch (Exception error)
            {
                token.ThrowIfCancellationRequested();
                return IsLostTarget() ? new(CandleAttemptStatus.Recoverable, "window_lost") : CandleAttempt.Fatal(error.Message);
            }
            token.ThrowIfCancellationRequested();
            if (!File.Exists(statusPath))
                return IsLostTarget() ? new(CandleAttemptStatus.Recoverable, "window_lost") : CandleAttempt.Fatal("缺少本轮确认状态。");
            if (new FileInfo(statusPath).Length > 65536) return CandleAttempt.Fatal("本轮状态文件无效。");
            var checkpoint = JsonNode.Parse(await File.ReadAllTextAsync(statusPath, token)) as JsonObject;
            if (checkpoint == null) return CandleAttempt.Fatal("本轮状态格式无效。");
            var result = BountyCandleRecoveryPolicy.ReadCheckpoint(checkpoint, runId, processor.InstanceId, resume);
            if (BountyCandleRecoveryPolicy.CheckpointBelongsTo(checkpoint, runId, processor.InstanceId))
                _revision.Observe(checkpoint);
            if ((result.Status is CandleAttemptStatus.Success or CandleAttemptStatus.Ready) && status != MaaJobStatus.Succeeded)
                return CandleAttempt.Fatal("原生任务未成功结束，未计入完成次数。");
            if (result.Status == CandleAttemptStatus.Recoverable && result.Reason == "window_lost" && !IsLostTarget())
                return CandleAttempt.Fatal("目标窗口仍然有效，未确认窗口丢失。");
            if (result.Status == CandleAttemptStatus.Fatal
                && BountyCandleRecoveryPolicy.IsCaptureLossCandidate(checkpoint, runId, processor.InstanceId)
                && IsLostTarget())
                return new(CandleAttemptStatus.Recoverable, "window_lost");
            return result;
        }

        public async Task<CandleAttempt> RecoverAsync(int slot, int attempt, CancellationToken token)
        {
            token.ThrowIfCancellationRequested();
            if (_target == null) return CandleAttempt.Fatal("没有经过校验的恢复目标。");
            processor.AddLog($"守卫宝烛：本轮自动恢复 {attempt}/{options.MaxRetries}，已完成轮数保持不变。");
            return await BountyCandleRecoveryPolicy.RestartAsync(this, slot, token);
        }

        public Task StopInputsAndAgentAsync(CancellationToken token) =>
            Task.Run(() => processor.SetTasker(requireAllStopped: true), token);

        public Task StopSelectedProcessAsync(CancellationToken token) =>
            StopSelectedProcessAsync(_target ?? throw new InvalidOperationException("没有经过校验的恢复目标。"), token);

        public async Task StartSelectedProcessAsync(CancellationToken token)
        {
            token.ThrowIfCancellationRequested();
            using var started = Process.Start(new ProcessStartInfo(options.ExePath)
            {
                WorkingDirectory = Path.GetDirectoryName(options.ExePath)!, UseShellExecute = false,
                CreateNoWindow = false, WindowStyle = ProcessWindowStyle.Normal
            }) ?? throw new InvalidOperationException("游戏进程未启动。");
            var until = DateTime.UtcNow + TimeSpan.FromSeconds(5);
            while (DateTime.UtcNow < until)
            {
                token.ThrowIfCancellationRequested();
                if (started.HasExited) throw new InvalidOperationException("游戏进程启动后立即结束。");
                try
                {
                    _target = Identity(started.Id);
                    _target.RequireConfiguredPath(options.ExePath);
                    return;
                }
                catch (System.ComponentModel.Win32Exception)
                {
                    // Windows may not expose MainModule until loader initialization completes.
                    await Task.Delay(100, token);
                }
            }
            throw new InvalidOperationException("无法核验新启动的游戏进程身份。");
        }

        public async Task BindUniqueWindowAsync(CancellationToken token)
        {
            var pid = _target?.Pid ?? throw new InvalidOperationException("没有重启的目标进程。");
            var window = await FindNewWindowAsync(pid, token);
            token.ThrowIfCancellationRequested();
            _window = window.Handle;
            EnsureUnshared(pid);
            await RestoreClientSizeAsync(window.Handle, pid, token);
            await DispatcherHelper.RunOnMainThreadAsync(() => processor.ViewModel!.BindWin32RecoveryWindow(window));
        }

        private async Task RestoreClientSizeAsync(IntPtr window, int pid, CancellationToken token)
        {
            token.ThrowIfCancellationRequested();
            _target!.RequireSame(Identity(pid));
            if (WindowPid(window) != pid) throw new InvalidOperationException("新游戏窗口身份已改变。");
            var current = ClientSize(window);
            if (current == _clientSize) return;
            if (!GetWindowRect(window, out var rectangle)) throw new InvalidOperationException("无法读取新游戏窗口边框尺寸。");
            var outer = new CandleWindowSize(rectangle.Right - rectangle.Left, rectangle.Bottom - rectangle.Top);
            var target = _clientSize.OuterSizeFor(current, outer);
            token.ThrowIfCancellationRequested();
            // Only resize the uniquely verified new game HWND. Keep position,
            // z-order and focus unchanged; never stretch the recorded coordinates.
            if (!SetWindowPos(window, IntPtr.Zero, 0, 0, target.Width, target.Height, 0x0002 | 0x0004 | 0x0010))
                throw new InvalidOperationException("无法恢复原游戏客户区尺寸。");
            var until = DateTime.UtcNow + TimeSpan.FromSeconds(2);
            while (DateTime.UtcNow < until)
            {
                token.ThrowIfCancellationRequested();
                if (WindowPid(window) != pid) throw new InvalidOperationException("调整尺寸时游戏窗口已改变。");
                if (ClientSize(window) == _clientSize) return;
                await Task.Delay(50, token);
            }
            throw new InvalidOperationException("新游戏窗口未恢复原客户区尺寸，已停止以防操作偏移。");
        }

        public async Task ReconnectAsync(CancellationToken token)
        {
            token.ThrowIfCancellationRequested();
            var tasker = await processor.GetTaskerAsync(token);
            token.ThrowIfCancellationRequested();
            if (tasker == null)
                throw new InvalidOperationException("游戏窗口重新连接失败。");
        }

        public Task<CandleAttempt> ResumeAsync(int slot, CancellationToken token) => RunEntryAsync(ResumeEntry, true, token);

        private async Task StopSelectedProcessAsync(CandleProcessIdentity expected, CancellationToken token)
        {
            Process? selected;
            try { selected = Process.GetProcessById(expected.Pid); }
            catch (ArgumentException) { return; }
            using (selected)
            {
                lock (ScriptStudioGuard.SyncRoot)
                {
                    token.ThrowIfCancellationRequested();
                    if (selected.HasExited) return;
                    expected.RequireSame(Identity(expected.Pid));
                    EnsureUnshared(expected.Pid);
                    selected.Kill(); // Never kill a process tree or all processes sharing a name.
                }
                await selected.WaitForExitAsync(token).WaitAsync(TimeSpan.FromSeconds(15), token);
            }
        }

        private async Task<DesktopWindowInfo> FindNewWindowAsync(int pid, CancellationToken token)
        {
            var name = processor.ViewModel?.GetCurrentControllerName();
            var profile = MaaProcessor.Interface?.Controller?.FirstOrDefault(item => item.Name == name && item.ControllerType == MaaControllerTypes.Win32)?.Win32
                ?? throw new InvalidOperationException("原控制器窗口筛选规则不存在。");
            var until = DateTime.UtcNow + TimeSpan.FromSeconds(60);
            while (DateTime.UtcNow < until)
            {
                token.ThrowIfCancellationRequested();
                _target!.RequireSame(Identity(pid));
                var windows = MaaProcessor.Toolkit.Desktop.Window.Find().Where(window =>
                    Matches(window.Name, profile.WindowRegex) && Matches(window.ClassName, profile.ClassRegex));
                var candidate = BountyCandleRecoveryPolicy.SelectUniqueWindow(windows, window => WindowPid(window.Handle), pid);
                if (candidate.Found) return candidate.Window;
                await Task.Delay(250, token);
            }
            throw new TimeoutException("等待重启后的游戏窗口超时；启动器与实际游戏EXE可能不一致。");
        }

        private void EnsureUnshared(int pid)
        {
            BountyCandleRecoveryPolicy.RequireExclusivePid(pid, MaaProcessor.Processors
                .Where(other => other != processor && other.IsTaskRunActive)
                .Select(other => other.ViewModel?.CurrentDevice is DesktopWindowInfo device ? WindowPid(device.Handle) : 0));
        }

        private bool IsLostTarget()
        {
            if (_target == null) return false;
            try { _target.RequireSame(Identity(_target.Pid)); return WindowPid(_window) != _target.Pid; }
            catch (ArgumentException) { return true; }
            catch (InvalidOperationException) { return false; }
        }
    }

    private static bool Matches(string? value, string? pattern) => string.IsNullOrEmpty(pattern)
        || Regex.IsMatch(value ?? "", pattern, RegexOptions.None, TimeSpan.FromSeconds(1));
    private static CandleProcessIdentity Identity(int pid)
    {
        using var process = Process.GetProcessById(pid);
        if (process.HasExited) throw new ArgumentException("游戏进程已结束。");
        return new(pid, process.StartTime.ToUniversalTime().Ticks,
            process.MainModule?.FileName ?? throw new InvalidOperationException("无法核验游戏程序路径。"));
    }
    private static int WindowPid(IntPtr window)
    {
        if (window == IntPtr.Zero) return 0;
        GetWindowThreadProcessId(window, out var pid);
        return unchecked((int)pid);
    }
    private static CandleWindowSize ClientSize(IntPtr window)
    {
        if (!GetClientRect(window, out var rectangle)) throw new InvalidOperationException("无法读取游戏客户区尺寸。");
        return new(rectangle.Right - rectangle.Left, rectangle.Bottom - rectangle.Top);
    }
    [StructLayout(LayoutKind.Sequential)]
    private struct WindowRectangle { public int Left, Top, Right, Bottom; }
    [DllImport("user32.dll")]
    private static extern uint GetWindowThreadProcessId(IntPtr window, out uint processId);
    [DllImport("user32.dll", SetLastError = true)]
    private static extern bool GetClientRect(IntPtr window, out WindowRectangle rectangle);
    [DllImport("user32.dll", SetLastError = true)]
    private static extern bool GetWindowRect(IntPtr window, out WindowRectangle rectangle);
    [DllImport("user32.dll", SetLastError = true)]
    private static extern bool SetWindowPos(IntPtr window, IntPtr after, int x, int y, int width, int height, uint flags);
}
