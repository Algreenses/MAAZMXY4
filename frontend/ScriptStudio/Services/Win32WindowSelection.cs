using System;
using System.Collections.Generic;

namespace MFAAvalonia.Services;

/// <summary>Rediscover a chosen window; never substitute an unrelated first item.</summary>
public static class Win32WindowSelection
{
    public static int SelectIndex(IReadOnlyList<(nint Handle, string Title, string ClassName)> windows,
        (nint Handle, string Title, string ClassName)? current, (string Title, string ClassName)? saved)
    {
        if (current is { } previous)
        {
            for (var i = 0; i < windows.Count; i++)
                if (previous.Handle != 0 && windows[i] == previous) return i;
            return UniqueIdentity(windows, previous.Title, previous.ClassName);
        }
        return saved is { } identity ? UniqueIdentity(windows, identity.Title, identity.ClassName) : -1;
    }

    private static int UniqueIdentity(IReadOnlyList<(nint Handle, string Title, string ClassName)> windows,
        string title, string className)
    {
        if (string.IsNullOrWhiteSpace(title) || string.IsNullOrWhiteSpace(className)) return -1;
        var match = -1;
        for (var i = 0; i < windows.Count; i++)
        {
            if (windows[i].Handle == 0 || windows[i].Title != title || windows[i].ClassName != className) continue;
            if (match != -1) return -1;
            match = i;
        }
        return match;
    }
}
