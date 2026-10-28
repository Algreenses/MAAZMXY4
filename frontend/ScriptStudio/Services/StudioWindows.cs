using System;
using System.Collections.Generic;
using System.Linq;
using Avalonia.Input;
using MFAAvalonia.Extensions.MaaFW;

namespace MFAAvalonia.Services;

public sealed record StudioWindow(nint Handle, string Title, string ClassName)
{
    public override string ToString() => string.IsNullOrWhiteSpace(Title) ? "游戏窗口" : Title;
}

public static class StudioWindows
{
    public static nint SelectedHandle => MaaProcessorManager.IsInstanceCreated
        ? MaaProcessorManager.Instance.Current.Config.DesktopWindow.HWnd : 0;

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

    public static IReadOnlyList<StudioWindow> List()
    {
        if (!OperatingSystem.IsWindows() || !MaaProcessorManager.IsInstanceCreated) return Array.Empty<StudioWindow>();
        var manager = MaaProcessorManager.Instance;
        var taskPage = manager.GetViewModel(manager.Current.InstanceId);
        return taskPage?.GetScriptRecordingWindows()
            .Select(window => new StudioWindow(window.Handle, window.Name, window.ClassName)).ToList()
            ?? new List<StudioWindow>();
    }
}
