using System;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Text.Json.Nodes;
using System.Threading.Tasks;
using Avalonia;
using Avalonia.Controls;
using Avalonia.Controls.Templates;
using Avalonia.Input;
using Avalonia.Layout;
using Avalonia.Markup.Xaml.MarkupExtensions;
using Avalonia.Media;
using Avalonia.Platform.Storage;
using Avalonia.Threading;
using Avalonia.VisualTree;
using MFAAvalonia.Controls;
using MFAAvalonia.Extensions.MaaFW;
using MFAAvalonia.Services;
using MFAAvalonia.ViewModels.Pages;

namespace MFAAvalonia.Views.Pages;

/// <summary>Native MFA page, retaining the existing app navigation and themes.</summary>
public sealed class ScriptStudioView : UserControl
{
    private readonly ScriptStudioViewModel _vm;
    private readonly TabControl _tabs = new();
    private readonly ListBox _library = new(), _events = new();
    private readonly TextBox _search = new() { Watermark = "搜索脚本名称" };
    private readonly TextBox _name = new() { Watermark = "为脚本起个名字" };
    private readonly TextBlock _summary = new(), _status = new(), _captureState = new(), _selection = new();
    private readonly TextBlock _taskBinding = new() { TextWrapping = TextWrapping.Wrap, FontSize = 12 };
    private readonly TextBlock _bindingHint = new() { TextWrapping = TextWrapping.Wrap, FontSize = 12, Opacity = .7 };
    private readonly TextBlock _captureHint = new() { TextWrapping = TextWrapping.Wrap, Opacity = .7 };
    private readonly ComboBox _tasks = new() { HorizontalAlignment = HorizontalAlignment.Stretch };
    private readonly ComboBox _bindingMode = new() { HorizontalAlignment = HorizontalAlignment.Stretch };
    private readonly TextBlock _diagnostics = new() { TextWrapping = TextWrapping.Wrap };
    private readonly ComboBox _windows = new() { HorizontalAlignment = HorizontalAlignment.Stretch, MinWidth = 200 };
    private readonly NumericUpDown _countdown = Number(3, 0, 30, "0");
    private readonly ScriptTimeline _timeline = new();
    private readonly ScrollViewer _timelineScroll = new() { HorizontalScrollBarVisibility = Avalonia.Controls.Primitives.ScrollBarVisibility.Auto, HorizontalContentAlignment = HorizontalAlignment.Left, VerticalContentAlignment = VerticalAlignment.Top };
    private readonly NumericUpDown _cursor = Number(0, 0, 604800, "0.000");
    private readonly ComboBox _kind = new() { ItemsSource = new[] { "按键长按", "鼠标点击 / 拖动", "单次按键", "单次点击", "鼠标移动", "滚轮", "输入文本", "阶段标记" }, SelectedIndex = 1 };
    private readonly NumericUpDown _start = Number(0), _duration = Number(.05), _key = Number(74, 1, 255, "0"), _x = Number(0, 0, 32767, "0"), _y = Number(0, 0, 32767, "0");
    private readonly NumericUpDown _dx = Number(0, -100000, 100000, "0"), _dy = Number(1, -100000, 100000, "0");
    private readonly ComboBox _button = new() { ItemsSource = new[] { "左键", "右键", "中键" }, SelectedIndex = 0 };
    private readonly TextBox _text = new() { Watermark = "文本或标记名称", AcceptsReturn = true, MinHeight = 70 };
    private readonly TextBox _keyCapture = new() { Watermark = "点此后按一个键", IsReadOnly = true };
    private readonly StackPanel _inspector = new() { Spacing = 8 };
    private Control? _keyFields, _mouseFields, _scrollFields;
    private readonly Button _save, _record, _pause, _stop, _apply, _undo, _redo, _use, _editPage, _bindTask, _unbindTask;
    private Button? _refreshWindows, _mark;
    private readonly DispatcherTimer _poll = new() { Interval = TimeSpan.FromMilliseconds(250) };
    private JsonArray _files = new();
    private StudioOperation? _selected;
    private bool _sync, _busy, _polling, _initialized, _inspectorDirty, _adding, _allowClose;
    private Window? _owner;
    private string _recordingName = "";
    private string _state = "idle";
    private string _renderedName = "";
    private string? _renderedFile;
    private bool _fitNextTimeline = true;
    private Grid? _editorMain;
    private GridSplitter? _editorSplitter;
    private Control? _inspectorPane;
    private Expander? _operationList;
    private Button? _focusTimelineButton;
    private bool _timelineFocused;
    private GridLength _inspectorWidth = new(320);
    private string _inspectorBaseline = "";

    public ScriptStudioView(ScriptStudioViewModel viewModel)
    {
        _vm = viewModel; DataContext = viewModel;
        _save = Button("保存脚本", SaveAsync, true);
        _record = Button("开始录制", StartRecordingAsync, true);
        _pause = Button("暂停", async () => RenderCapture(await _vm.Client.CallAsync(_state == "paused" ? "capture_resume" : "capture_pause")));
        _stop = Button("停止并编辑", FinishRecordingAsync);
        _apply = Button("应用操作", ApplyInspectorAsync, true);
        _undo = Button("撤销", async () => { if (await EnsureInspectorAsync()) await _vm.ChangeAsync("undo"); });
        _redo = Button("重做", async () => { if (await EnsureInspectorAsync()) await _vm.ChangeAsync("redo"); });
        _use = Button("设为通用回放", SelectForTasksAsync);
        _bindTask = Button("绑定脚本", BindTaskAsync, true);
        _unbindTask = Button("解除绑定", UnbindTaskAsync);
        _editPage = Button("打开时间轴编辑 →", async () => { if (_vm.Document != null) _tabs.SelectedIndex = 1; await Task.CompletedTask; });
        BuildPage();
        _tasks.SelectionChanged += (_, _) => RefreshBinding();
        _bindingMode.SelectionChanged += (_, _) => UpdateBindingHint();
        _vm.Changed += RenderDocument;
        _poll.Tick += async (_, _) => await PollCaptureAsync();
        _search.TextChanged += (_, _) => FilterLibrary();
        _library.SelectionChanged += async (_, _) =>
        {
            if (_sync || _library.SelectedItem is not JsonNode item) return;
            await Run(async () =>
            {
                if (!await EnsureSavedAsync()) { FilterLibrary(); return; }
                await _vm.ChangeAsync("load", new JsonObject { ["file"] = item["file"]!.GetValue<string>() });
                _status.Text = "已打开脚本，可切换到「时间轴与编辑」修改操作。";
            });
        };
        _events.SelectionChanged += async (_, _) =>
        {
            if (!_sync && _events.SelectedItem is StudioOperation operation)
                await Run(async () => { if (await EnsureInspectorAsync()) SelectOperation(operation); else RestoreSelectionVisual(); });
        };
        _timeline.Selected += operation => _ = Run(async () => { if (await EnsureInspectorAsync()) SelectOperation(operation); else RestoreSelectionVisual(); });
        _timeline.Edited += (operation, start, duration) => _ = Run(async () =>
        {
            if (!await EnsureInspectorAsync()) return;
            await _vm.ChangeAsync("edit", new JsonObject { ["id"] = operation.Id,
                ["changes"] = new JsonObject { ["start_ms"] = start, ["duration_ms"] = duration } });
            SelectById(operation.Id);
        });
        _timeline.CursorChanged += time => _cursor.Value = (decimal)(time / 1000);
        _tabs.SelectionChanged += (_, e) =>
        {
            if (ReferenceEquals(e.Source, _tabs) && _tabs.SelectedIndex == 1)
                Dispatcher.UIThread.Post(FitPendingTimeline, DispatcherPriority.Background);
        };
        _cursor.ValueChanged += (_, _) => _timeline.SetCursor(Milliseconds(_cursor));
        foreach (var number in new[] { _start, _duration, _key, _x, _y, _dx, _dy })
            number.ValueChanged += (_, _) => { if (!_sync) RefreshInspectorDirty(); };
        _button.SelectionChanged += (_, _) => { if (!_sync) RefreshInspectorDirty(); };
        _kind.SelectionChanged += (_, _) =>
        {
            if (_sync) return;
            UpdateInspectorFields();
            if (_kind.SelectedIndex is not (0 or 1)) _duration.Value = 0;
            RefreshInspectorDirty();
        };
        _text.TextChanged += (_, _) => { if (!_sync) RefreshInspectorDirty(); };
        _keyCapture.KeyDown += (_, e) =>
        {
            var key = StudioWindows.VirtualKeyFromKey(e.Key);
            if (key > 0) { _key.Value = key; _keyCapture.Text = StudioOperation.KeyName(key); }
            else _keyCapture.Text = "此按键暂不支持，请手动填写键码。";
            e.Handled = true;
        };
        AttachedToVisualTree += async (_, _) =>
        {
            _owner = TopLevel.GetTopLevel(this) as Window;
            if (!_initialized)
            {
                _initialized = true;
                if (_owner != null) _owner.Closing += OnClosing;
                await Run(async () => { RefreshWindows(); await RefreshLibraryAsync(); });
            }
            _poll.Start();
        };
        AddHandler(KeyDownEvent, OnShortcut, Avalonia.Interactivity.RoutingStrategies.Tunnel);
    }

    private void BuildPage()
    {
        var page = new Grid { RowDefinitions = new RowDefinitions("Auto,Auto,*,Auto"), Margin = new Thickness(22, 16), RowSpacing = 14 };
        var header = new Grid { ColumnDefinitions = new ColumnDefinitions("*,Auto") };
        var title = new StackPanel { Spacing = 4 };
        title.Children.Add(new TextBlock { Text = "录制与脚本", FontSize = 25, FontWeight = FontWeight.SemiBold });
        title.Children.Add(new TextBlock { Text = "记录战斗操作，整理脚本，在时间轴上精确调整。", Opacity = .65 });
        header.Children.Add(title); Grid.SetColumn(_save, 1); header.Children.Add(_save); page.Children.Add(header);
        var nameRow = new Grid { ColumnDefinitions = new ColumnDefinitions("*,Auto"), ColumnSpacing = 14 };
        nameRow.Children.Add(_name); _summary.VerticalAlignment = VerticalAlignment.Center; Grid.SetColumn(_summary, 1); nameRow.Children.Add(_summary);
        Grid.SetRow(nameRow, 1); page.Children.Add(nameRow);
        _tabs.Items.Add(new TabItem { Header = "脚本库与录制", Content = BuildLibraryTab() });
        _tabs.Items.Add(new TabItem { Header = "时间轴与编辑", Content = BuildEditorTab() });
        Grid.SetRow(_tabs, 2); page.Children.Add(_tabs);
        _status.Text = "先选择已有脚本，或启动游戏后开始录制。"; _status.TextWrapping = TextWrapping.Wrap; _status.Opacity = .8;
        Grid.SetRow(_status, 3); page.Children.Add(_status); Content = page;
    }
    private Control BuildLibraryTab()
    {
        var layout = new Grid { ColumnDefinitions = new ColumnDefinitions("*,14,*"), Margin = new Thickness(0, 14, 0, 0) };
        var library = new Grid { RowDefinitions = new RowDefinitions("Auto,Auto,*,Auto,Auto"), RowSpacing = 10 };
        library.Children.Add(new TextBlock { Text = "脚本库", FontSize = 18, FontWeight = FontWeight.SemiBold });
        Grid.SetRow(_search, 1); library.Children.Add(_search);
        _library.MinHeight = 0; _library.ClipToBounds = true;
        _library.ItemTemplate = new FuncDataTemplate<JsonNode>((item, _) =>
        {
            if (item == null) return new TextBlock();
            var stack = new StackPanel { Spacing = 5, Margin = new Thickness(6, 7) };
            stack.Children.Add(new TextBlock { Text = item["name"]?.GetValue<string>(), FontWeight = FontWeight.SemiBold, TextTrimming = TextTrimming.CharacterEllipsis });
            stack.Children.Add(new TextBlock { Text = $"{(item["duration_ms"]?.GetValue<double>() ?? 0) / 1000:0.0} 秒   ·   {item["event_count"]} 个事件", FontSize = 12, Opacity = .6 });
            if (!string.IsNullOrEmpty(item["error"]?.GetValue<string>())) stack.Children.Add(new TextBlock { Text = "需要检查录制完整性", FontSize = 12, Foreground = Brushes.DarkOrange });
            return stack;
        });
        Grid.SetRow(_library, 2); library.Children.Add(_library);
        var actions = Wrap(Button("导入", ImportAsync), Button("导出", ExportAsync), Button("刷新", RefreshLibraryAsync));
        Grid.SetRow(actions, 3); library.Children.Add(actions);
        _tasks.ItemTemplate = new FuncDataTemplate<JsonNode>((item, _) => new TextBlock { Text = item?["label"]?.GetValue<string>(), TextTrimming = TextTrimming.CharacterEllipsis });
        var bindings = new StackPanel { Spacing = 8 };
        var bindingChoices = new Grid { ColumnDefinitions = new ColumnDefinitions("*,*"), ColumnSpacing = 8 };
        bindingChoices.Children.Add(_tasks); Grid.SetColumn(_bindingMode, 1); bindingChoices.Children.Add(_bindingMode);
        bindings.Children.Add(bindingChoices);
        bindings.Children.Add(_taskBinding); bindings.Children.Add(_bindingHint);
        bindings.Children.Add(Wrap(_bindTask, _unbindTask));
        var bindingSection = new Expander { Header = "绑定到任务", Content = bindings, IsExpanded = true, HorizontalAlignment = HorizontalAlignment.Stretch };
        var bindingNarrow = false;
        bindingChoices.SizeChanged += (_, _) =>
        {
            var narrow = bindingChoices.Bounds.Width < 400;
            if (narrow == bindingNarrow) return;
            bindingNarrow = narrow;
            bindingChoices.ColumnDefinitions = new ColumnDefinitions(narrow ? "*" : "*,*");
            bindingChoices.RowDefinitions = new RowDefinitions(narrow ? "Auto,Auto" : "Auto");
            bindingChoices.RowSpacing = narrow ? 8 : 0;
            Grid.SetColumn(_bindingMode, narrow ? 0 : 1); Grid.SetRow(_bindingMode, narrow ? 1 : 0);
        };
        Grid.SetRow(bindingSection, 4); library.Children.Add(bindingSection); layout.Children.Add(Card(library));
        var recording = new StackPanel { Spacing = 10 };
        recording.Children.Add(new TextBlock { Text = "录制战斗", FontSize = 18, FontWeight = FontWeight.SemiBold });
        recording.Children.Add(new TextBlock { Text = "选择游戏窗口，在倒计时结束前切回游戏。", TextWrapping = TextWrapping.Wrap, Opacity = .7 });
        var targetRow = new Grid { ColumnDefinitions = new ColumnDefinitions("*,Auto"), ColumnSpacing = 8 };
        targetRow.Children.Add(_windows);
        _refreshWindows = Button("刷新", async () => { RefreshWindows(); await Task.CompletedTask; }); Grid.SetColumn(_refreshWindows, 1); targetRow.Children.Add(_refreshWindows);
        recording.Children.Add(Field("目标游戏窗口", targetRow));
        var ready = new Grid { ColumnDefinitions = new ColumnDefinitions("140,*"), ColumnSpacing = 20 };
        ready.Children.Add(Field("倒计时（秒）", _countdown));
        _captureState.FontSize = 22; _captureState.FontWeight = FontWeight.SemiBold; _captureState.Text = "准备就绪";
        _captureState.VerticalAlignment = VerticalAlignment.Center; _captureState.TextWrapping = TextWrapping.Wrap;
        Grid.SetColumn(_captureState, 1); ready.Children.Add(_captureState); recording.Children.Add(ready);
        _mark = Button("添加标记", async () => await _vm.Client.CallAsync("capture_mark", new JsonObject { ["label"] = "阶段标记" }));
        recording.Children.Add(Wrap(_record, _pause, _stop, _mark));
        recording.Children.Add(_captureHint);
        recording.Children.Add(new TextBlock { Text = "F8 暂停 / 继续    F9 停录    F10 停录并保存\n切出游戏会自动暂停。单击继续后有3秒切回游戏，也可在游戏中按 F8 立即继续。", TextWrapping = TextWrapping.Wrap, Opacity = .65, FontSize = 12 });
        recording.Children.Add(new Separator()); recording.Children.Add(_editPage);
        recording.Children.Add(new TextBlock { Text = "绑定读取脚本库中的同一文件，编辑保存后下轮生效；另存为新文件后请重新绑定。", TextWrapping = TextWrapping.Wrap, Opacity = .65, FontSize = 12 });
        _selection.TextWrapping = TextWrapping.Wrap;
        recording.Children.Add(_selection);
        recording.Children.Add(Wrap(_use, Button("新建空白脚本", async () => { if (await EnsureSavedAsync()) { await _vm.ChangeAsync("new"); _tabs.SelectedIndex = 1; BeginInsert(); } })));
        var right = Card(new ScrollViewer { Content = recording }); Grid.SetColumn(right, 2); layout.Children.Add(right); return layout;
    }
    private Control BuildEditorTab()
    {
        var layout = new Grid { RowDefinitions = new RowDefinitions("Auto,*"), RowSpacing = 8, Margin = new Thickness(0, 10, 0, 0) };
        _focusTimelineButton = Button("专注时间轴", async () => { ToggleTimelineFocus(); await Task.CompletedTask; });
        _cursor.Width = 110; _cursor.MinWidth = 100; _cursor.MaxWidth = 110; _cursor.MinHeight = 32; _cursor.Height = 32;
        var toolbar = Wrap(_undo, _redo, Button("新增操作", async () => { if (await EnsureInspectorAsync()) BeginInsert(); }),
            Button("删除操作", DeleteAsync), Button("添加标记", async () => { if (await EnsureInspectorAsync()) await _vm.ChangeAsync("marker", new JsonObject { ["at_ms"] = Milliseconds(_cursor), ["label"] = "阶段标记" }); }),
            Button("离线检查", ValidateAsync), Button("补齐释放", async () => { if (await EnsureInspectorAsync()) await _vm.ChangeAsync("repair"); }),
            new TextBlock { Text = "定位（秒）", VerticalAlignment = VerticalAlignment.Center, FontSize = 12 }, _cursor,
            Button("在此拆分", SplitAsync), Button("缩小", async () => { _timeline.Zoom(.8); await Task.CompletedTask; }),
            Button("放大", async () => { _timeline.Zoom(1.25); await Task.CompletedTask; }),
            Button("全览", async () => { _timeline.Fit(_timelineScroll.Viewport.Width); await Task.CompletedTask; }),
            Button("轨道−", async () => { _timeline.AdjustTrackHeight(-8); await Task.CompletedTask; }),
            Button("轨道＋", async () => { _timeline.AdjustTrackHeight(8); await Task.CompletedTask; }), _focusTimelineButton);
        foreach (var control in toolbar.Children)
        {
            control.Margin = new Thickness(0, 0, 5, 4);
            if (control is Button button) { button.Padding = new Thickness(9, 5); button.MinHeight = 32; button.FontSize = 12; }
        }
        layout.Children.Add(toolbar);

        _editorMain = new Grid { ColumnDefinitions = new ColumnDefinitions("*,8,320") };
        _editorMain.ColumnDefinitions[0].MinWidth = 240;
        _editorMain.ColumnDefinitions[2].MinWidth = 280;
        var timelineArea = new Grid { RowDefinitions = new RowDefinitions("*,Auto") };
        _timelineScroll.Content = _timeline; _timelineScroll.MinHeight = 180;
        _timelineScroll.VerticalScrollBarVisibility = Avalonia.Controls.Primitives.ScrollBarVisibility.Auto;
        _timelineScroll.PropertyChanged += (_, change) =>
        {
            if (change.Property == ScrollViewer.OffsetProperty) { _timeline.InvalidateVisual(); return; }
            if (change.Property != ScrollViewer.ViewportProperty) return;
            _timeline.SetViewport(_timelineScroll.Viewport.Width, _timelineScroll.Viewport.Height);
            FitPendingTimeline();
        };
        var timelineCard = Card(_timelineScroll); timelineCard.Padding = new Thickness(0); timelineArea.Children.Add(timelineCard);

        var list = new Grid { RowDefinitions = new RowDefinitions("*,Auto"), RowSpacing = 6, Height = 160 };
        _events.ItemsSource = _vm.Operations;
        _events.ItemTemplate = new FuncDataTemplate<StudioOperation>((operation, _) =>
        {
            var row = new Grid { ColumnDefinitions = new ColumnDefinitions("85,*,90"), Margin = new Thickness(4, 6) };
            if (operation == null) return row;
            row.Children.Add(new TextBlock { Text = operation.TimeText, FontFamily = FontFamily.Parse("Consolas"), Opacity = .7 });
            var label = new TextBlock { Text = operation.Label, TextTrimming = TextTrimming.CharacterEllipsis }; Grid.SetColumn(label, 1); row.Children.Add(label);
            var duration = new TextBlock { Text = operation.DurationText, Opacity = .6, HorizontalAlignment = HorizontalAlignment.Right }; Grid.SetColumn(duration, 2); row.Children.Add(duration); return row;
        });
        list.Children.Add(_events); Grid.SetRow(_diagnostics, 1); list.Children.Add(_diagnostics);
        _operationList = new Expander { Header = "操作列表（按需展开）", Content = list, IsExpanded = false,
            HorizontalAlignment = HorizontalAlignment.Stretch, HorizontalContentAlignment = HorizontalAlignment.Stretch,
            Margin = new Thickness(0, 8, 0, 0) };
        Grid.SetRow(_operationList, 1); timelineArea.Children.Add(_operationList); _editorMain.Children.Add(timelineArea);

        _inspector.Children.Add(new TextBlock { Text = "操作参数", FontSize = 17, FontWeight = FontWeight.SemiBold });
        _inspector.Children.Add(Field("操作类型", _kind));
        var timing = new Grid { ColumnDefinitions = new ColumnDefinitions("*,10,*") }; timing.Children.Add(Field("开始（秒）", _start)); var holdField = Field("持续（秒）", _duration); Grid.SetColumn(holdField, 2); timing.Children.Add(holdField); _inspector.Children.Add(timing);
        var keys = new Grid { ColumnDefinitions = new ColumnDefinitions("*,10,*") }; keys.Children.Add(Field("按键", _keyCapture)); var keyCode = Field("按键码", _key); Grid.SetColumn(keyCode, 2); keys.Children.Add(keyCode); _keyFields = keys; _inspector.Children.Add(keys);
        var xy = new Grid { ColumnDefinitions = new ColumnDefinitions("*,8,*,8,*") }; xy.Children.Add(Field("鼠标按钮", _button)); var xf = Field("X 坐标", _x); Grid.SetColumn(xf, 2); xy.Children.Add(xf); var yf = Field("Y 坐标", _y); Grid.SetColumn(yf, 4); xy.Children.Add(yf); _mouseFields = xy; _inspector.Children.Add(xy);
        _scrollFields = Field("横向 / 纵向滚轮", Wrap(_dx, _dy)); _inspector.Children.Add(_scrollFields); _inspector.Children.Add(Field("文本 / 标记", _text));
        var inspectorBody = new Grid { RowDefinitions = new RowDefinitions("*,Auto"), RowSpacing = 8 };
        inspectorBody.Children.Add(new ScrollViewer { Content = _inspector });
        var applyRow = Wrap(_apply, Button("还原填写", async () => { if (_selected != null) SelectOperation(_selected); else BeginInsert(); await Task.CompletedTask; })); Grid.SetRow(applyRow, 1); inspectorBody.Children.Add(applyRow);
        _inspectorPane = Card(inspectorBody); Grid.SetColumn(_inspectorPane, 2); _editorMain.Children.Add(_inspectorPane);
        _editorSplitter = new GridSplitter { ResizeDirection = GridResizeDirection.Columns, ResizeBehavior = GridResizeBehavior.PreviousAndNext,
            HorizontalAlignment = HorizontalAlignment.Stretch, VerticalAlignment = VerticalAlignment.Stretch,
            Background = new SolidColorBrush(Color.Parse("#188497B0")), KeyboardIncrement = 16 };
        ToolTip.SetTip(_editorSplitter, "拖动调整时间轴与参数区的宽度"); Grid.SetColumn(_editorSplitter, 1); _editorMain.Children.Add(_editorSplitter);
        Grid.SetRow(_editorMain, 1); layout.Children.Add(_editorMain); return layout;
    }

    private void ToggleTimelineFocus()
    {
        if (_editorMain == null || _inspectorPane == null || _editorSplitter == null || _operationList == null) return;
        _timelineFocused = !_timelineFocused;
        if (_timelineFocused)
        {
            _inspectorWidth = _editorMain.ColumnDefinitions[2].Width;
            _editorMain.ColumnDefinitions[2].MinWidth = 0;
            _editorMain.ColumnDefinitions[2].Width = new GridLength(0);
            _editorMain.ColumnDefinitions[1].Width = new GridLength(0);
        }
        else
        {
            _editorMain.ColumnDefinitions[2].MinWidth = 280;
            _editorMain.ColumnDefinitions[2].Width = _inspectorWidth;
            _editorMain.ColumnDefinitions[1].Width = new GridLength(8);
        }
        _inspectorPane.IsVisible = _editorSplitter.IsVisible = _operationList.IsVisible = !_timelineFocused;
        if (_focusTimelineButton != null) _focusTimelineButton.Content = _timelineFocused ? "返回参数区" : "专注时间轴";
        Dispatcher.UIThread.Post(() => _timeline.SetViewport(_timelineScroll.Viewport.Width, _timelineScroll.Viewport.Height), DispatcherPriority.Background);
    }

    private static NumericUpDown Number(double value, double minimum = 0, double maximum = 604800, string format = "0.000") => new()
    { Value = (decimal)value, Minimum = (decimal)minimum, Maximum = (decimal)maximum, Increment = format == "0" ? 1 : .01m, FormatString = format, MinWidth = 90, HorizontalAlignment = HorizontalAlignment.Stretch };
    private Button Button(string label, Func<Task> action, bool primary = false)
    {
        var button = new Button { Content = label, Padding = new Thickness(14, 9), MinHeight = 38 };
        if (primary) button.Classes.Add("Flat");
        button.Click += async (_, _) => await Run(action); return button;
    }
    private static WrapPanel Wrap(params Control[] controls)
    {
        var panel = new WrapPanel { Orientation = Orientation.Horizontal };
        foreach (var control in controls) { control.Margin = new Thickness(0, 0, 8, 6); panel.Children.Add(control); } return panel;
    }
    private static StackPanel Field(string label, Control control)
    {
        var panel = new StackPanel { Spacing = 5 }; panel.Children.Add(new TextBlock { Text = label, Opacity = .65, FontSize = 12 }); panel.Children.Add(control); return panel;
    }
    private static Border Card(Control content)
    {
        var border = new Border { Child = content, Padding = new Thickness(16), CornerRadius = new CornerRadius(12), BorderThickness = new Thickness(1), BorderBrush = new SolidColorBrush(Color.Parse("#258497B0")) };
        border.Bind(Border.BackgroundProperty, new DynamicResourceExtension("SukiCardBackground")); return border;
    }
    private static long Milliseconds(NumericUpDown field) => (long)Math.Round((field.Value ?? 0) * 1000);
    private async Task Run(Func<Task> action)
    {
        if (_busy) return;
        if (MaaProcessor.Processors.Any(p => p.IsTaskRunActive))
        { _status.Text = "任务正在运行，请停止任务后再修改或录制脚本。"; return; }
        _busy = true; UpdateControls();
        try { await action(); }
        catch (Exception error)
        {
            _status.Text = error.Message;
            if (_vm.Client.IsDisconnected) { _vm.Capturing = false; _state = "failed"; ScriptStudioGuard.EndCapture(); }
        }
        finally { _busy = false; UpdateControls(); }
    }
    private void RefreshWindows()
    {
        var old = (_windows.SelectedItem as StudioWindow)?.Handle;
        var windows = StudioWindows.List(); _windows.ItemsSource = windows;
        _windows.SelectedItem = windows.FirstOrDefault(w => w.Handle == old) ?? windows.FirstOrDefault();
        if (windows.Count == 0) _status.Text = "没有找到游戏窗口，请先启动造梦盒子，再刷新窗口。";
    }
    private async Task RefreshLibraryAsync()
    {
        var result = await _vm.Client.CallAsync("list"); _files = result["items"]!.AsArray();
        var selected = result["selected_file"]?.GetValue<string>();
        _selection.Text = selected == null ? "通用回放：尚未指定，普通任务默认使用最新录制。" : "通用回放：" + selected;
        var selectedTask = (_tasks.SelectedItem as JsonNode)?["id"]?.GetValue<string>();
        var tasks = (result["tasks"]?.AsArray() ?? new JsonArray()).Where(n => n != null).ToList();
        _tasks.ItemsSource = tasks;
        _tasks.SelectedItem = tasks.FirstOrDefault(t => t?["id"]?.GetValue<string>() == selectedTask) ?? tasks.FirstOrDefault();
        RefreshBinding();
        if (result["binding_errors"]?["catalog"] is JsonNode error) _status.Text = error.GetValue<string>();
        FilterLibrary();
    }
    private void FilterLibrary()
    {
        _sync = true;
        try
        {
            var query = _search.Text?.Trim() ?? "";
            var files = _files.Where(item => item != null && (query.Length == 0 || (item["name"]?.GetValue<string>() ?? "").Contains(query, StringComparison.CurrentCultureIgnoreCase))).ToList();
            _library.ItemsSource = files;
            _library.SelectedItem = files.FirstOrDefault(item => item?["file"]?.GetValue<string>() == _vm.File);
        }
        finally { _sync = false; }
    }
    private void RenderDocument()
    {
        var selectedId = _selected?.Id; _sync = true;
        if (_vm.File != _renderedFile || _vm.Name != _renderedName) { _fitNextTimeline = true; _adding = false; }
        var pendingName = (_name.Text ?? "") != _renderedName;
        if (!pendingName || _vm.File != _renderedFile || _vm.Name != _renderedName) _name.Text = _vm.Name;
        _renderedName = _vm.Name; _renderedFile = _vm.File;
        _summary.Text = _vm.Document == null ? "" : $"{_vm.Duration / 1000:0.000} 秒 · {_vm.Operations.Count} 个操作" + (_vm.Dirty ? " · 未保存" : " · 已保存");
        _diagnostics.Text = string.Join("\n", _vm.Diagnostics.Take(3));
        _timeline.Update(_vm.Operations.ToList(), _vm.Duration);
        _sync = false;
        _inspectorDirty = false;
        SelectById(selectedId); UpdateControls();
        if (_tabs.SelectedIndex == 1) Dispatcher.UIThread.Post(FitPendingTimeline, DispatcherPriority.Background);
    }
    private void FitPendingTimeline()
    {
        if (!_fitNextTimeline || _timelineScroll.Viewport.Width < 150) return;
        _timeline.Fit(_timelineScroll.Viewport.Width); _fitNextTimeline = false;
    }
    private void SelectById(string? id)
    {
        var operation = _vm.Operations.FirstOrDefault(o => o.Id == id);
        if (operation != null) SelectOperation(operation); else { _selected = null; _inspector.IsEnabled = false; }
    }
    private void RestoreSelectionVisual()
    {
        _sync = true; _events.SelectedItem = _selected; _timeline.SelectedId = _selected?.Id;
        _timeline.InvalidateVisual(); _sync = false;
    }
    private void SelectOperation(StudioOperation operation)
    {
        _sync = true;
        try
        {
        _selected = operation; _adding = false; _inspector.IsEnabled = true;
        _events.SelectedItem = operation; _timeline.SelectedId = operation.Id; _timeline.InvalidateVisual();
        _kind.SelectedIndex = operation.Kind switch { "key_hold" => 0, "mouse_hold" => 1, "key" => 2, "click" => 3, "move" => 4, "scroll" => 5, "text" => 6, "marker" => 7, _ => 1 };
        _kind.IsEnabled = false;
        _start.Value = (decimal)(operation.Start / 1000); _duration.Value = (decimal)(operation.Duration / 1000);
        _key.Value = StudioOperation.Integer(operation.Data["key"], 74);
        _keyCapture.Text = StudioOperation.KeyName((int)(_key.Value ?? 74));
        _x.Value = StudioOperation.Integer(operation.Data["x"]); _y.Value = StudioOperation.Integer(operation.Data["y"]);
        _dx.Value = StudioOperation.Integer(operation.Data["dx"]); _dy.Value = StudioOperation.Integer(operation.Data["dy"]);
        _button.SelectedIndex = StudioOperation.String(operation.Data["button"]) switch { "right" => 1, "middle" => 2, _ => 0 };
        _text.Text = operation.Kind == "marker" ? StudioOperation.String(operation.Data["label"]) : StudioOperation.String(operation.Data["text"]);
        UpdateInspectorFields();
        RememberInspectorBaseline();
        }
        finally { _sync = false; }
    }
    private void BeginInsert()
    {
        if (_vm.Document == null) throw new InvalidOperationException("请先选择脚本或新建空白脚本。");
        _sync = true; _selected = null; _adding = true; _inspector.IsEnabled = true; _kind.IsEnabled = true;
        _kind.SelectedIndex = 1; _start.Value = _cursor.Value; _duration.Value = .05m; _text.Text = "";
        UpdateInspectorFields();
        RememberInspectorBaseline(); _sync = false;
    }
    private JsonObject ReadInspectorValues()
    {
        var kind = new[] { "key_hold", "mouse_hold", "key", "click", "move", "scroll", "text", "marker" }[Math.Clamp(_kind.SelectedIndex, 0, 7)];
        var changes = new JsonObject { ["kind"] = kind, ["start_ms"] = Milliseconds(_start), ["duration_ms"] = kind is "key_hold" or "mouse_hold" ? Milliseconds(_duration) : 0 };
        if (kind is "key_hold" or "key") changes["key"] = (int)(_key.Value ?? 74);
        if (kind is "mouse_hold" or "click" or "move") { changes["x"] = (int)(_x.Value ?? 0); changes["y"] = (int)(_y.Value ?? 0); }
        if (kind == "mouse_hold") changes["button"] = new[] { "left", "right", "middle" }[Math.Max(0, _button.SelectedIndex)];
        if (kind == "scroll") { changes["dx"] = (int)(_dx.Value ?? 0); changes["dy"] = (int)(_dy.Value ?? 0); }
        if (kind == "text") changes["text"] = _text.Text ?? "";
        if (kind == "marker") changes["label"] = _text.Text ?? "阶段标记";
        return changes;
    }
    private void RememberInspectorBaseline()
    {
        _inspectorBaseline = ReadInspectorValues().ToJsonString();
        _inspectorDirty = false;
    }
    private bool RefreshInspectorDirty()
    {
        if (_sync) return _inspectorDirty;
        // Compare the same normalized, kind-specific values that an apply would send.
        // Deferred control events and equivalent decimal formatting are not edits.
        _inspectorDirty = (_selected != null || _adding) &&
            !string.Equals(ReadInspectorValues().ToJsonString(), _inspectorBaseline, StringComparison.Ordinal);
        return _inspectorDirty;
    }
    private async Task ApplyInspectorAsync()
    {
        var changes = ReadInspectorValues();
        var kind = changes["kind"]!.GetValue<string>();
        changes.Remove("kind");
        if (_adding)
        {
            var previousIds = _vm.Operations.Select(operation => operation.Id).ToHashSet();
            changes["kind"] = kind;
            await _vm.ChangeAsync("insert", new JsonObject { ["changes"] = changes });
            var added = _vm.Operations.FirstOrDefault(operation => !previousIds.Contains(operation.Id));
            if (added != null) SelectOperation(added);
        }
        else if (_selected != null) await _vm.ChangeAsync("edit", new JsonObject { ["id"] = _selected.Id, ["changes"] = changes });
        RememberInspectorBaseline(); _status.Text = "操作已应用，保存脚本后即可用于任务。";
    }
    private void UpdateInspectorFields()
    {
        var kind = _kind.SelectedIndex;
        if (_kind.Parent is Control typeField) typeField.IsVisible = _adding;
        if (_keyFields != null) _keyFields.IsVisible = kind is 0 or 2;
        if (_mouseFields != null) _mouseFields.IsVisible = kind is 1 or 3 or 4;
        if (_scrollFields != null) _scrollFields.IsVisible = kind == 5;
        if (_text.Parent is Control textField) textField.IsVisible = kind is 6 or 7;
        _text.MinHeight = kind == 7 ? 38 : 70;
        _duration.IsEnabled = kind is 0 or 1;
        _key.IsEnabled = _keyCapture.IsEnabled = kind is 0 or 2;
        _button.IsEnabled = kind == 1;
        _x.IsEnabled = _y.IsEnabled = kind is 1 or 3 or 4;
        _dx.IsEnabled = _dy.IsEnabled = kind == 5;
        _text.IsEnabled = kind is 6 or 7;
    }
    private async Task<bool> EnsureInspectorAsync()
    {
        if (!RefreshInspectorDirty()) return true;
        var choice = await AskAsync("操作参数尚未应用", "先应用填写的参数，或还原填写后继续。", "应用", "还原");
        if (choice == 0) return false;
        if (choice == 1) await ApplyInspectorAsync();
        else if (_selected != null) SelectOperation(_selected);
        else BeginInsert();
        return true;
    }
    private bool HasChanges => _vm.Dirty || RefreshInspectorDirty() || _vm.Document != null && (_name.Text ?? "") != _vm.Name;
    private async Task<bool> EnsureSavedAsync()
    {
        if (!HasChanges) return true;
        var choice = await AskAsync("保留当前脚本修改", "当前修改尚未保存。", "保存", "放弃修改");
        if (choice == 0) return false;
        if (choice == 1) await SaveAsync();
        else
        {
            if (_vm.Client.IsDisconnected) _vm.Apply(new JsonObject { ["empty"] = true });
            else await _vm.ChangeAsync("discard");
            _inspectorDirty = false;
        }
        return true;
    }
    private async Task SaveAsync()
    {
        if (RefreshInspectorDirty()) await ApplyInspectorAsync();
        if (_vm.Document == null) throw new InvalidOperationException("请先选择或录制脚本。");
        if ((_name.Text ?? "").Trim() != _vm.Name)
            await _vm.ChangeAsync("rename", new JsonObject { ["name"] = (_name.Text ?? "").Trim() });
        await _vm.ChangeAsync("save"); await RefreshLibraryAsync(); _status.Text = "脚本已保存。";
    }
    private async Task ImportAsync()
    {
        if (!await EnsureSavedAsync()) return;
        var storage = TopLevel.GetTopLevel(this)?.StorageProvider; if (storage == null) return;
        var files = await storage.OpenFilePickerAsync(new FilePickerOpenOptions { Title = "导入录制脚本", AllowMultiple = false, FileTypeFilter = new[] { JsonFiles } });
        var path = files.FirstOrDefault()?.TryGetLocalPath(); if (path == null) return;
        await _vm.ChangeAsync("import", new JsonObject { ["path"] = path }); _status.Text = "脚本已导入为草稿，保存后进入脚本库。";
    }
    private static FilePickerFileType JsonFiles => new("录制脚本 JSON") { Patterns = new[] { "*.json" } };
    private async Task ExportAsync()
    {
        if (RefreshInspectorDirty()) await ApplyInspectorAsync();
        var storage = TopLevel.GetTopLevel(this)?.StorageProvider; if (storage == null || _vm.Document == null) return;
        var file = await storage.SaveFilePickerAsync(new FilePickerSaveOptions { Title = "导出录制脚本", SuggestedFileName = _vm.Name + ".json", DefaultExtension = "json", FileTypeChoices = new[] { JsonFiles }, ShowOverwritePrompt = true });
        var path = file?.TryGetLocalPath(); if (path != null) { await _vm.Client.CallAsync("export", new JsonObject { ["path"] = path }); _status.Text = "脚本已导出。"; }
    }
    private async Task SelectForTasksAsync()
    {
        if (!await EnsureSavedAsync() || _vm.File == null) return;
        await _vm.Client.CallAsync("select"); await RefreshLibraryAsync();
        _status.Text = "已设为通用回放。任务选择「脚本工具所选」时读取这份脚本；任务独立绑定优先生效。";
    }
    private void RefreshBinding()
    {
        var task = _tasks.SelectedItem as JsonNode;
        var modes = task?["modes"]?.AsArray().Select(n => n!.GetValue<string>()).ToArray() ?? Array.Empty<string>();
        _bindingMode.ItemsSource = modes.Select(m => m == "pipeline" ? "替换任务内的回放脚本" : "用脚本执行整个任务").ToArray();
        var boundMode = task?["binding"]?["mode"]?.GetValue<string>();
        _bindingMode.SelectedIndex = Math.Max(0, Array.IndexOf(modes, boundMode));
        var file = task?["binding"]?["file"]?.GetValue<string>();
        var error = task?["binding_error"]?.GetValue<string>();
        _taskBinding.Text = error != null ? error : task == null ? "没有可绑定的任务，请先在任务页添加任务。" : file == null ? "尚未绑定，选择脚本后可绑定到此任务。" : "当前绑定：" + file;
        ToolTip.SetTip(_tasks, task?["entry"]?.GetValue<string>());
        UpdateBindingHint(); UpdateControls();
    }
    private string SelectedBindingMode => (_tasks.SelectedItem as JsonNode)?["modes"]?.AsArray().ElementAtOrDefault(_bindingMode.SelectedIndex)?.GetValue<string>() ?? "replace";
    private void UpdateBindingHint() => _bindingHint.Text = SelectedBindingMode == "pipeline"
        ? "保留任务流程，替换其中的回放脚本。保存修改后下轮生效。"
        : "仅执行绑定脚本，跳过原任务流程。请在开始任务前进入脚本所需的游戏场景。";
    private async Task BindTaskAsync()
    {
        if (_tasks.SelectedItem is not JsonNode task) return;
        var id = task["id"]!.GetValue<string>(); var mode = SelectedBindingMode;
        if (!await EnsureSavedAsync() || _vm.File == null) return;
        await _vm.Client.CallAsync("bind_task", new JsonObject { ["task_id"] = id, ["mode"] = mode });
        await RefreshLibraryAsync();
        _status.Text = "已将「" + _vm.File + "」绑定到「" + task["label"] + "」。保存修改后下轮生效。";
    }
    private async Task UnbindTaskAsync()
    {
        if (_tasks.SelectedItem is not JsonNode task || task["binding"]?["file"] is not JsonNode file) return;
        await _vm.Client.CallAsync("unbind_task", new JsonObject { ["task_id"] = task["id"]!.GetValue<string>(), ["expected_file"] = file.GetValue<string>() });
        await RefreshLibraryAsync(); _status.Text = "已解除绑定，任务恢复使用自身配置。";
    }
    private async Task StartRecordingAsync()
    {
        if (!await EnsureSavedAsync()) return;
        if (_windows.SelectedItem is not StudioWindow window) throw new InvalidOperationException("请先选择游戏窗口。");
        _recordingName = "录制-" + DateTime.Now.ToString("MMdd-HHmmss", CultureInfo.InvariantCulture);
        ScriptStudioGuard.BeginCapture();
        try
        {
            var snapshot = await _vm.Client.CallAsync("capture_start", new JsonObject { ["hwnd"] = (long)window.Handle, ["countdown_seconds"] = (int)(_countdown.Value ?? 3) });
            _vm.Capturing = true; RenderCapture(snapshot);
            _status.Text = "请切回游戏。F8 暂停 / 继续，F9 停止，F10 停止并保存。"; _poll.Start();
        }
        catch { ScriptStudioGuard.EndCapture(); throw; }
    }
    private void RenderCapture(JsonNode snapshot)
    {
        _state = snapshot["state"]?.GetValue<string>() ?? "idle";
        _captureState.Text = StudioCapturePresentation.Title(snapshot.AsObject());
        _captureHint.Text = snapshot["message"]?.GetValue<string>() ?? "";
        UpdateControls();
    }
    private async Task PollCaptureAsync()
    {
        UpdateControls();
        if (_polling || _busy || !_vm.Capturing) return;
        _polling = true;
        try
        {
            var snapshot = await _vm.Client.CallAsync("capture_status"); RenderCapture(snapshot);
            if (_state is "stopped" or "failed" or "closed")
            {
                await FinishRecordingAsync();
                if (!string.IsNullOrEmpty(snapshot["error"]?.GetValue<string>())) _status.Text = snapshot["error"]!.GetValue<string>();
                if (snapshot["save_requested"]?.GetValue<bool>() == true) await SaveAsync();
            }
        }
        catch (Exception error)
        {
            _status.Text = error.Message;
            if (_vm.Client.IsDisconnected) { _vm.Capturing = false; _state = "failed"; ScriptStudioGuard.EndCapture(); }
        }
        finally { _polling = false; UpdateControls(); }
    }
    private async Task FinishRecordingAsync()
    {
        if (!_vm.Capturing) return;
        if (_vm.Client.IsDisconnected) { _vm.Capturing = false; _state = "failed"; ScriptStudioGuard.EndCapture(); return; }
        if (_state == "countdown")
        {
            await _vm.Client.CallAsync("capture_cancel"); _vm.Capturing = false; _state = "idle";
            ScriptStudioGuard.EndCapture(); _captureState.Text = "准备就绪"; _captureHint.Text = "已取消倒计时，当前脚本保持原样。"; return;
        }
        await _vm.ChangeAsync("capture_stop"); _vm.Capturing = false; ScriptStudioGuard.EndCapture();
        _state = "stopped"; _captureState.Text = "录制完成"; _captureHint.Text = "草稿已送到时间轴，可检查后保存。";
        await _vm.ChangeAsync("rename", new JsonObject { ["name"] = _recordingName });
        _tabs.SelectedIndex = 1; _timeline.Fit(_timelineScroll.Bounds.Width);
        _status.Text = "录制已停止，内容保留为草稿。可以编辑、检查后保存。";
    }
    private async Task DeleteAsync()
    {
        if (!await EnsureInspectorAsync() || _selected == null) return;
        await _vm.ChangeAsync("delete", new JsonObject { ["id"] = _selected.Id });
    }
    private async Task SplitAsync()
    {
        if (!await EnsureInspectorAsync()) return;
        var result = await _vm.Client.CallAsync("split", new JsonObject { ["at_ms"] = Milliseconds(_cursor) });
        await RefreshLibraryAsync(); _status.Text = "已在键鼠全部释放的位置拆分，生成：" + string.Join("、", result["files"]!.AsArray().Select(n => n!.GetValue<string>()));
    }
    private Task ValidateAsync()
    {
        _status.Text = _vm.Document == null ? "请先选择脚本。" : _vm.Diagnostics.Count == 0 ? $"离线检查通过：{_vm.Operations.Count} 个操作，总时长 {_vm.Duration / 1000:0.000} 秒。" : string.Join("；", _vm.Diagnostics);
        return Task.CompletedTask;
    }
    private void UpdateControls()
    {
        var tasks = MaaProcessor.Processors.Any(p => p.IsTaskRunActive);
        var editable = !_busy && !_vm.Capturing && !tasks;
        _save.IsEnabled = editable && _vm.Document != null;
        _record.IsEnabled = !_busy && !_vm.Capturing && !tasks;
        _pause.IsEnabled = !_busy && _vm.Capturing && _state is "recording" or "paused" or "resume_countdown";
        _pause.Content = _state == "paused" ? "继续（3秒准备）" : _state == "resume_countdown" ? "取消继续" : "暂停";
        _stop.IsEnabled = !_busy && _vm.Capturing;
        _stop.Content = _state == "countdown" ? "取消倒计时" : "停止并编辑";
        _windows.IsEnabled = _countdown.IsEnabled = editable;
        if (_refreshWindows != null) _refreshWindows.IsEnabled = editable;
        if (_mark != null) _mark.IsEnabled = !_busy && _vm.Capturing && _state is "recording" or "paused";
        _library.IsEnabled = !_busy && !_vm.Capturing;
        _name.IsEnabled = editable && _vm.Document != null;
        _timeline.Editable = editable; _events.IsEnabled = editable;
        _inspector.IsEnabled = editable && (_selected != null || _adding);
        _apply.IsEnabled = editable && (_selected != null || _adding);
        _undo.IsEnabled = editable && _vm.Document?["can_undo"]?.GetValue<bool>() == true;
        _redo.IsEnabled = editable && _vm.Document?["can_redo"]?.GetValue<bool>() == true;
        _use.IsEnabled = editable && _vm.Document != null;
        _bindTask.IsEnabled = editable && _vm.Document != null && _tasks.SelectedItem != null;
        _unbindTask.IsEnabled = editable && (_tasks.SelectedItem as JsonNode)?["binding"] != null;
        _tasks.IsEnabled = _bindingMode.IsEnabled = editable;
        _editPage.IsEnabled = _vm.Document != null;
    }
    private async void OnShortcut(object? sender, KeyEventArgs e)
    {
        if (!e.KeyModifiers.HasFlag(KeyModifiers.Control) || _vm.Capturing) return;
        if (e.Key == Key.S) { e.Handled = true; await Run(SaveAsync); }
        else if (e.Key is Key.Z or Key.Y && e.Source is not TextBox && !(e.Source is Control input && input.GetVisualAncestors().OfType<TextBox>().Any()))
        { e.Handled = true; await Run(async () => { if (await EnsureInspectorAsync()) await _vm.ChangeAsync(e.Key == Key.Y || e.KeyModifiers.HasFlag(KeyModifiers.Shift) ? "redo" : "undo"); }); }
    }
    private async Task<int> AskAsync(string title, string message, string primary, string secondary)
    {
        if (TopLevel.GetTopLevel(this) is not Window owner) return 0;
        var dialog = new Window { Title = title, Width = 440, SizeToContent = SizeToContent.Height, CanResize = false,
            ShowInTaskbar = false, WindowStartupLocation = WindowStartupLocation.CenterOwner };
        var content = new StackPanel { Spacing = 20, Margin = new Thickness(24) };
        content.Children.Add(new TextBlock { Text = message, TextWrapping = TextWrapping.Wrap });
        var buttons = new WrapPanel { HorizontalAlignment = HorizontalAlignment.Right };
        foreach (var item in new[] { (primary, 1), (secondary, 2), ("取消", 0) })
        { var button = new Button { Content = item.Item1, Margin = new Thickness(4), Padding = new Thickness(12, 8) }; var result = item.Item2; button.Click += (_, _) => dialog.Close(result); buttons.Children.Add(button); }
        content.Children.Add(buttons); dialog.Content = content; return await dialog.ShowDialog<int>(owner);
    }
    private async void OnClosing(object? sender, WindowClosingEventArgs e)
    {
        if (_allowClose) return;
        if (_vm.Capturing || HasChanges)
        {
            e.Cancel = true;
            await Run(async () =>
            {
                if (_vm.Capturing) await FinishRecordingAsync();
                if (!await EnsureSavedAsync()) return;
                _allowClose = true; _poll.Stop(); _vm.Dispose(); _owner?.Close();
            });
        }
        else { _allowClose = true; _poll.Stop(); _vm.Dispose(); }
    }
}
