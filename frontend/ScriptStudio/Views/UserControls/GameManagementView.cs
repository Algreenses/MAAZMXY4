using System;
using System.Globalization;
using System.Threading.Tasks;
using Avalonia;
using Avalonia.Controls;
using Avalonia.Layout;
using Avalonia.Platform.Storage;
using MFAAvalonia.Services;
using MFAAvalonia.ViewModels.Pages;

namespace MFAAvalonia.Views.UserControls;

/// <summary>Uses the selected instance and its existing log, never a second message surface.</summary>
public sealed class GameManagementView : UserControl
{
    private readonly CheckBox _failure = new() { Content = "任务失败后自动重启" };
    private readonly CheckBox _memory = new() { Content = "内存超过 1.8 GB 自动重启" };
    private readonly CheckBox _interval = new() { Content = "定时自动重启（间隔分钟）" };
    private readonly TextBox _minutes = new() { Text = "60", Width = 80 };
    private readonly TextBox _exe = new() { Watermark = "现有造梦盒子 EXE 路径" };
    private readonly TextBox _account = new() { Watermark = "盒子历史账号中的名称" };
    private readonly TextBox _role = new() { Watermark = "用于核对存档的角色名" };
    private readonly ComboBox _slot = new() { ItemsSource = new[] { "存档 1", "存档 2", "存档 3", "存档 4", "存档 5", "存档 6", "存档 7", "存档 8" }, SelectedIndex = 0 };
    private bool _busy;
    private TaskQueueViewModel? Page => DataContext as TaskQueueViewModel;

    public GameManagementView()
    {
        var panel = new StackPanel { Spacing = 7 };
        panel.Children.Add(_failure);
        panel.Children.Add(_memory);
        var interval = new WrapPanel { Orientation = Orientation.Horizontal };
        interval.Children.Add(_interval); interval.Children.Add(_minutes); panel.Children.Add(interval);
        panel.Children.Add(new TextBlock { Text = "程序与登录", FontWeight = Avalonia.Media.FontWeight.SemiBold });
        panel.Children.Add(_exe);
        var paths = new WrapPanel();
        paths.Children.Add(Button("选择 EXE", ChooseExe));
        paths.Children.Add(Button("使用已选窗口", () =>
        {
            if (Page != null) _exe.Text = GameManagementService.For(Page.Processor).SelectedExePath();
            return Task.CompletedTask;
        }));
        panel.Children.Add(paths);
        panel.Children.Add(_account);
        panel.Children.Add(_role);
        panel.Children.Add(_slot);
        var actions = new WrapPanel();
        actions.Children.Add(Button("保存设置", Save));
        actions.Children.Add(Button("启动并连接", async () => { await Save(); await Service().StartAsync(); }));
        actions.Children.Add(Button("重启", async () => { if (Page?.Processor.IsTaskRunActive != true) await Save(); await Service().RequestRestartAsync(); }));
        actions.Children.Add(Button("关闭盒子", async () => { await Save(); await Service().CloseAsync(); }));
        panel.Children.Add(actions);
        Content = new ScrollViewer { Content = panel, HorizontalScrollBarVisibility = Avalonia.Controls.Primitives.ScrollBarVisibility.Disabled };
        DataContextChanged += (_, _) => Load();
        AttachedToVisualTree += (_, _) => Load();
    }

    private GameManagementService Service() => Page == null
        ? throw new InvalidOperationException("当前实例尚未就绪。") : GameManagementService.For(Page.Processor);

    private Button Button(string title, Func<Task> action)
    {
        var button = new Button { Content = title, Margin = new Thickness(0, 0, 6, 4), Padding = new Thickness(9, 5) };
        button.Click += async (_, _) =>
        {
            if (_busy) return;
            _busy = true;
            try { await action(); }
            catch (Exception error) { Page?.Processor.AddLog("游戏管理：" + error.Message); }
            finally { _busy = false; }
        };
        return button;
    }

    private void Load()
    {
        if (Page == null) return;
        try
        {
            var options = Service().Options;
            _failure.IsChecked = options.RestartOnFailure; _memory.IsChecked = options.RestartOnMemory;
            _interval.IsChecked = options.RestartOnInterval; _minutes.Text = options.IntervalMinutes.ToString(CultureInfo.InvariantCulture);
            _exe.Text = options.ExePath; _account.Text = options.AccountName; _role.Text = options.RoleName;
            _slot.SelectedIndex = options.SaveSlot - 1;
        }
        catch (Exception error) { Page.Processor.AddLog("游戏管理设置未载入：" + error.Message); }
    }

    private Task Save()
    {
        if (!int.TryParse(_minutes.Text, NumberStyles.None, CultureInfo.InvariantCulture, out var minutes))
            throw new InvalidOperationException("重启间隔请输入整数分钟。");
        Service().Save(new GameManagementOptions
        {
            RestartOnFailure = _failure.IsChecked == true, RestartOnMemory = _memory.IsChecked == true,
            RestartOnInterval = _interval.IsChecked == true, IntervalMinutes = minutes,
            ExePath = _exe.Text?.Trim() ?? "", AccountName = _account.Text?.Trim() ?? "",
            RoleName = _role.Text?.Trim() ?? "", SaveSlot = _slot.SelectedIndex + 1
        });
        return Task.CompletedTask;
    }

    private async Task ChooseExe()
    {
        var provider = TopLevel.GetTopLevel(this)?.StorageProvider;
        if (provider == null) return;
        var files = await provider.OpenFilePickerAsync(new FilePickerOpenOptions
        {
            Title = "选择现有造梦盒子程序", AllowMultiple = false,
            FileTypeFilter = new[] { new FilePickerFileType("Windows 程序") { Patterns = new[] { "*.exe" } } }
        });
        if (files.Count == 1) _exe.Text = files[0].TryGetLocalPath();
    }
}
