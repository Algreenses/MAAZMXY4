"""右下角托盘气泡通知 —— 纯 ctypes，零依赖。

MAA 的 agent 进程没法调用客户端的通知接口（`NotificationType` 只有
Starting / Succeeded / Failed，不能带自定义文本），所以这里自己弹一个
Windows 托盘气泡，效果与客户端「任务完成」的右下角提示基本一致。

用法::

    from toast import toast
    toast("战斗录制", "已开始录制，按 F9 停止")

注意：Windows 的「专注助手 / 勿扰模式」可能会把气泡折叠进通知中心，
这是系统行为，本项目无法绕过。
"""

# ---------------------------------------------------------------- WinRT Toast
# Windows 10/11 的系统通知，和 MAA 客户端「任务已完成」那条是同一套 API：
# 带「通知」标题栏、应用图标、粗体标题、正文。
# 导入失败（没装 winrt）就退回下面的托盘气泡。

AUMID = "MaaFramework.AutoAdventure"
APP_NAME = "MAA造梦西游4"
# 承载 AUMID 的那个开始菜单快捷方式的名字。
# ⚠️ 绝对不能和用户自己建的「MAA造梦西游4」重名 —— 重名会把人家指向
# MAA造梦西游4.exe 的快捷方式覆盖掉，表现为「开始菜单里点 MAA 打不开了」。
LNK_NAME = "MAA造梦西游4 通知支持"

# ⚠️ ctypes 必须**先于 winrt** 导入。
# winrt 的 .pyd 先加载时会把进程的 DLL 状态搞乱，之后任何
# `import ctypes.wintypes` 都会抛 OSError [WinError 1114]
# 「动态链接库(DLL)初始化例程失败」，连带把整个 agent 拖到起不来。
import ctypes
from ctypes import wintypes

_winrt_cache = {"loaded": False, "mod": None}


def _winrt():
    """延迟导入 winrt。

    ⚠️ 绝对不能在模块顶层 import。
    winrt 的 .pyd 一加载就会把进程的 DLL 状态搞乱，之后 maa 再去
    `WinDLL(MaaAgentServer.dll)` 就报 `OSError [WinError 1114]`
    「动态链接库(DLL)初始化例程失败」—— 注册 CustomAction 时正是这一步，
    于是整个 agent 直接起不来（客户端上显示「agent 启动失败」）。

    延迟到真正要弹通知的那一刻（此时 maa 的注册早就完成了）再导入就安全，
    代价只是第一条通知多花几十毫秒。
    """
    if _winrt_cache["loaded"]:
        return _winrt_cache["mod"]
    _winrt_cache["loaded"] = True
    try:
        from winrt.windows.data.xml.dom import XmlDocument
        from winrt.windows.ui.notifications import (
            ToastNotification,
            ToastNotificationManager,
            ToastTemplateType,
        )

        _winrt_cache["mod"] = {
            "XmlDocument": XmlDocument,
            "ToastNotification": ToastNotification,
            "ToastNotificationManager": ToastNotificationManager,
            "ToastTemplateType": ToastTemplateType,
        }
    except Exception:  # noqa: BLE001 - 没装 winrt 就退回托盘气泡
        _winrt_cache["mod"] = None
    return _winrt_cache["mod"]

import os

_aumid_done = [False]


def _find_icon() -> str:
    """找一个能当通知图标的 png（项目自带的 256x256 图标）。"""
    here = os.path.dirname(os.path.abspath(__file__))
    for rel in (
        os.path.join("..", "docs", "images", "icon.png"),
        "icon.png",
        os.path.join("..", "assets", "adventure_icon.png"),
    ):
        p = os.path.normpath(os.path.join(here, rel))
        if os.path.exists(p):
            return p
    return ""


def register_aumid(icon_path: str = "") -> bool:
    """在 HKCU 注册应用标识，让通知里显示自己的名字/图标。

    只写两个字符串值，不需要管理员权限。失败也不影响弹通知（只是名字显示成 AUMID）。
    """
    try:
        import winreg

        key = winreg.CreateKeyEx(
            winreg.HKEY_CURRENT_USER,
            r"Software\Classes\AppUserModelId\%s" % AUMID,
            0,
            winreg.KEY_SET_VALUE,
        )
        winreg.SetValueEx(key, "DisplayName", 0, winreg.REG_SZ, APP_NAME)
        if icon_path:
            winreg.SetValueEx(key, "IconUri", 0, winreg.REG_SZ, icon_path)
        winreg.CloseKey(key)
        return True
    except Exception:
        return False


def _find_ico() -> str:
    """找一个能当快捷方式图标的 .ico（通知抬头用）。"""
    here = os.path.dirname(os.path.abspath(__file__))
    for rel in ("../新图标.ico", "新图标.ico", os.path.join("..", "docs", "images", "icon.png")):
        p = os.path.normpath(os.path.join(here, rel))
        if os.path.exists(p):
            return p
    return ""


def _ensure_aumid_async(icon: str) -> None:
    """后台注册 AUMID：写注册表 DisplayName/IconUri + 建开始菜单快捷方式。

    两处都要写 —— 通知抬头显示的应用名取自注册表的 DisplayName，
    而系统只肯给「开始菜单里有快捷方式」的 AUMID 发通知。
    首次约 1~2 秒，放在后台线程，不阻塞当前这条通知。
    """
    register_aumid(icon)
    ps = os.path.join(os.path.dirname(os.path.abspath(__file__)), "register_toast_aumid.ps1")
    if not os.path.exists(ps):
        return
    ico = _find_ico() or icon

    def _run():
        try:
            subprocess.run(
                [
                    "powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", ps,
                    "-Aumid", AUMID, "-AppName", LNK_NAME,
                    "-IconPath", ico, "-TargetPath", sys.executable,
                ],
                capture_output=True,
                timeout=60,
                creationflags=0x08000000,
            )
        except Exception:
            pass

    threading.Thread(target=_run, daemon=True).start()


def _xml_escape(s: str) -> str:
    return (
        str(s).replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


_proc_aumid_done = [False]


def _show_toast_python(title: str, message: str, icon: str) -> bool:
    """纯 Python 走 WinRT 弹通知，约 0.03 秒。

    两个坑是一起绕开的：

    * `create_toast_notifier(aumid)` 在 winrt 3.2.1 里有 HSTRING 绑定 bug，
      传字符串直接 `TypeError: Invalid parameter count` → 改用
      `create_toast_notifier_with_id(aumid)`。
    * `IXmlNode` 没有 `set_attribute`，没法用 `get_template_content()` 改模板
      → 直接手写 XML 用 `XmlDocument.load_xml()` 灌进去。

    另一个关键点：Windows 对同一个 AUMID 的通知默认是**排队**的 —— 前一条
    没消失，后一条就不出来（这就是「F9 的通知要等它消失，F10 的才弹」）。
    给每条都设同一个 `tag`，新通知就会**立刻替换**旧的那条。
    """
    wr = _winrt()
    if not wr:
        return False
    XmlDocument = wr["XmlDocument"]
    ToastNotification = wr["ToastNotification"]
    ToastNotificationManager = wr["ToastNotificationManager"]
    if not _proc_aumid_done[0]:
        _proc_aumid_done[0] = True
        try:
            # 进程级 AUMID：让 with_id 那条路径也能取到注册好的名字/图标
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(AUMID)
        except Exception:
            pass
    try:
        src = "file:///" + icon.replace("\\", "/")
        xml = (
            '<toast duration="short"><visual><binding template="ToastImageAndText02">'
            '<image id="1" src="%s"/>'
            '<text id="1">%s</text><text id="2">%s</text>'
            "</binding></visual></toast>"
            % (src, _xml_escape(title), _xml_escape(message))
        )
        doc = XmlDocument()
        doc.load_xml(xml)
        note = ToastNotification(doc)
        try:
            note.tag = "adventure"  # 同 tag → 替换上一条，不排队
            note.group = "adventure"
        except Exception:
            pass
        ToastNotificationManager.create_toast_notifier_with_id(AUMID).show(note)
        return True
    except Exception:
        return False


def _show_toast(title: str, message: str) -> bool:
    """弹通知：纯 Python 优先（0.03 秒），PowerShell 兜底（1~2 秒）。"""
    icon = _find_icon()
    if not icon:
        return False
    if not _aumid_done[0]:
        _aumid_done[0] = True
        _ensure_aumid_async(icon)

    if _show_toast_python(title, message, icon):
        return True

    ps = os.path.join(os.path.dirname(os.path.abspath(__file__)), "toast_show.ps1")
    if not os.path.exists(ps):
        return False
    try:
        subprocess.run(
            [
                "powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", ps,
                "-Title", title, "-Body", message,
                "-Icon", icon, "-Aumid", AUMID,
            ],
            capture_output=True,
            timeout=20,
            creationflags=0x08000000,  # CREATE_NO_WINDOW，不闪黑框
        )
        return True
    except Exception:
        return False


import subprocess
import sys
import threading
import time

user32 = ctypes.windll.user32
shell32 = ctypes.windll.shell32
kernel32 = ctypes.windll.kernel32

WM_USER = 0x0400
WM_TRAY_CALLBACK = WM_USER + 20
WM_DESTROY = 0x0002

NIM_ADD = 0x00000000
NIM_MODIFY = 0x00000001
NIM_DELETE = 0x00000002

NIF_MESSAGE = 0x00000001
NIF_ICON = 0x00000002
NIF_TIP = 0x00000004
NIF_INFO = 0x00000010

NIIF_INFO = 0x00000001
NIIF_NOSOUND = 0x00000010

IDI_INFORMATION = 32516


# 托盘图标 ID：每次弹窗用不同 ID，避免后一个覆盖前一个的提示
_uid_counter = [0]


class NOTIFYICONDATAW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("hWnd", wintypes.HWND),
        ("uID", wintypes.UINT),
        ("uFlags", wintypes.UINT),
        ("uCallbackMessage", wintypes.UINT),
        ("hIcon", wintypes.HICON),
        ("szTip", wintypes.WCHAR * 128),
        ("dwState", wintypes.DWORD),
        ("dwStateMask", wintypes.DWORD),
        ("szInfo", wintypes.WCHAR * 256),
        ("uTimeout", wintypes.UINT),
        ("szInfoTitle", wintypes.WCHAR * 64),
        ("dwInfoFlags", wintypes.DWORD),
        ("guidItem", wintypes.BYTE * 16),
        ("hBalloonIcon", wintypes.HICON),
    ]


WNDPROC = ctypes.WINFUNCTYPE(
    ctypes.c_ssize_t, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM
)


class WNDCLASSW(ctypes.Structure):
    _fields_ = [
        ("style", wintypes.UINT),
        ("lpfnWndProc", WNDPROC),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", wintypes.HINSTANCE),
        ("hIcon", wintypes.HICON),
        ("hCursor", wintypes.HANDLE),
        ("hbrBackground", wintypes.HBRUSH),
        ("lpszMenuName", wintypes.LPCWSTR),
        ("lpszClassName", wintypes.LPCWSTR),
    ]


user32.DefWindowProcW.restype = ctypes.c_ssize_t
user32.DefWindowProcW.argtypes = [
    wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM
]
user32.CreateWindowExW.restype = wintypes.HWND
kernel32.GetModuleHandleW.restype = wintypes.HMODULE
shell32.Shell_NotifyIconW.restype = wintypes.BOOL


# --- 显式声明签名：不声明的话 64 位句柄会被当成 c_int，CreateWindowExW 直接 OverflowError
user32.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASSW)]
user32.RegisterClassW.restype = wintypes.ATOM
user32.UnregisterClassW.argtypes = [wintypes.LPCWSTR, wintypes.HINSTANCE]
user32.UnregisterClassW.restype = wintypes.BOOL
user32.CreateWindowExW.argtypes = [
    wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
    ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
    wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID,
]
user32.CreateWindowExW.restype = wintypes.HWND
user32.DestroyWindow.argtypes = [wintypes.HWND]
user32.DestroyWindow.restype = wintypes.BOOL
user32.LoadIconW.argtypes = [wintypes.HINSTANCE, wintypes.LPCWSTR]
user32.LoadIconW.restype = wintypes.HICON
user32.PeekMessageW.argtypes = [
    ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT, wintypes.UINT
]
user32.PeekMessageW.restype = wintypes.BOOL
user32.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
user32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
user32.DispatchMessageW.restype = ctypes.c_ssize_t
shell32.Shell_NotifyIconW.argtypes = [wintypes.DWORD, ctypes.POINTER(NOTIFYICONDATAW)]
shell32.Shell_NotifyIconW.restype = wintypes.BOOL

def _show(title: str, message: str, ms: int) -> None:
    """在当前线程建隐藏窗口 → 加托盘图标 → 弹气泡 → 延时 → 清理。"""
    hinst = kernel32.GetModuleHandleW(None)
    _uid_counter[0] += 1
    uid = _uid_counter[0]
    cls_name = "DshToastHost_%d" % uid

    holder = {}

    def wndproc(hwnd, msg, wparam, lparam):
        if msg == WM_DESTROY:
            user32.PostQuitMessage(0)
            return 0
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    proc = WNDPROC(wndproc)
    holder["proc"] = proc  # 必须保住引用，否则回调被回收

    wc = WNDCLASSW()
    wc.style = 0
    wc.lpfnWndProc = proc
    wc.cbClsExtra = 0
    wc.cbWndExtra = 0
    wc.hInstance = hinst
    wc.hIcon = user32.LoadIconW(None, wintypes.LPCWSTR(IDI_INFORMATION))
    wc.hCursor = None
    wc.hbrBackground = None
    wc.lpszMenuName = None
    wc.lpszClassName = cls_name

    if not user32.RegisterClassW(ctypes.byref(wc)):
        return  # 类名冲突或注册失败，直接放弃（不能因为通知失败影响主流程）

    hwnd = user32.CreateWindowExW(
        0, cls_name, cls_name, 0, 0, 0, 0, 0, None, None, hinst, None
    )
    if not hwnd:
        user32.UnregisterClassW(cls_name, hinst)
        return

    nid = NOTIFYICONDATAW()
    nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
    nid.hWnd = hwnd
    nid.uID = uid
    nid.uFlags = NIF_ICON | NIF_TIP | NIF_MESSAGE
    nid.uCallbackMessage = WM_TRAY_CALLBACK
    nid.hIcon = wc.hIcon
    nid.szTip = "造梦西游4"

    if not shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(nid)):
        user32.DestroyWindow(hwnd)
        user32.UnregisterClassW(cls_name, hinst)
        return

    nid.uFlags = NIF_INFO
    nid.szInfoTitle = title[:63]
    nid.szInfo = message[:255]
    nid.dwInfoFlags = NIIF_INFO
    nid.uTimeout = 6000
    shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(nid))

    # 跑一个小消息循环，等气泡该消失了再清理
    end = time.monotonic() + ms / 1000.0
    msg = wintypes.MSG()
    while time.monotonic() < end:
        while user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 1):
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
        time.sleep(0.05)

    shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(nid))
    user32.DestroyWindow(hwnd)
    user32.UnregisterClassW(cls_name, hinst)


def toast(title: str, message: str, ms: int = 8000, background: bool = True) -> None:
    """弹一个右下角通知。

    优先 WinRT 系统通知（样式与 MAA 客户端一致），不可用时退回托盘气泡。
    background=True 时在后台线程里弹，立刻返回 —— 录制这种不能阻塞的场景用这个。
    """
    def _run():
        if _show_toast(title, message):
            return
        try:
            _show(title, message, ms)
        except Exception:
            pass

    if background:
        threading.Thread(target=_run, daemon=True).start()
    else:
        _run()


if __name__ == "__main__":
    toast("战斗录制", "这是一条测试通知，能看到就说明通了")
    time.sleep(2)
