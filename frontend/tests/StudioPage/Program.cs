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
        var tests = new Action[] { FirstOpenDuringTask, RefreshAndMutationGuard, RetryFailedInitialization, RecordingUsesCurrentTaskController, RenameCancelCollisionAndSave, RenameOtherFileKeepsEditorState, DurationTextApplies, DurationTextSavesWithoutExtraApply };
        foreach (var test in tests)
        {
            try { test(); Console.WriteLine("PASS " + test.Method.Name); }
            catch (Exception error) { failed++; Console.WriteLine("FAIL " + test.Method.Name + ": " + error.Message); }
        }
        Console.WriteLine($"{tests.Length-failed}/{tests.Length} page regression checks passed; no real processes, hooks, or game input were used.");
        return failed == 0 ? 0 : 1;
    }

    private static T Field<T>(object target, string name) => (T)target.GetType().GetField(name, BindingFlags.NonPublic | BindingFlags.Instance)!.GetValue(target)!;
    private static void DurationTextApplies()
    {
        var (window, view, model) = Open(false);
        try
        {
            model.Apply(JsonNode.Parse("""{"file":"user.json","name":"User recording","duration_ms":31359,"operations":[],"dirty":false}""")!);
            Field<TabControl>(view,"_tabs").SelectedIndex=1;
            Dispatcher.UIThread.RunJobs();
            var duration=Field<NumericUpDown>(view,"_scriptDuration");
            duration.Text="32.500";
            Console.WriteLine($"TRACE duration text={duration.Text} value={duration.Value}");
            Click(Find(window,"应用时长"));
            Check(model.Client.DurationRequest==32500, $"typed duration sent {model.Client.DurationRequest}, expected 32500");
            Check(model.Duration==32500, "duration response must update the page");
        }
        finally { Close(window,model); }
    }
    private static void DurationTextSavesWithoutExtraApply()
    {
        var (window, view, model) = Open(false);
        try
        {
            model.Apply(JsonNode.Parse("""{"file":"user.json","name":"User recording","duration_ms":31359,"operations":[],"dirty":false}""")!);
            Field<TabControl>(view,"_tabs").SelectedIndex=1;
            Dispatcher.UIThread.RunJobs();
            Field<NumericUpDown>(view,"_scriptDuration").Text="32.500";
            Click(Find(window,"保存"));
            Check(model.Client.DurationRequest==32500, "Save ignored the typed total duration");
            Check(model.Client.Calls.IndexOf("set_duration")<model.Client.Calls.IndexOf("save"), "duration must be applied before saving");
        }
        finally { Close(window,model); }
    }
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
    private static void RenameCancelCollisionAndSave()
    {
        var (window, view, model) = Open(false);
        try
        {
            model.Apply(JsonNode.Parse("""{"file":"user.json","name":"User recording","duration_ms":1000,"operations":[],"dirty":false}""")!);
            Dispatcher.UIThread.RunJobs();
            Click(Find(window,"重命名…"));
            var dialog=window.OwnedWindows.Single();
            dialog.GetVisualDescendants().OfType<TextBox>().Single().Text="Cancelled";
            Click(Find(dialog,"取消"));
            Check(!model.Client.Calls.Contains("rename_file") && model.File=="user.json", "cancel must preserve the saved file");
            Click(Find(window,"重命名…"));
            dialog=window.OwnedWindows.Single();
            var input=dialog.GetVisualDescendants().OfType<TextBox>().Single();
            input.Text="default";
            Click(Find(dialog,"保存"));
            Check(dialog.IsVisible && !model.Client.Calls.Contains("rename_file"),"collision must keep the name dialog open");
            input.Text="Renamed script";
            Click(Find(dialog,"保存"));
            Check(model.File=="Renamed script.json" && model.Name=="Renamed script", "renaming must update the loaded document path and name");
            Check(model.Client.Calls.Count(x=>x=="rename_file")==1, "one confirmation must perform one rename");
            var selected=Field<ListBox>(view,"_library").SelectedItem as JsonNode;
            Check(selected?["file"]?.GetValue<string>()==model.File, "library selection must follow the renamed document");
        }
        finally { Close(window,model); }
    }
    private static void RenameOtherFileKeepsEditorState()
    {
        var (window,view,model)=Open(false);
        try
        {
            model.Apply(JsonNode.Parse("""{"file":"user.json","name":"User recording","duration_ms":1000,"operations":[],"dirty":true}""")!);
            Dispatcher.UIThread.RunJobs();
            var changes=0;
            model.Changed+=()=>changes++;
            var other=Field<JsonArray>(view,"_files")[1]!;
            var rename=(Task)view.GetType().GetMethod("RenameLibraryFileAsync",BindingFlags.Instance|BindingFlags.NonPublic)!.Invoke(view,new object[]{other})!;
            Dispatcher.UIThread.RunJobs();
            var dialog=window.OwnedWindows.Single();
            dialog.GetVisualDescendants().OfType<TextBox>().Single().Text="Other renamed";
            Click(Find(dialog,"保存"));
            Check(rename.IsCompletedSuccessfully && model.File=="user.json" && model.Dirty && changes==0,
                "renaming another row must not rerender or discard the current unsaved inspector");
        }
        finally { Close(window,model); }
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
        public long? DurationRequest;
        private string? _renamed, _renamedFile;
        public bool IsDisconnected => false;
        public Task<JsonNode> CallAsync(string method, JsonObject? parameters = null)
        {
            Calls.Add(method);
            if (method=="set_duration")
            {
                DurationRequest=parameters!["duration_ms"]!.GetValue<long>();
                return Task.FromResult<JsonNode>(new JsonObject { ["file"]="user.json", ["name"]="User recording", ["duration_ms"]=(double)DurationRequest.Value, ["operations"]=new JsonArray(), ["dirty"]=true });
            }
            if (method == "list" && FailLists-- > 0) throw new IOException("Fixture transient list failure");
            if (method == "rename_file")
            {
                _renamed=parameters!["name"]!.GetValue<string>();
                _renamedFile=parameters["file"]!.GetValue<string>();
                return Task.FromResult<JsonNode>(new JsonObject { ["document"]=new JsonObject
                { ["file"]=_renamed+".json", ["name"]=_renamed, ["duration_ms"]=1000.0, ["operations"]=new JsonArray(), ["dirty"]=false } });
            }
            JsonNode result = method == "list"
                ? JsonNode.Parse("""{"items":[{"name":"User recording","file":"user.json","revision":"fixture","duration_ms":1000,"event_count":2},{"name":"Default recording","file":"default.json","revision":"fixture","duration_ms":1000,"event_count":2,"is_default":true}],"tasks":[],"binding_revision":"fixture"}""")!
                : JsonNode.Parse("""{"name":"New recording","duration_ms":0,"operations":[],"dirty":false}""")!;
            if (method=="list" && _renamed!=null)
            {
                var item=result["items"]!.AsArray().First(value=>value!["file"]!.GetValue<string>()==_renamedFile)!;
                item["name"]=_renamed; item["file"]=_renamed+".json";
            }
            return Task.FromResult(result);
        }
        public void Dispose() { }
    }
}
