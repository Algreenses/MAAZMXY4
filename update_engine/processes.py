"""Read-only Windows process ownership checks. Never terminate a game/client."""
import os
from pathlib import Path
import time

from .packages import PackageError


def running_images():
    if os.name != 'nt':
        raise PackageError('自动安装仅支持 Windows。')
    import ctypes as c
    from ctypes import wintypes as w

    class ProcessEntry(c.Structure):
        _fields_ = [('dwSize', w.DWORD), ('cntUsage', w.DWORD), ('th32ProcessID', w.DWORD),
                    ('th32DefaultHeapID', c.c_size_t), ('th32ModuleID', w.DWORD), ('cntThreads', w.DWORD),
                    ('th32ParentProcessID', w.DWORD), ('pcPriClassBase', w.LONG), ('dwFlags', w.DWORD),
                    ('szExeFile', w.WCHAR * 260)]
    api = c.WinDLL('kernel32', use_last_error=True)
    api.CreateToolhelp32Snapshot.argtypes = [w.DWORD, w.DWORD]
    api.CreateToolhelp32Snapshot.restype = w.HANDLE
    api.Process32FirstW.argtypes = [w.HANDLE, c.POINTER(ProcessEntry)]
    api.Process32NextW.argtypes = [w.HANDLE, c.POINTER(ProcessEntry)]
    api.OpenProcess.argtypes = [w.DWORD, w.BOOL, w.DWORD]
    api.OpenProcess.restype = w.HANDLE
    api.QueryFullProcessImageNameW.argtypes = [w.HANDLE, w.DWORD, w.LPWSTR, c.POINTER(w.DWORD)]
    api.CloseHandle.argtypes = [w.HANDLE]
    snapshot = api.CreateToolhelp32Snapshot(2, 0)
    if snapshot == c.c_void_p(-1).value:
        raise PackageError('无法读取进程列表，请关闭助手后重试。')
    result = []
    try:
        entry = ProcessEntry()
        entry.dwSize = c.sizeof(entry)
        present = api.Process32FirstW(snapshot, c.byref(entry))
        while present:
            handle = api.OpenProcess(0x1000, False, entry.th32ProcessID)
            if handle:
                try:
                    size = w.DWORD(32768)
                    path = c.create_unicode_buffer(size.value)
                    if api.QueryFullProcessImageNameW(handle, 0, path, c.byref(size)):
                        result.append((entry.th32ProcessID, path.value))
                finally:
                    api.CloseHandle(handle)
            elif (c.get_last_error() not in (87, 1168) and
                  entry.szExeFile.casefold() in ('mfaavalonia.exe', 'maa造梦西游4.exe', 'python.exe', 'pythonw.exe')):
                # An elevated matching process cannot be assigned to this root
                # safely. Fail closed instead of killing it or guessing.
                raise PackageError('无法核实正在运行的 ' + entry.szExeFile + '，请手动关闭相关助手后安装。')
            present = api.Process32NextW(snapshot, c.byref(entry))
    finally:
        api.CloseHandle(snapshot)
    return result


def owned_processes(root, *, worker=None, images=None):
    root = Path(root).resolve()
    worker = Path(worker).resolve() if worker else None
    result = []
    for pid, image in running_images() if images is None else images:
        path = Path(image).resolve()
        if path.is_relative_to(root) and not (worker and path.is_relative_to(worker)):
            result.append((pid, str(path)))
    return result


def wait_for_exit(root, worker, *, timeout=90, report=lambda *_: None):
    deadline = time.monotonic() + timeout
    while processes := owned_processes(root, worker=worker):
        report('等待此安装目录的客户端及后台进程退出。')
        if time.monotonic() >= deadline:
            raise PackageError('仍有程序占用此安装目录，未修改程序文件。请关闭助手后重试。')
        time.sleep(0.5)
