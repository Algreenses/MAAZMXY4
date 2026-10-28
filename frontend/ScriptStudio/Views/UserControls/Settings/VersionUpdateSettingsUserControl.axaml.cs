using System;
using System.Diagnostics;
using System.IO;
using Avalonia.Controls;
using Avalonia.Interactivity;
using Avalonia.Platform.Storage;
using Avalonia.Threading;
using MFAAvalonia.Services;

namespace MFAAvalonia.Views.UserControls.Settings;

public partial class VersionUpdateSettingsUserControl : UserControl
{
    private readonly DispatcherTimer _timer = new() { Interval = TimeSpan.FromSeconds(1) };
    public VersionUpdateSettingsUserControl()
    {
        InitializeComponent();
        DataContext = ProjectUpdater.Current;
        _timer.Tick += (_, _) => ProjectUpdater.Current.Refresh();
        Loaded += async (_, _) =>
        {
            await ProjectUpdater.InitializeAsync();
            _timer.Start();
        };
        Unloaded += (_, _) => _timer.Stop();
    }

    private async void ImportPackage(object? sender, RoutedEventArgs args)
    {
        var window = TopLevel.GetTopLevel(this);
        if (window == null || ProjectUpdater.Current.Busy) return;
        try
        {
            var files = await window.StorageProvider.OpenFilePickerAsync(new FilePickerOpenOptions
            {
                Title = "选择本项目已发布的完整客户端 ZIP", AllowMultiple = false,
                FileTypeFilter = new[] { new FilePickerFileType("完整客户端 ZIP") { Patterns = new[] { "*.zip" } } }
            });
            if (files.Count == 1 && files[0].TryGetLocalPath() is { } path)
                await ProjectUpdater.DownloadAsync(path);
        }
        catch (Exception error) { ProjectUpdater.Current.Status = "导入未完成：" + error.Message; }
    }

    private void OpenLog(object? sender, RoutedEventArgs args)
    {
        try
        {
            var directory = Path.Combine(ProjectUpdater.Current.Client.Root, "temp", "project-update");
            Directory.CreateDirectory(directory);
            Process.Start(new ProcessStartInfo(directory) { UseShellExecute = true });
        }
        catch (Exception error) { ProjectUpdater.Current.Status = "无法打开日志目录：" + error.Message; }
    }
}
