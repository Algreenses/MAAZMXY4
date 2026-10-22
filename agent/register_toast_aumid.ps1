param(
    [Parameter(Mandatory = $true)][string]$Aumid,
    [Parameter(Mandatory = $true)][string]$AppName,
    [Parameter(Mandatory = $true)][string]$IconPath,
    [Parameter(Mandatory = $true)][string]$TargetPath
)

# Create a Start Menu shortcut carrying System.AppUserModel.ID.
#
# Windows Toast notifications only trust AUMIDs that have a Start Menu shortcut.
# Writing DisplayName under HKCU\Software\Classes\AppUserModelId is NOT enough:
# the toast header still shows "Python" and the big image falls back to the
# default blue information icon.
#
# This script is idempotent; re-running just overwrites the shortcut.
# NOTE: keep every string in this file ASCII-only. PowerShell 5.1 reads a
# BOM-less UTF-8 script as ANSI, which mangles non-ASCII literals badly enough
# to corrupt the embedded C# source.

$ErrorActionPreference = 'Stop'

$lnkDir = Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs'
if (-not (Test-Path $lnkDir)) {
    New-Item -ItemType Directory -Path $lnkDir -Force | Out-Null
}
$lnkPath = Join-Path $lnkDir ($AppName + '.lnk')

$ws = New-Object -ComObject WScript.Shell
$lnk = $ws.CreateShortcut($lnkPath)
$lnk.TargetPath = $TargetPath
if (Test-Path $IconPath) {
    $lnk.IconLocation = ($IconPath + ',0')
}
$lnk.Description = $AppName
$lnk.WorkingDirectory = (Split-Path -Parent $TargetPath)
$lnk.Save()
Write-Output ('shortcut: ' + $lnkPath)

$src = @'
using System;
using System.Runtime.InteropServices;
using System.Runtime.InteropServices.ComTypes;

public static class AppIdSetter
{
    [StructLayout(LayoutKind.Sequential, Pack = 4)]
    public struct PROPERTYKEY { public Guid fmtid; public int pid; }

    [StructLayout(LayoutKind.Explicit)]
    public struct PROPVARIANT
    {
        [FieldOffset(0)] public ushort vt;
        [FieldOffset(8)] public IntPtr p;
    }

    [ComImport, Guid("886D8EEB-8CF2-4446-8D02-CDBA1DBDCF99"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    public interface IPropertyStore
    {
        uint GetCount(out uint cProps);
        uint GetAt(uint iProp, out PROPERTYKEY pkey);
        uint GetValue(ref PROPERTYKEY key, out PROPVARIANT pv);
        uint SetValue(ref PROPERTYKEY key, ref PROPVARIANT pv);
        uint Commit();
    }

    [DllImport("shell32.dll", CharSet = CharSet.Unicode)]
    static extern int SHGetPropertyStoreFromParsingName(
        string pszPath, IntPtr pbc, uint flags, ref Guid riid, out IPropertyStore ppv);

    public static void SetAppId(string lnk, string appId)
    {
        Guid iid = new Guid("886D8EEB-8CF2-4446-8D02-CDBA1DBDCF99");
        IPropertyStore store;
        // flags must be GPS_READWRITE (2). Opening with 0 (GPS_DEFAULT) gives a
        // read-only store and Commit throws STG_E_ACCESSDENIED 0x80030005.
        int hr = SHGetPropertyStoreFromParsingName(lnk, IntPtr.Zero, 2, ref iid, out store);
        if (hr != 0) throw new Exception("SHGetPropertyStore failed: 0x" + hr.ToString("X8"));

        PROPERTYKEY key = new PROPERTYKEY();
        key.fmtid = new Guid("9F4C2855-9F79-4B39-A8D0-E1D42DE1D5F3");
        key.pid = 5; // System.AppUserModel.ID

        PROPVARIANT pv = new PROPVARIANT();
        pv.vt = 31; // VT_LPWSTR
        pv.p = Marshal.StringToCoTaskMemUni(appId);
        try
        {
            store.SetValue(ref key, ref pv);
            store.Commit();
        }
        finally
        {
            Marshal.FreeCoTaskMem(pv.p);
        }
    }
}
'@

Add-Type -TypeDefinition $src -Language CSharp
[AppIdSetter]::SetAppId($lnkPath, $Aumid)
Write-Output ('aumid: ' + $Aumid)
