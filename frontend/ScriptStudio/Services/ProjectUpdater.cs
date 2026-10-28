using System;
using System.IO;
using System.Linq;
using System.Text.Json.Nodes;
using System.Threading;
using System.Threading.Tasks;
using Avalonia;
using Avalonia.Controls.ApplicationLifetimes;
using Avalonia.Threading;
using CommunityToolkit.Mvvm.ComponentModel;
using CommunityToolkit.Mvvm.Input;
using MFAAvalonia.Extensions.MaaFW;
using MFAAvalonia.Helper;

namespace MFAAvalonia.Services;

public static class ProjectUpdater
{
    public static ProjectUpdateViewModel Current { get; } = new();
    public static bool Enabled => true;
    private static Task? _initialization;

    public static Task InitializeAsync() => _initialization ??= InitializeCoreAsync();

    private static async Task InitializeCoreAsync()
    {
        ScriptStudioGuard.UpdateBlockReason = "正在核对安装状态，请稍候。";
        var token = Environment.GetEnvironmentVariable("MAAZMXY4_UPDATE_TOKEN");
        try
        {
            await Current.LoadAsync();
            var state = Current.TransactionState;
            if (state is "applying" or "recovery_required" || state == "pending_health" && string.IsNullOrEmpty(token))
                throw new InvalidOperationException("更新尚未完成，请关闭客户端并使用 MAA造梦西游4.exe 恢复。");
            if (!string.IsNullOrEmpty(token))
            {
                ScriptStudioGuard.UpdateBlockReason = "正在确认新版客户端启动，请稍候。";
                await Current.Client.CallAsync("confirm", new JsonObject { ["token"] = token });
                Environment.SetEnvironmentVariable("MAAZMXY4_UPDATE_TOKEN", null);
            }
            ScriptStudioGuard.UpdateBlockReason = "";
        }
        catch (Exception error)
        {
            ScriptStudioGuard.UpdateBlockReason = error.Message;
            Current.Status = "启动检查未通过：" + error.Message;
            ToastHelper.Warn("更新状态需要处理", error.Message);
        }
    }

    public static async Task CheckAsync(bool startup = false)
    {
        await Dispatcher.UIThread.InvokeAsync(async () =>
        {
            await InitializeAsync();
            if (!string.IsNullOrEmpty(ScriptStudioGuard.UpdateBlockReason)) return;
            if (startup && !Current.CheckOnStart) return;
            await Current.CheckAsync();
            if (startup && Current.CanDownload)
            {
                ToastHelper.Info("发现项目更新", Current.Status + " 可在设置中的版本更新查看。");
                if (Current.AutoDownload) _ = Current.DownloadAsync();
            }
        });
    }

    public static async Task DownloadAsync(string? localPackage = null)
    {
        await Dispatcher.UIThread.InvokeAsync(async () =>
        {
            await InitializeAsync();
            if (!string.IsNullOrEmpty(ScriptStudioGuard.UpdateBlockReason)) return;
            await Current.CheckAsync(localPackage != null);
            if (Current.CanDownload) await Current.DownloadAsync(localPackage);
        });
    }
}

public sealed class ProjectUpdateViewModel : ObservableObject
{
    public ProjectUpdateClient Client { get; } = new();
    private string _status = "尚未检查更新。", _versions = "正在读取安装版本…", _notes = "", _source = "";
    private bool _busy, _available, _staged, _checkOnStart = true, _autoDownload, _allowModified;
    private double _progress;
    private CancellationTokenSource? _cancel;
    private string? _channel;
    public string TransactionState { get; private set; } = "idle";
    public string Status { get => _status; set => SetProperty(ref _status, value); }
    public string Versions { get => _versions; private set => SetProperty(ref _versions, value); }
    public string Notes { get => _notes; private set => SetProperty(ref _notes, value); }
    public string Source { get => _source; private set => SetProperty(ref _source, value); }
    public double Progress { get => _progress; private set { SetProperty(ref _progress, value); OnPropertyChanged(nameof(ProgressIndeterminate)); } }
    public bool ProgressIndeterminate => Busy && Progress <= 0;
    public bool Busy { get => _busy; private set { SetProperty(ref _busy, value); Refresh(); } }
    public bool CheckOnStart { get => _checkOnStart; set => SetProperty(ref _checkOnStart, value); }
    public bool AutoDownload { get => _autoDownload; set => SetProperty(ref _autoDownload, value); }
    public bool AllowModified { get => _allowModified; set => SetProperty(ref _allowModified, value); }
    public bool CanDownload => !Busy && _available;
    public bool CanInstall => !Busy && _staged && !ScriptStudioGuard.IsCapturing &&
        !MaaProcessor.Processors.Any(p => p.IsTaskRunActive);
    public string InstallHint => Busy ? "正在处理更新，请等待当前步骤完成。" : CanInstall ? "请先保存编辑内容。安装会退出本目录的助手，并保留个人录制与配置。" :
        _staged ? "请先停止所有实例任务及脚本录制，再安装更新。" : "下载并校验更新包后，可以退出助手安装。";
    public AsyncRelayCommand CheckCommand { get; }
    public AsyncRelayCommand DownloadCommand { get; }
    public AsyncRelayCommand InstallCommand { get; }
    public AsyncRelayCommand RepairCommand { get; }
    public AsyncRelayCommand SavePreferencesCommand { get; }
    public RelayCommand CancelCommand { get; }

    public ProjectUpdateViewModel()
    {
        CheckCommand = new AsyncRelayCommand(() => CheckAsync(), () => !Busy);
        DownloadCommand = new AsyncRelayCommand(() => DownloadAsync(), () => CanDownload);
        InstallCommand = new AsyncRelayCommand(InstallAsync, () => CanInstall);
        RepairCommand = new AsyncRelayCommand(() => CheckAsync(true), () => !Busy);
        SavePreferencesCommand = new AsyncRelayCommand(SavePreferencesAsync, () => !Busy);
        CancelCommand = new RelayCommand(() => _cancel?.Cancel(), () => Busy);
    }

    public void Refresh()
    {
        OnPropertyChanged(nameof(CanDownload)); OnPropertyChanged(nameof(CanInstall)); OnPropertyChanged(nameof(InstallHint));
        OnPropertyChanged(nameof(ProgressIndeterminate));
        CheckCommand?.NotifyCanExecuteChanged(); DownloadCommand?.NotifyCanExecuteChanged();
        InstallCommand?.NotifyCanExecuteChanged(); RepairCommand?.NotifyCanExecuteChanged();
        SavePreferencesCommand?.NotifyCanExecuteChanged(); CancelCommand?.NotifyCanExecuteChanged();
    }

    public async Task LoadAsync()
    {
        var data = await Client.CallAsync("status");
        if (data["client_version"] == null)
            throw new InvalidOperationException("缺少客户端安装记录，请先手动安装本项目的完整新版。");
        Versions = $"客户端 {data["client_version"]?.GetValue<string>() ?? "未知"}    ·    资源 {data["resource_version"]?.GetValue<string>() ?? "未知"}";
        _channel = data["source"]?["channel"]?.GetValue<string>();
        Source = $"{(_channel == "preview" ? "预览渠道" : "正式渠道")}    ·    {data["source"]?["repository"]}";
        CheckOnStart = data["preferences"]?["check_on_start"]?.GetValue<bool>() ?? true;
        AutoDownload = data["preferences"]?["auto_download"]?.GetValue<bool>() ?? false;
        TransactionState = data["transaction"]?["state"]?.GetValue<string>() ?? "idle";
        _staged = data["pending"]?.GetValue<bool>() ?? false;
        Refresh();
    }

    private async Task RunAsync(Func<Task> action)
    {
        if (Busy) return;
        Busy = true;
        _cancel = new CancellationTokenSource();
        try { await action(); }
        catch (OperationCanceledException) { Status = "已取消操作；尚未安装的文件不会改变当前版本。"; }
        catch (Exception error) { Status = "操作未完成：" + error.Message; }
        finally { _cancel.Dispose(); _cancel = null; Busy = false; }
    }

    public Task CheckAsync(bool repair = false) => RunAsync(async () =>
    {
        _available = false;
        Progress = 0;
        Status = repair ? "正在检查完整修复包…" : "正在检查项目更新…";
        var data = await Client.CallAsync("check", new JsonObject { ["repair"] = repair }, cancellationToken: _cancel!.Token);
        // A newer accepted manifest can invalidate an earlier download. Ask
        // the same backend again instead of leaving a stale install button.
        _staged = false;
        var local = await Client.CallAsync("status", cancellationToken: _cancel.Token);
        _staged = local["pending"]?.GetValue<bool>() ?? false;
        Status = data["message"]?.GetValue<string>() ?? "检查完成。";
        _available = data["status"]?.GetValue<string>() == "available";
        var update = data["update"];
        if (update != null)
        {
            var kind = update["kind"]?.GetValue<string>() == "client" ? "完整客户端" : "资源";
            Status = $"{kind} {update["version"]} · {(update["size"]?.GetValue<long>() ?? 0) / 1048576.0:0.0} MB。" + Status;
            Notes = update["notes"]?.GetValue<string>() ?? "";
        }
    });

    public Task DownloadAsync(string? localPackage = null) => RunAsync(async () =>
    {
        Status = localPackage == null ? "正在下载并校验更新包…" : "正在核对并导入已发布的完整包…";
        Progress = 0;
        await Client.CallAsync(localPackage == null ? "download" : "import",
            localPackage == null ? null : new JsonObject { ["path"] = localPackage },
            (done, total) => Progress = total > 0 ? 100.0 * done / total : 0, _cancel!.Token);
        _staged = true; _available = false;
        Progress = 100;
        Status = "更新包已校验，当前运行版本尚未改变。就绪后点击“退出并安装”。";
    });

    private Task SavePreferencesAsync() => RunAsync(async () =>
    {
        await Client.CallAsync("preferences", new JsonObject { ["channel"] = _channel,
            ["check_on_start"] = CheckOnStart, ["auto_download"] = AutoDownload });
        Status = "更新偏好已保存；安装始终需要确认退出。";
    });

    private Task InstallAsync() => RunAsync(async () =>
    {
        lock (ScriptStudioGuard.SyncRoot)
        {
            if (ScriptStudioGuard.IsCapturing || MaaProcessor.Processors.Any(p => p.IsTaskRunActive))
                throw new InvalidOperationException("请先停止所有任务及脚本录制。");
            ScriptStudioGuard.UpdateBlockReason = "正在准备安装更新，请等待或取消。";
        }
        try
        {
            Status = "正在准备独立安装助手与备份…";
            Progress = 0;
            await Client.CallAsync("install", new JsonObject { ["allow_modified"] = AllowModified }, cancellationToken: _cancel!.Token);
            if (Application.Current?.ApplicationLifetime is IClassicDesktopStyleApplicationLifetime desktop)
            {
                if (!desktop.TryShutdown()) throw new InvalidOperationException("退出被取消，安装助手将等待程序关闭。请勿启动任务。");
            }
            else throw new InvalidOperationException("请手动关闭此客户端以继续安装。");
        }
        catch { ScriptStudioGuard.UpdateBlockReason = ""; throw; }
    });
}
