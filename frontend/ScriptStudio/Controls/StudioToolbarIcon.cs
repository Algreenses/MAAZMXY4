using System;
using Avalonia;
using Avalonia.Controls;
using Avalonia.Media;
using Avalonia.Media.Imaging;
using Avalonia.Platform;

namespace MFAAvalonia.Controls;

/// <summary>Render a glyph from the imagegen atlas without extra runtime files.</summary>
public sealed class StudioToolbarIcon : Control
{
    private static readonly Lazy<Bitmap> Atlas = new(() => new Bitmap(AssetLoader.Open(
        new Uri("avares://MFAAvalonia.Core/Assets/studio-toolbar-solid.png"))));
    private static readonly double[] CentersX = { 185, 480, 780, 1075 };
    private static readonly double[] CentersY = { 187, 480, 773, 1058 };
    public int Index { get; }
    public StudioToolbarIcon(int index) { Index = Math.Clamp(index, 0, 15); Width = Height = 24; }
    public override void Render(DrawingContext context)
    {
        var atlas = Atlas.Value;
        var scale = atlas.PixelSize.Width / 1254.0;
        var source = new Rect((CentersX[Index % 4] - 112) * scale,
            (CentersY[Index / 4] - 112) * scale, 224 * scale, 224 * scale);
        context.DrawImage(atlas, source, new Rect(Bounds.Size));
    }
}
