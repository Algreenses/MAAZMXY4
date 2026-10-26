using System;
using System.Collections.Generic;
using System.Runtime.InteropServices;
using System.Text;
using Avalonia.Input;

namespace MFAAvalonia.Services;

public sealed record StudioWindow(nint Handle, string Title, string ClassName)
{
    public override string ToString() => string.IsNullOrWhiteSpace(Title) ? "游戏窗口" : Title;
}

public static class StudioWindows
{
    /// <summary>Translate supported logical keys without coupling Core to Avalonia.Win32.</summary>
    public static int VirtualKeyFromKey(Key key)
    {
        if (key >= Key.A && key <= Key.Z) return 0x41 + (int)key - (int)Key.A;
        if (key >= Key.D0 && key <= Key.D9) return 0x30 + (int)key - (int)Key.D0;
        if (key >= Key.F1 && key <= Key.F24) return 0x70 + (int)key - (int)Key.F1;
        if (key >= Key.NumPad0 && key <= Key.NumPad9) return 0x60 + (int)key - (int)Key.NumPad0;

        // Enum aliases can stringify to either name; both spellings map to the same VK.
        return key.ToString() switch
        {
            "Back" => 0x08, "Tab" => 0x09, "Clear" => 0x0C,
            "Enter" or "Return" => 0x0D, "Pause" => 0x13,
            "CapsLock" or "Capital" => 0x14, "Escape" => 0x1B, "Space" => 0x20,
            "PageUp" or "Prior" => 0x21, "PageDown" or "Next" => 0x22,
            "End" => 0x23, "Home" => 0x24, "Left" => 0x25, "Up" => 0x26,
            "Right" => 0x27, "Down" => 0x28, "PrintScreen" or "Snapshot" => 0x2C,
            "Insert" => 0x2D, "Delete" => 0x2E,
            "LWin" => 0x5B, "RWin" => 0x5C, "Apps" => 0x5D,
            "Multiply" => 0x6A, "Add" => 0x6B, "Separator" => 0x6C,
            "Subtract" => 0x6D, "Decimal" => 0x6E, "Divide" => 0x6F,
            "NumLock" => 0x90, "Scroll" => 0x91,
            "LeftShift" => 0xA0, "RightShift" => 0xA1,
            "LeftCtrl" or "LeftControl" => 0xA2, "RightCtrl" or "RightControl" => 0xA3,
            "LeftAlt" => 0xA4, "RightAlt" => 0xA5,
            "OemSemicolon" or "Oem1" => 0xBA, "OemPlus" => 0xBB,
            "OemComma" => 0xBC, "OemMinus" => 0xBD, "OemPeriod" => 0xBE,
            "OemQuestion" or "Oem2" => 0xBF, "OemTilde" or "Oem3" => 0xC0,
            "OemOpenBrackets" or "Oem4" => 0xDB, "OemPipe" or "Oem5" => 0xDC,
            "OemCloseBrackets" or "Oem6" => 0xDD, "OemQuotes" or "Oem7" => 0xDE,
            "OemBackslash" or "Oem102" => 0xE2,
            _ => 0
        };
    }

    private delegate bool EnumWindow(nint hwnd, nint parameter);
    [DllImport("user32.dll")] private static extern bool EnumWindows(EnumWindow callback, nint parameter);
    [DllImport("user32.dll")] private static extern bool IsWindowVisible(nint hwnd);
    [DllImport("user32.dll", CharSet = CharSet.Unicode)] private static extern int GetWindowText(nint hwnd, StringBuilder text, int length);
    [DllImport("user32.dll", CharSet = CharSet.Unicode)] private static extern int GetClassName(nint hwnd, StringBuilder text, int length);
    public static IReadOnlyList<StudioWindow> List()
    {
        var windows = new List<StudioWindow>();
        if (!OperatingSystem.IsWindows()) return windows;
        EnumWindows((hwnd, _) =>
        {
            if (!IsWindowVisible(hwnd)) return true;
            var title = new StringBuilder(512); var className = new StringBuilder(256);
            GetWindowText(hwnd, title, title.Capacity); GetClassName(hwnd, className, className.Capacity);
            if (title.ToString().Contains("造梦") || className.ToString().Contains("ApolloRuntimeContentWindow"))
                windows.Add(new StudioWindow(hwnd, title.ToString(), className.ToString()));
            return true;
        }, 0);
        return windows;
    }
}
