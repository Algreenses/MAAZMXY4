using System.Reflection;
using System.Text.Json.Nodes;
using Avalonia;
using Avalonia.Controls;
using Avalonia.Headless;
using Avalonia.Interactivity;
using Avalonia.Media;
using Avalonia.Themes.Fluent;
using Avalonia.Threading;
using Avalonia.VisualTree;
using MFAAvalonia.Extensions.MaaFW;
using MFAAvalonia.Services;
using MFAAvalonia.ViewModels.Pages;
using MFAAvalonia.Views.Pages;

internal static class Program
{
    [STAThread]
    public static int Main()
    {
        AppBuilder.Configure<TestApplication>().UseHeadless(new AvaloniaHeadlessPlatformOptions()).SetupWithoutStarting();
        var failed = 0;
        var tests = new Action[] { FirstOpenDuringTask, RefreshAndMutationGuard, RetryFailedInitialization, RecordingUsesCurrentTaskController };
        foreach (var test in tests)
        {
            try { test(); Console.WriteLine("PASS " + test.Method.Name); }
            catch (Exception error) { failed++; Console.WriteLine("FAIL " + test.Method.Name + ": " + error.Message); }
        }
        Console.WriteLine($"{tests.Length-failed}/{tests.Length} page regression checks passed; no real processes, hooks, or game input were used.");
        return failed == 0 ? 0 : 1;
    }

    private static T Field<T>(object target, string name) => (T)target.GetType().GetField(name, BindingFlags.NonPublic | BindingFlags.Instance)!.GetValue(target)!;
    private static void Check(bool condition, string message) { if (!condition) throw new Exception(message); }
    private static void Click(Button button) { button.RaiseEvent(new RoutedEventArgs(Button.ClickEvent)); Dispatcher.UIThread.RunJobs(); }
    private static Button Find(Window window, string name) => window.GetVisualDescendants().OfType<Button>().First(b => Equals(b.Content, name));
    private static (Window Window, ScriptStudioView View, ScriptStudioViewModel Model) Open(bool active, int failures = 0)
    {
        MaaProcessor.Processors.Clear(); MaaProcessor.Processors.Add(new() { IsTaskRunActive = active });
        WindowFixture.ListCalls = 0;
        var model = new ScriptStudioViewModel(); model.Client.FailLists = failures;
        var view = new ScriptStudioView(model);
        var window = new Window { Content = view, Width = 1200, Height = 800 };
        window.Show(); Dispatcher.UIThread.RunJobs();
        Console.WriteLine($"TRACE attached active={active} lists={model.Client.Calls.Count(x => x == "list")} windows={WindowFixture.ListCalls} initialized={Field<bool>(view,"_initialized")} status={Field<TextBlock>(view,"_status").Text}");
        return (window, view, model);
    }
    private static void Close(Window window, ScriptStudioViewModel model)
    {
        MaaProcessor.Processors.Clear(); model.Apply(new JsonObject { ["empty"] = true }); window.Close(); Dispatcher.UIThread.RunJobs();
    }
    private static void FirstOpenDuringTask()
    {
        var (window, view, model) = Open(true);
        try
        {
            Check(Field<ListBox>(view,"_library").ItemCount == 2, "first open must show both scripts while a task runs");
            Check(WindowFixture.ListCalls == 1, "read-only window discovery must run");
            Check(!Field<Button>(view,"_record").IsEnabled, "recording must remain disabled");
        }
        finally { Close(window, model); }
    }
    private static void RefreshAndMutationGuard()
    {
        var (window, view, model) = Open(false);
        try
        {
            MaaProcessor.Processors[0].IsTaskRunActive = true;
            Click(Find(window,"刷新"));
            Check(model.Client.Calls.Count(x=>x=="list") == 2, "library refresh must work after a task starts");
            Check(Field<ListBox>(view,"_library").ItemCount == 2, "refresh must preserve visible scripts");
            Click(Find(window,"新建"));
            Click(Field<Button>(view,"_record"));
            Check(!model.Client.Calls.Contains("new") && !model.Client.Calls.Contains("capture_start"), "mutations must still be blocked");
            MaaProcessor.Processors[0].IsTaskRunActive = false;
            Click(Find(window,"新建"));
            Check(model.Client.Calls.Contains("new"), "editing must unlock after the task stops");
        }
        finally { Close(window, model); }
    }
    private static void RetryFailedInitialization()
    {
        var (window, view, model) = Open(false, 1);
        try
        {
            Check(!Field<bool>(view,"_initialized"), "a failed first load must not mark initialization complete");
            window.Content = null; window.Content = view; Dispatcher.UIThread.RunJobs();
            Check(Field<ListBox>(view,"_library").ItemCount == 2, "reattaching after a failed load must retry");
            Check(Field<bool>(view,"_initialized"), "successful retry must complete initialization");
        }
        finally { Close(window, model); }
    }
    private static void RecordingUsesCurrentTaskController()
    {
        WindowFixture.Windows = new[] { new WindowFixture(7,"Alternate game client","OtherWindowClass"), new WindowFixture(3,"Second client","OtherWindowClass") };
        var before = WindowFixture.ListCalls;
        var windows = StudioWindows.List();
        Check(WindowFixture.ListCalls == before+1, "recording must query the current task page instead of enumerating with its own filters");
        Check(windows.Select(x=>x.Handle).SequenceEqual(new nint[] { 7,3 }), "recording must keep the host-selected ordering and support arbitrary titles/classes");
        Check(windows[0].Title == "Alternate game client", "recording must use the host-provided labels");
    }
}

internal sealed class TestApplication : Application
{
    public override void Initialize() { Styles.Add(new FluentTheme()); Resources["SukiCardBackground"] = Brushes.White; }
}

// Only the host/task state, OS discovery and service transport are faked. The production
// view, event handlers, layout, model and timeline above are compiled without alteration.
namespace MFAAvalonia.ViewModels.Pages { public class ViewModelBase { } }
namespace MFAAvalonia.Extensions.MaaFW
{
    public sealed class MaaProcessor
    {
        public static List<MaaProcessor> Processors { get; } = new();
        public bool IsTaskRunActive { get; set; }
        public string InstanceId => "fixture";
    }
    public sealed class MaaProcessorManager
    {
        public static bool IsInstanceCreated => true;
        public static MaaProcessorManager Instance { get; } = new();
        public MaaProcessor Current { get; } = new();
        public WindowFixture GetViewModel(string instanceId) => new(0,"","");
    }
}
namespace MFAAvalonia.Services
{
    public sealed record WindowFixture(nint Handle, string Name, string ClassName)
    {
        public static int ListCalls;
        public static IReadOnlyList<WindowFixture> Windows = new[] { new WindowFixture(1,"Fixture window","Fixture") };
        public IReadOnlyList<WindowFixture> GetScriptRecordingWindows() { ListCalls++; return Windows; }
    }
    public static class ScriptStudioGuard
    {
        public static void BeginCapture() { if (MaaProcessor.Processors.Any(p=>p.IsTaskRunActive)) throw new InvalidOperationException("Task is active"); }
        public static void EndCapture() { }
    }
    public sealed class ScriptStudioClient : IDisposable
    {
        public List<string> Calls { get; } = new();
        public int FailLists;
        public bool IsDisconnected => false;
        public Task<JsonNode> CallAsync(string method, JsonObject? parameters = null)
        {
            Calls.Add(method);
            if (method == "list" && FailLists-- > 0) throw new IOException("Fixture transient list failure");
            JsonNode result = method == "list"
                ? JsonNode.Parse("""{"items":[{"name":"User recording","file":"user.json","duration_ms":1000,"event_count":2},{"name":"Default recording","file":"default.json","duration_ms":1000,"event_count":2,"is_default":true}],"tasks":[],"binding_revision":"fixture"}""")!
                : JsonNode.Parse("""{"name":"New recording","duration_ms":0,"operations":[],"dirty":false}""")!;
            return Task.FromResult(result);
        }
        public void Dispose() { }
    }
}
