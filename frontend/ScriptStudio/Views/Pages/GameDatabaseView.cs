using System;
using System.IO;
using System.Linq;
using System.Threading.Tasks;
using Avalonia;
using Avalonia.Controls;
using Avalonia.Controls.Templates;
using Avalonia.Layout;
using Avalonia.Media;
using Avalonia.Media.Imaging;
using Avalonia.Threading;
using MFAAvalonia.Services;
using MFAAvalonia.ViewModels.Pages;

namespace MFAAvalonia.Views.Pages;

/// <summary>Offline PC-game catalogue, using the host's native navigation/theme.</summary>
public sealed class GameDatabaseView : UserControl
{
    private const int PageSize = 60;
    private readonly GameDatabaseViewModel _vm;
    private readonly TextBox _search = new() { Watermark = "搜索名称、获得地点或技能（空格组合关键词）" };
    private readonly ComboBox _category = new() { ItemsSource = new[] { "全部", "装备", "道具", "Boss" }, SelectedIndex = 0, MinWidth = 100 };
    private readonly ComboBox _subtype = new() { ItemsSource = new[] { "全部" }, SelectedIndex = 0, MinWidth = 130 };
    private readonly ListBox _results = new();
    private readonly StackPanel _details = new() { Spacing = 12, Margin = new Thickness(18, 8) };
    private readonly TextBlock _status = new() { TextWrapping = TextWrapping.Wrap };
    private readonly TextBlock _page = new();
    private readonly Button _previous = new() { Content = "上一页" }, _next = new() { Content = "下一页" }, _retry = new() { Content = "重新加载", IsVisible = false };
    private readonly Grid _body = new() { ColumnDefinitions = new ColumnDefinitions("280,8,*") };
    private readonly GridSplitter _splitter = new() { ResizeDirection = GridResizeDirection.Columns, HorizontalAlignment = HorizontalAlignment.Stretch };
    private readonly ScrollViewer _detailScroll;
    private readonly DispatcherTimer _debounce = new() { Interval = TimeSpan.FromMilliseconds(160) };
    private GameDatabaseCatalog? _catalog;
    private GameDatabaseEntry[] _matches = [];
    private Bitmap? _bitmap;
    private int _pageIndex;
    private bool _loading, _filtering, _filterPending;
    private bool? _narrow;

    public GameDatabaseView(GameDatabaseViewModel viewModel)
    {
        _vm = viewModel; DataContext = viewModel;
        _detailScroll = new ScrollViewer { Content = _details, HorizontalScrollBarVisibility = Avalonia.Controls.Primitives.ScrollBarVisibility.Disabled };
        _results.ItemTemplate = new FuncDataTemplate<GameDatabaseEntry>((entry, _) => new StackPanel { Spacing = 3, Margin = new Thickness(6), Children =
        {
            new TextBlock { Text = entry?.Name, FontWeight = FontWeight.SemiBold, TextWrapping = TextWrapping.Wrap },
            new TextBlock { Text = entry == null ? "" : $"{entry.Category} · {entry.Subtype}", Opacity = .65, FontSize = 12 },
        }});
        var root = new Grid { RowDefinitions = new RowDefinitions("Auto,Auto,*,Auto"), Margin = new Thickness(20), RowSpacing = 14 };
        var heading = new StackPanel { Spacing = 6 };
        heading.Children.Add(new TextBlock { Text = "游戏数据库", FontSize = 26, FontWeight = FontWeight.Bold });
        heading.Children.Add(new TextBlock { Text = "装备与道具的属性、获取方式，以及 Boss 技能和数值。", Opacity = .7, TextWrapping = TextWrapping.Wrap });
        root.Children.Add(heading);
        var filters = new Grid { ColumnDefinitions = new ColumnDefinitions("*,Auto,Auto"), ColumnSpacing = 8 };
        filters.Children.Add(_search); Grid.SetColumn(_category, 1); filters.Children.Add(_category); Grid.SetColumn(_subtype, 2); filters.Children.Add(_subtype);
        Grid.SetRow(filters, 1); root.Children.Add(filters);
        _body.Children.Add(_results); Grid.SetColumn(_splitter, 1); _body.Children.Add(_splitter); Grid.SetColumn(_detailScroll, 2); _body.Children.Add(_detailScroll);
        Grid.SetRow(_body, 2); root.Children.Add(_body);
        var footer = new StackPanel { Spacing = 7 };
        footer.Children.Add(new StackPanel { Orientation = Orientation.Horizontal, Spacing = 10, Children = { _previous, _page, _next, _retry } });
        footer.Children.Add(_status); Grid.SetRow(footer, 3); root.Children.Add(footer);
        Content = root;
        _debounce.Tick += (_, _) => { _debounce.Stop(); Filter(); };
        _search.TextChanged += (_, _) => { _filterPending = true; _debounce.Stop(); _debounce.Start(); };
        _category.SelectionChanged += (_, _) => { if (!_filtering) { UpdateSubtypes(); Filter(); } };
        _subtype.SelectionChanged += (_, _) => { if (!_filtering) Filter(); };
        _results.SelectionChanged += (_, _) => ShowDetails(_results.SelectedItem as GameDatabaseEntry);
        _previous.Click += (_, _) => { _pageIndex--; RenderPage(); };
        _next.Click += (_, _) => { _pageIndex++; RenderPage(); };
        _retry.Click += async (_, _) => await LoadAsync();
        AttachedToVisualTree += async (_, _) =>
        {
            if (_catalog == null) await LoadAsync();
            else if (_filterPending) Filter();
            else ShowDetails(_results.SelectedItem as GameDatabaseEntry);
        };
        DetachedFromVisualTree += (_, _) => { _debounce.Stop(); ReleaseBitmap(); };
        SizeChanged += (_, _) => ResizePage();
        ShowDetails(null);
    }
    private async Task LoadAsync()
    {
        if (_loading) return;
        _loading = true; _retry.IsVisible = false; _status.Text = "正在载入离线数据库…";
        try { _catalog = await _vm.LoadAsync(); UpdateSubtypes(); Filter(); }
        catch (Exception error) { _status.Text = "数据库载入失败：" + error.Message; _retry.IsVisible = true; }
        finally { _loading = false; }
    }
    private void UpdateSubtypes()
    {
        _filtering = true;
        var category = _category.SelectedItem as string ?? "全部";
        _subtype.ItemsSource = new[] { "全部" }.Concat((_catalog?.Entries ?? []).Where(e => category == "全部" || e.Category == category)
            .Select(e => e.Subtype).Distinct().OrderBy(s => s, StringComparer.Ordinal)).ToArray();
        _subtype.SelectedIndex = 0; _filtering = false;
    }
    private void Filter()
    {
        if (_catalog == null) return;
        _filterPending = false;
        _matches = _catalog.Search(_search.Text, _category.SelectedItem as string ?? "全部", _subtype.SelectedItem as string ?? "全部");
        _pageIndex = 0; RenderPage();
        _status.Text = $"{_catalog.Game} · 共 {_catalog.Entries.Count:N0} 条 · 当前 {_matches.Length:N0} 条 · 资料整理于 {_catalog.CheckedOn}";
    }
    private void RenderPage()
    {
        var count = Math.Max(1, (_matches.Length + PageSize - 1) / PageSize);
        _pageIndex = Math.Clamp(_pageIndex, 0, count - 1);
        _results.ItemsSource = _matches.Skip(_pageIndex * PageSize).Take(PageSize).ToArray();
        _results.SelectedIndex = _matches.Length == 0 ? -1 : 0;
        _previous.IsEnabled = _pageIndex > 0; _next.IsEnabled = _pageIndex + 1 < count;
        _page.Text = $"第 {_pageIndex + 1} / {count} 页";
        if (_matches.Length == 0) ShowDetails(null);
    }
    private void ShowDetails(GameDatabaseEntry? entry)
    {
        ReleaseBitmap(); _details.Children.Clear(); _detailScroll.Offset = default;
        if (entry == null) { AddText(_catalog == null ? "载入后选择条目查看详细资料。" : "没有匹配条目，请减少关键词或调整分类。"); return; }
        var header = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 14 };
        if (_catalog != null && entry.Icon != null)
        {
            try
            {
                var path = Path.Combine(_catalog.Root, entry.Icon.Replace('/', Path.DirectorySeparatorChar));
                using var stream = File.OpenRead(path);
                _bitmap = Bitmap.DecodeToWidth(stream, 80);
                header.Children.Add(new Image { Source = _bitmap, Width = 64, Height = 64, Stretch = Stretch.Uniform });
            }
            catch (Exception error) when (error is IOException or ArgumentException or NotSupportedException) { /* Text remains usable when an optional icon is missing. */ }
        }
        header.Children.Add(new TextBlock { Text = entry.Name, FontSize = 23, FontWeight = FontWeight.Bold, VerticalAlignment = VerticalAlignment.Center, TextWrapping = TextWrapping.Wrap });
        _details.Children.Add(header);
        AddText($"{entry.Category} · {entry.Subtype}", true);
        foreach (var fact in entry.Facts) { AddTitle(fact.Label); AddText(fact.Value); }
        if (entry.Category == "Boss" || entry.Skills.Length > 0)
        {
            AddTitle("技能");
            if (entry.Skills.Length == 0) AddText("官方条目暂未提供可整理的技能，请查阅下方来源。", true);
            foreach (var skill in entry.Skills) { AddTitle(skill.Name, 14); AddText(skill.Description); }
        }
        if (entry.ConfigVariants.Length > 0)
        {
            AddTitle("客户端配置 · 2026-10-19");
            AddText("以下为配置基准值。分号分隔区间端点；比例保留小数（0.05 = 5%）。关卡、等级、形态与实战修正可能改变数值。", true);
            foreach (var variant in entry.ConfigVariants)
            {
                var title = "记录 " + variant.Id + (string.IsNullOrEmpty(variant.Level) ? "" : " · 等级 " + variant.Level);
                if (variant.Locations.Length > 0) title += " · " + string.Join("、", variant.Locations);
                var block = new StackPanel { Spacing = 8 };
                foreach (var stat in variant.Stats) block.Children.Add(new SelectableTextBlock { Text = stat.Label + "：" + stat.Value, TextWrapping = TextWrapping.Wrap });
                _details.Children.Add(new Expander { Header = title, Content = block, IsExpanded = entry.ConfigVariants.Length == 1, HorizontalAlignment = HorizontalAlignment.Stretch });
            }
        }
        else if (!entry.Facts.Any(f => f.Label.Contains("属性") || f.Label is "生命" or "血量" or "攻击"))
            AddText("数值：来源未提供适用的数值，不以 0 代替。", true);
        foreach (var fact in entry.WikiFacts) { AddTitle("社区参考 · " + fact.Label); AddText(fact.Value); }
        AddTitle("资料来源");
        AddText("官方图鉴优先；社区资料用于补充。不同日期的数值分别展示，活动获取方式可能已变化。", true);
        foreach (var reference in entry.References)
        {
            // Binary client snapshots are provenance, not links that should
            // cause an executable SWF download when consulting a database.
            AddText(reference.Title + (string.IsNullOrEmpty(reference.Date) ? "" : " · " + reference.Date), true);
            if (reference.Kind == "client") continue;
            var button = new Button { Content = "打开来源页面", HorizontalAlignment = HorizontalAlignment.Left };
            button.Click += async (_, _) =>
            {
                if (!GameDatabaseCatalog.IsReferenceUrl(reference.Url)) return;
                try { if (TopLevel.GetTopLevel(this) is { } top) await top.Launcher.LaunchUriAsync(new Uri(reference.Url)); }
                catch (Exception error) { _status.Text = "无法打开来源：" + error.Message; }
            };
            _details.Children.Add(button);
        }
    }
    private void AddTitle(string text, double size = 16) => _details.Children.Add(new TextBlock { Text = text, FontWeight = FontWeight.SemiBold, FontSize = size, Margin = new Thickness(0, 8, 0, 0), TextWrapping = TextWrapping.Wrap });
    private void AddText(string text, bool secondary = false) => _details.Children.Add(new SelectableTextBlock { Text = text, TextWrapping = TextWrapping.Wrap, Opacity = secondary ? .7 : 1 });
    private void ReleaseBitmap() { _bitmap?.Dispose(); _bitmap = null; }
    private void ResizePage()
    {
        var narrow = Bounds.Width < 760;
        if (_narrow == narrow) return;
        _narrow = narrow;
        _body.ColumnDefinitions = new ColumnDefinitions(narrow ? "*" : "280,8,*");
        _body.RowDefinitions = new RowDefinitions(narrow ? "200,8,*" : "*");
        Grid.SetColumn(_splitter, narrow ? 0 : 1); Grid.SetColumn(_detailScroll, narrow ? 0 : 2);
        Grid.SetRow(_splitter, narrow ? 1 : 0); Grid.SetRow(_detailScroll, narrow ? 2 : 0);
        _splitter.ResizeDirection = narrow ? GridResizeDirection.Rows : GridResizeDirection.Columns;
    }
}
