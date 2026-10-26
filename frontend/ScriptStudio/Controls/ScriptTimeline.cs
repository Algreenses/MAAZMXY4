using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using Avalonia;
using Avalonia.Controls;
using Avalonia.Input;
using Avalonia.Media;
using Avalonia.Styling;
using Avalonia.VisualTree;
using MFAAvalonia.ViewModels.Pages;

namespace MFAAvalonia.Controls;

/// <summary>Native input tracks migrated from ZaoMengScript's recorder timeline.
/// The ruler only moves the cursor; it never executes game input.</summary>
public sealed class ScriptTimeline : Control
{
    private const double Header = 110, Ruler = 34, EndPadding = 24;
    private readonly List<(Rect Bounds, StudioOperation Operation)> _hits = new();
    private StudioOperation? _drag;
    private Point _origin;
    private double _previewStart, _previewDuration;
    private int _edge;
    private double _scale = 35;
    private double _rowHeight = 48, _viewportWidth = 600, _viewportHeight;
    private bool _fitted = true;
    private string? _hoveredId;
    private static readonly Cursor ResizeCursor = new(StandardCursorType.SizeWestEast);
    private static readonly Cursor MoveCursor = new(StandardCursorType.SizeAll);
    private static readonly Cursor HandCursor = new(StandardCursorType.Hand);
    private static readonly Cursor ArrowCursor = new(StandardCursorType.Arrow);
    private IReadOnlyList<StudioOperation> _operations = Array.Empty<StudioOperation>();
    private readonly List<string> _lanes = new();
    public string? SelectedId { get; set; }
    public bool Editable { get; set; } = true;
    public double CursorMs { get; private set; }
    public double DurationMs { get; private set; }
    public event Action<StudioOperation>? Selected;
    public event Action<StudioOperation, double, double>? Edited;
    public event Action<double>? CursorChanged;
    public ScriptTimeline() { ClipToBounds = true; Focusable = true; HorizontalAlignment = Avalonia.Layout.HorizontalAlignment.Left; VerticalAlignment = Avalonia.Layout.VerticalAlignment.Top; }
    public void Update(IReadOnlyList<StudioOperation> operations, double duration)
    {
        _operations = operations; DurationMs = duration; _hoveredId = null;
        // Retiming an operation must not move whole tracks under the pointer.
        var present = operations.Select(operation => operation.Lane).Distinct().ToList();
        _lanes.RemoveAll(lane => !present.Contains(lane));
        foreach (var lane in present) if (!_lanes.Contains(lane)) _lanes.Add(lane);
        ToolTip.SetTip(this, null);
        if (_fitted) FitScale();
        Resize();
    }
    public void Zoom(double factor) { _fitted = false; _scale = Math.Clamp(_scale * factor, .001, 800); Resize(); }
    public void AdjustTrackHeight(double delta) { _rowHeight = Math.Clamp(_rowHeight + delta, 28, 88); Resize(); }
    public void SetViewport(double width, double height)
    {
        if (width < 150 || height < 1 || Math.Abs(width - _viewportWidth) < .5 && Math.Abs(height - _viewportHeight) < .5) return;
        _viewportWidth = width; _viewportHeight = height;
        if (_fitted) FitScale();
        Resize();
    }
    public void SetCursor(double time) { CursorMs = Math.Max(0, time); InvalidateVisual(); }
    public void Fit(double width)
    {
        if (width < 150) return;
        _viewportWidth = width; _fitted = true; FitScale();
        Resize();
    }
    private void FitScale() => _scale = Math.Clamp((_viewportWidth - Header - EndPadding) / Math.Max(.001, DurationMs / 1000), .001, 800);
    private void Resize()
    {
        Width = Math.Max(Header + DurationMs / 1000 * _scale + EndPadding, _viewportWidth);
        Height = Math.Max(Ruler + Math.Max(3, _lanes.Count) * _rowHeight + 12, _viewportHeight);
        InvalidateVisual();
    }
    public override void Render(DrawingContext context)
    {
        base.Render(context);
        var dark = ActualThemeVariant == ThemeVariant.Dark;
        var background = new SolidColorBrush(dark ? Color.Parse("#172033") : Color.Parse("#F4F7FC"));
        var text = new SolidColorBrush(dark ? Color.Parse("#CDD8ED") : Color.Parse("#40516B"));
        var line = new Pen(new SolidColorBrush(dark ? Color.Parse("#29374D") : Color.Parse("#DDE5F0")));
        var scroll = this.GetVisualAncestors().OfType<ScrollViewer>().FirstOrDefault();
        var left = scroll?.Offset.X ?? 0;
        var visibleWidth = Math.Max(1, scroll?.Viewport.Width ?? Bounds.Width);
        context.FillRectangle(background, new Rect(left, 0, visibleWidth, Height));
        var lanes = _lanes;
        var spacing = _scale >= 100 ? .5 : _scale >= 30 ? 1 : _scale >= 6 ? 5 : _scale >= 1 ? 30 : Math.Max(1, Math.Ceiling(DurationMs / 1000 / 12));
        for (var second = Math.Max(0, Math.Floor((left - Header) / _scale / spacing) * spacing);
             second * _scale < left + visibleWidth - Header; second += spacing)
        {
            var x = Header + second * _scale;
            context.DrawLine(line, new Point(x, Ruler), new Point(x, Height));
            Text(context, second.ToString("0.#", CultureInfo.InvariantCulture) + "s", x + 3, 8, text, 11);
        }
        for (var i = 0; i < Math.Max(3, lanes.Count); i++)
        {
            var y = Ruler + i * _rowHeight;
            context.DrawLine(line, new Point(left, y), new Point(left + visibleWidth, y));
        }
        _hits.Clear();
        foreach (var operation in _operations)
        {
            var start = operation == _drag ? _previewStart : operation.Start;
            var duration = operation == _drag ? _previewDuration : operation.Duration;
            var rect = new Rect(Header + start / 1000 * _scale, Ruler + lanes.IndexOf(operation.Lane) * _rowHeight + 6,
                Math.Max(12, duration / 1000 * _scale), _rowHeight - 12);
            if (rect.X > left + visibleWidth || rect.Right < Math.Max(Header, left)) continue;
            var color = operation.Kind == "marker" ? "#D89A25" : operation.Lane == "鼠标" ? "#17A398" : "#627AF2";
            context.DrawRectangle(new SolidColorBrush(Color.Parse(color)), operation.Id == SelectedId ? new Pen(Brushes.White, 2) : null, rect, 5, 5);
            if (rect.Width > 42) Text(context, operation.Label, rect.X + 9, rect.Y + Math.Max(2, (rect.Height - 16) / 2), Brushes.White, 12);
            if (operation.Id == SelectedId && operation.Duration > 0)
            {
                var grip = new Pen(Brushes.White, 2);
                var middle = rect.Y + rect.Height / 2;
                context.DrawLine(grip, new Point(rect.X + 4, middle - 5), new Point(rect.X + 4, middle + 5));
                context.DrawLine(grip, new Point(rect.Right - 4, middle - 5), new Point(rect.Right - 4, middle + 5));
            }
            _hits.Add((rect, operation));
        }
        var cursor = Header + CursorMs / 1000 * _scale;
        context.DrawLine(new Pen(new SolidColorBrush(Color.Parse("#E56766")), 2), new Point(cursor, 0), new Point(cursor, Height));
        context.FillRectangle(background, new Rect(left, 0, Header, Height));
        context.DrawLine(line, new Point(left + Header, 0), new Point(left + Header, Height));
        Text(context, "操作轨道", left + 12, 9, text, 12);
        for (var i = 0; i < lanes.Count; i++) Text(context, lanes[i], left + 12, Ruler + i * _rowHeight + (_rowHeight - 16) / 2, text, 12);
    }
    private static void Text(DrawingContext context, string value, double x, double y, IBrush brush, double size)
    {
        var formatted = new FormattedText(value, CultureInfo.CurrentCulture, FlowDirection.LeftToRight, Typeface.Default, size, brush);
        context.DrawText(formatted, new Point(x, y));
    }
    protected override void OnPointerPressed(PointerPressedEventArgs e)
    {
        base.OnPointerPressed(e);
        if (!e.GetCurrentPoint(this).Properties.IsLeftButtonPressed) return;
        Focus();
        var position = e.GetPosition(this);
        if (position.X < Header + (this.GetVisualAncestors().OfType<ScrollViewer>().FirstOrDefault()?.Offset.X ?? 0)) return;
        var hit = _hits.LastOrDefault(h => h.Bounds.Contains(position));
        if (hit.Operation != null)
        {
            SelectedId = hit.Operation.Id; Selected?.Invoke(hit.Operation);
            if (Editable)
            {
                _drag = hit.Operation; _origin = position;
                _previewStart = _drag.Start; _previewDuration = _drag.Duration;
                _edge = EdgeAt(hit.Bounds, _drag.Duration, position.X);
                e.Pointer.Capture(this);
            }
        }
        else
        {
            CursorMs = Math.Clamp((position.X - Header) / _scale * 1000, 0, Math.Max(0, DurationMs));
            CursorChanged?.Invoke(CursorMs);
        }
        InvalidateVisual(); e.Handled = true;
    }
    protected override void OnPointerMoved(PointerEventArgs e)
    {
        base.OnPointerMoved(e);
        if (_drag == null) { UpdateHover(e.GetPosition(this)); return; }
        Cursor = _edge == 0 ? MoveCursor : ResizeCursor;
        var delta = (e.GetPosition(this).X - _origin.X) / _scale * 1000;
        var snap = e.KeyModifiers.HasFlag(KeyModifiers.Alt) ? 1 : 10;
        delta = Math.Round(delta / snap) * snap;
        if (_edge == 1) { _previewStart = _drag.Start; _previewDuration = Math.Max(0, _drag.Duration + delta); }
        else if (_edge == -1)
        {
            _previewStart = Math.Clamp(_drag.Start + delta, 0, _drag.Start + _drag.Duration);
            _previewDuration = _drag.Start + _drag.Duration - _previewStart;
        }
        else { _previewStart = Math.Max(0, _drag.Start + delta); _previewDuration = _drag.Duration; }
        InvalidateVisual();
    }
    protected override void OnPointerReleased(PointerReleasedEventArgs e)
    {
        base.OnPointerReleased(e);
        var drag = _drag; _drag = null; e.Pointer.Capture(null);
        if (drag != null && (drag.Start != _previewStart || drag.Duration != _previewDuration))
            Edited?.Invoke(drag, Math.Round(_previewStart), Math.Round(_previewDuration));
        InvalidateVisual();
    }
    private static int EdgeAt(Rect bounds, double duration, double x)
    {
        if (duration <= 0) return 0;
        // Keep a draggable center on very short blocks while enlarging normal handles to 10px.
        var edgeWidth = Math.Min(10, Math.Max(3, (bounds.Width - 6) / 2));
        return x <= bounds.X + edgeWidth ? -1 : x >= bounds.Right - edgeWidth ? 1 : 0;
    }
    private void UpdateHover(Point position)
    {
        var left = this.GetVisualAncestors().OfType<ScrollViewer>().FirstOrDefault()?.Offset.X ?? 0;
        var hit = position.X < left + Header ? default : _hits.LastOrDefault(h => h.Bounds.Contains(position));
        Cursor = !Editable || hit.Operation == null ? ArrowCursor : hit.Operation.Duration <= 0 ? HandCursor : EdgeAt(hit.Bounds, hit.Operation.Duration, position.X) == 0 ? MoveCursor : ResizeCursor;
        if (_hoveredId == hit.Operation?.Id) return;
        _hoveredId = hit.Operation?.Id;
        ToolTip.SetTip(this, hit.Operation == null ? null : $"{hit.Operation.Label}\n开始 {hit.Operation.Start / 1000:0.000} 秒 · 持续 {hit.Operation.Duration / 1000:0.000} 秒");
    }
    protected override void OnPointerExited(PointerEventArgs e)
    {
        if (_drag == null) { _hoveredId = null; Cursor = ArrowCursor; ToolTip.SetTip(this, null); }
        base.OnPointerExited(e);
    }
    protected override void OnPointerCaptureLost(PointerCaptureLostEventArgs e) { _drag = null; Cursor = ArrowCursor; InvalidateVisual(); base.OnPointerCaptureLost(e); }
    protected override void OnPointerWheelChanged(PointerWheelEventArgs e)
    {
        if (e.KeyModifiers.HasFlag(KeyModifiers.Control)) { Zoom(e.Delta.Y > 0 ? 1.25 : .8); e.Handled = true; }
        else if (e.KeyModifiers.HasFlag(KeyModifiers.Shift)) { AdjustTrackHeight(e.Delta.Y > 0 ? 8 : -8); e.Handled = true; }
        base.OnPointerWheelChanged(e);
    }
}
