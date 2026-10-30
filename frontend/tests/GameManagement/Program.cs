using Avalonia;
using Avalonia.Controls;
using Avalonia.Headless;
using Avalonia.Interactivity;
using Avalonia.Themes.Fluent;
using Avalonia.Threading;
using Avalonia.VisualTree;
using MFAAvalonia.Services;
using MFAAvalonia.ViewModels.Pages;
using MFAAvalonia.Views.UserControls;

internal static class Program
{
    public static int Main()
    {
        try { Run(); return 0; }
        catch (Exception error) { Console.Error.WriteLine(error); return 1; }
    }
    private static void Require(bool condition, string message) { if (!condition) throw new Exception(message); }
    private static void Run()
    {
        var render = Environment.GetEnvironmentVariable("GAME_MANAGEMENT_RENDER_DIR");
        AppBuilder.Configure<TestApp>().UseSkia().UseHeadless(new AvaloniaHeadlessPlatformOptions
            { UseHeadlessDrawing = render == null }).SetupWithoutStarting();
        var first = new TaskQueueViewModel(); var second = new TaskQueueViewModel();
        var control = new GameManagementView { DataContext = first };
        var window = new Window { Width = 430, Height = 580, Content = control };
        window.Show(); Dispatcher.UIThread.RunJobs();
        var rows = new AfkOptionControls();
        var count = new NumericUpDown { Value = 37 };
        var row = new StackPanel(); row.Children.Add(count); rows.Add(row);
        rows.SetEnabled(true);
        Require(!row.IsEnabled && count.Value == 37, "AFK disables count without overwriting it");
        var retryRow = new StackPanel(); rows.Add(retryRow);
        Require(!retryRow.IsEnabled, "Late-created retry options remain disabled");
        rows.SetEnabled(false);
        Require(row.IsEnabled && retryRow.IsEnabled && count.Value == 37, "Leaving AFK restores original values");
        var checks = control.GetVisualDescendants().OfType<CheckBox>().ToArray();
        Require(checks.Length == 3 && checks.All(c => c.IsChecked != true), "All three policies default off");
        var texts = control.GetVisualDescendants().OfType<TextBox>().ToArray();
        var minutes = texts.Single(t => t.Width == 80);
        var exe = texts.Single(t => t.Watermark?.Contains("EXE") == true);
        var account = texts.Single(t => t.Watermark?.Contains("账号") == true);
        var role = texts.Single(t => t.Watermark?.Contains("角色") == true);
        var save = control.GetVisualDescendants().OfType<Button>().Single(b => Equals(b.Content,"保存设置"));
        checks[1].IsChecked = true; checks[2].IsChecked = true;
        exe.Text = Path.GetFullPath("zmBox.exe"); account.Text = "历史账号甲"; role.Text = "角色甲";
        minutes.Text = "0"; save.RaiseEvent(new RoutedEventArgs(Button.ClickEvent)); Dispatcher.UIThread.RunJobs();
        Require(first.Processor.Logs.Last().Contains("分钟") && !GameManagementService.For(first.Processor).Options.Enabled,
            "Invalid interval logs and preserves saved options");
        minutes.Text = "60"; save.RaiseEvent(new RoutedEventArgs(Button.ClickEvent)); Dispatcher.UIThread.RunJobs();
        Require(GameManagementService.For(first.Processor).Options.RestartOnMemory, "Save applies memory policy");
        control.DataContext = second; Dispatcher.UIThread.RunJobs();
        Require(checks.All(c => c.IsChecked != true), "Another instance does not inherit policy");
        control.DataContext = first; Dispatcher.UIThread.RunJobs();
        Require(checks[1].IsChecked == true && checks[2].IsChecked == true && minutes.Text == "60", "Saved instance restores its options");
        if (render != null)
        {
            Directory.CreateDirectory(render);
            Application.Current!.RequestedThemeVariant = Avalonia.Styling.ThemeVariant.Dark;
            Pump();
            using var dark = window.CaptureRenderedFrame(); dark!.Save(Path.Combine(render,"game-management-dark.png"));
            window.Height = 280; Pump();
            var scroll = control.GetVisualDescendants().OfType<ScrollViewer>().First();
            Require(scroll.Extent.Height > scroll.Viewport.Height, "Short cards keep controls scrollable");
            using var compact = window.CaptureRenderedFrame(); compact!.Save(Path.Combine(render,"game-management-compact.png"));
        }
        window.Close();
        Console.WriteLine("PASS game-management controls, validation, instance isolation and compact layout; no game/process/device used.");
    }
    private static void Pump()
    {
        var frame = new DispatcherFrame();
        var timer = new DispatcherTimer { Interval = TimeSpan.FromMilliseconds(80) };
        timer.Tick += (_, _) => { timer.Stop(); frame.Continue=false; };
        timer.Start(); Dispatcher.UIThread.PushFrame(frame);
    }
}
internal sealed class TestApp : Application { public override void Initialize() => Styles.Add(new FluentTheme()); }
