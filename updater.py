# -*- coding: utf-8 -*-
"""
MAA造梦西游4 启动时自动更新器。

由同目录的 MAA造梦西游4.exe（启动器）在启动时调用，流程：

  1. 读本地 interface.json 的 version
  2. 拉远端 version.json
  3. 没有新版本 -> 直接启动 MFA.exe
  4. 有新版本 -> 弹窗询问；点「立即更新」才下载
  5. 下载 update-vX.zip -> 校验 sha256 -> 解压到临时目录
  6. 备份旧 resource/agent/interface.json -> 覆盖
  7. 启动 MFA.exe

任何一步失败都不会卡住玩家：报个错，然后照常启动旧版本。
"""

import hashlib
import json
import os
import shutil
import ssl
import subprocess
import sys
import time
import traceback
import urllib.request
import zipfile

import tkinter as tk
from tkinter import ttk

# ---------------- 配置 ----------------

# 版本清单（version.json）的多源列表：按顺序尝试，谁先成功用谁。
# 填好下面这一行，就自动把它排到第一位（国内玩家优先走它）。
#   例：R2_BASE = "https://maa.example.com/maa"
R2_BASE = "https://pub-2b261f42e9e445f7ba82f6ad70309148.r2.dev/maa"
VERSION_URLS = [
    "https://cdn.jsdelivr.net/gh/Inkyu-Zero/MAAZMXY4@main/version.json",
    "https://raw.githubusercontent.com/Inkyu-Zero/MAAZMXY4/main/version.json",
]
if R2_BASE.strip():
    VERSION_URLS.insert(0, R2_BASE.strip().rstrip("/") + "/version.json")

TIMEOUT = 8              # 请求超时（秒）
CHUNK = 64 * 1024
MFA_EXE = "MFA.exe"
UPDATE_DIR = os.path.join("temp", "update")
SKIP_FILE = os.path.join("config", "update_skip.json")

BASE = os.path.dirname(os.path.abspath(__file__))
LOG_PATH = os.path.join(BASE, "temp", "updater.log")

# 只更新这三样，玩家的 config / recordings / debug 一律不碰
UPDATE_ITEMS = ("resource", "agent", "interface.json")


# ---------------- 小工具 ----------------

def log(msg):
    try:
        os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write("[%s] %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg))
    except Exception:
        pass


def parse_version(text):
    """'2.10' -> (2, 10, 0)，按数字比较，不是字符串比较。"""
    parts = []
    for piece in str(text or "").strip().lstrip("vV").split("."):
        try:
            parts.append(int(piece))
        except ValueError:
            parts.append(0)
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts[:3])


def local_version():
    try:
        with open(os.path.join(BASE, "interface.json"), encoding="utf-8") as f:
            return str(json.load(f).get("version") or "0")
    except Exception:
        return "0"


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": "MAAZMXY4-Updater"})
    with urllib.request.urlopen(req, timeout=TIMEOUT, context=ssl.create_default_context()) as resp:
        return resp.read()


def load_remote_version():
    """返回 dict（含 version / file / sha256 / urls / note），失败返回 None。"""
    for url in VERSION_URLS:
        if not url:
            continue
        try:
            data = json.loads(fetch(url).decode("utf-8-sig"))
            if data.get("version"):
                log("远端版本 %s（%s）" % (data["version"], url))
                return data
        except Exception as exc:
            log("读取 %s 失败：%r" % (url, exc))
    return None


def package_urls(remote):
    """更新包的候选下载地址：优先 urls 数组，其次兼容旧的单个 url。"""
    urls = remote.get("urls")
    if isinstance(urls, list):
        urls = [u for u in urls if isinstance(u, str) and u.strip()]
        if urls:
            return urls
    single = remote.get("url") or remote.get("download_url") or ""
    return [single] if single else []


def skipped_versions():
    try:
        with open(os.path.join(BASE, SKIP_FILE), encoding="utf-8") as f:
            return set(json.load(f).get("skip") or [])
    except Exception:
        return set()


def remember_skip(version):
    path = os.path.join(BASE, SKIP_FILE)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        data = {"skip": sorted(skipped_versions() | {version})}
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
    except Exception:
        pass


def find_mfa():
    for name in (MFA_EXE, "MFAAvalonia.exe"):
        p = os.path.join(BASE, name)
        if os.path.isfile(p):
            return p
    return None


def launch_mfa():
    exe = find_mfa()
    if not exe:
        log("找不到 MFA.exe，无法启动")
        return False
    log("启动 %s" % exe)
    try:
        subprocess.Popen([exe], cwd=BASE, close_fds=True)
        return True
    except Exception:
        log("启动失败：%s" % traceback.format_exc())
        return False


def download(url, dest, on_progress):
    req = urllib.request.Request(url, headers={"User-Agent": "MAAZMXY4-Updater"})
    with urllib.request.urlopen(req, timeout=TIMEOUT, context=ssl.create_default_context()) as resp:
        total = int(resp.headers.get("Content-Length") or 0)
        done = 0
        with open(dest, "wb") as f:
            while True:
                block = resp.read(CHUNK)
                if not block:
                    break
                f.write(block)
                done += len(block)
                on_progress(done, total)


def download_any(urls, dest, on_progress):
    """依次尝试各下载地址，成功返回实际使用的 url；全部失败抛最后一个异常。"""
    last = None
    for url in urls:
        try:
            log("下载 %s" % url)
            download(url, dest, on_progress)
            return url
        except Exception as exc:
            last = exc
            log("下载失败（%s）：%r" % (url, exc))
            try:
                if os.path.exists(dest):
                    os.remove(dest)
            except Exception:
                pass
    raise last or RuntimeError("没有可用的下载地址")


def sha256_of(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(CHUNK), b""):
            h.update(block)
    return h.hexdigest()


def apply_update(zip_path, info, on_progress):
    """解压并替换。返回 (ok, message)。"""
    temp_dir = os.path.join(BASE, UPDATE_DIR)
    extract_dir = os.path.join(temp_dir, "extract")
    if os.path.isdir(extract_dir):
        shutil.rmtree(extract_dir, ignore_errors=True)
    os.makedirs(extract_dir, exist_ok=True)

    on_progress("正在解压…", 0)
    with zipfile.ZipFile(zip_path) as zf:
        zf.extractall(extract_dir)

    if not os.path.isfile(os.path.join(extract_dir, "interface.json")):
        return False, "更新包不完整（缺少 interface.json）"

    backup_dir = os.path.join(BASE, "backup", "update-%s-%s" % (
        local_version(), time.strftime("%Y%m%d-%H%M%S")))
    os.makedirs(backup_dir, exist_ok=True)

    moved = []
    try:
        for name in UPDATE_ITEMS:
            src = os.path.join(extract_dir, name)
            dst = os.path.join(BASE, name)
            if not os.path.exists(src):
                continue
            on_progress("正在替换 %s…" % name, 0)
            if os.path.exists(dst):
                shutil.move(dst, os.path.join(backup_dir, name))
                moved.append((name, True))
            else:
                moved.append((name, False))
            shutil.move(src, dst)
        log("更新完成，旧文件备份在 %s" % backup_dir)
        return True, ""
    except Exception:
        log("替换失败，回滚：%s" % traceback.format_exc())
        for name, existed in reversed(moved):
            dst = os.path.join(BASE, name)
            try:
                if os.path.exists(dst):
                    shutil.rmtree(dst) if os.path.isdir(dst) else os.remove(dst)
                if existed:
                    shutil.move(os.path.join(backup_dir, name), dst)
            except Exception:
                pass
        return False, "替换文件失败，已回滚"


# ---------------- 界面 ----------------

class Updater(tk.Tk):
    def __init__(self, info, remote):
        super().__init__()
        self.info = info
        self.remote = remote
        self.title("MAA造梦西游4 更新")
        self.resizable(False, False)
        self.configure(padx=18, pady=14)

        tk.Label(self, text="发现新版本 v%s" % remote.get("version"),
                 font=("Microsoft YaHei UI", 13, "bold")).pack(anchor="w")
        tk.Label(self, text="当前版本 v%s" % info.get("local"),
                 font=("Microsoft YaHei UI", 9), fg="#666").pack(anchor="w", pady=(2, 8))

        note = (remote.get("note") or "").strip()
        if note:
            text = tk.Text(self, height=4, width=46, wrap="word", relief="flat",
                           bg=self.cget("bg"), font=("Microsoft YaHei UI", 9))
            text.insert("1.0", note)
            text.configure(state="disabled")
            text.pack(anchor="w")

        self.status = tk.Label(self, text="", font=("Microsoft YaHei UI", 9), fg="#666")
        self.status.pack(anchor="w", pady=(6, 0))
        self.bar = ttk.Progressbar(self, length=384, mode="determinate")
        self.bar.pack(anchor="w", pady=(4, 8))

        btns = tk.Frame(self)
        btns.pack(anchor="e")
        self.btn_skip = tk.Button(btns, text="跳过这次", width=10, command=self.on_skip)
        self.btn_skip.pack(side="left", padx=(0, 8))
        self.btn_ok = tk.Button(btns, text="立即更新", width=10, command=self.on_update)
        self.btn_ok.pack(side="left")

        # 窗口高度按实际内容算：说明文字长短不一，写死高度会把底部按钮裁掉。
        self.update_idletasks()
        w = max(460, self.winfo_reqwidth())
        h = self.winfo_reqheight()
        x = (self.winfo_screenwidth() - w) // 2
        y = (self.winfo_screenheight() - h) // 3
        self.geometry("%dx%d+%d+%d" % (w, h, x, y))

    def on_skip(self):
        remember_skip(self.remote.get("version"))
        self.destroy()
        launch_mfa()

    def on_update(self):
        self.btn_ok.configure(state="disabled")
        self.btn_skip.configure(state="disabled")
        self.bar.configure(mode="indeterminate")
        self.bar.start(12)
        try:
            self._do_update()
        except Exception:
            log(traceback.format_exc())
            self._fail("更新时出错，已跳过")

    def _set_status(self, text, percent=None):
        self.status.configure(text=text)
        if percent is not None:
            self.bar.stop()
            self.bar.configure(mode="determinate")
            self.bar["value"] = max(0, min(100, percent))
        self.update_idletasks()

    def _do_update(self):
        info = self.info
        urls = package_urls(self.remote)
        if not urls:
            return self._fail("更新地址缺失")

        os.makedirs(os.path.join(BASE, UPDATE_DIR), exist_ok=True)
        zip_path = os.path.join(BASE, UPDATE_DIR, "update.zip")

        last = [0.0]

        def on_progress(done, total):
            now = time.time()
            if now - last[0] < 0.08 and done != total:
                return
            last[0] = now
            if total:
                mb = done / 1048576.0
                self._set_status("正在下载… %.1f / %.1f MB" % (mb, total / 1048576.0),
                                 done * 100.0 / total)
            else:
                self._set_status("正在下载… %.1f MB" % (done / 1048576.0))

        self._set_status("正在下载…", 0)
        download_any(urls, zip_path, on_progress)

        digest = sha256_of(zip_path)
        expect = (self.remote.get("sha256") or "").strip().lower()
        if expect and digest != expect:
            os.remove(zip_path)
            return self._fail("校验失败，更新包已损坏")

        ok, msg = apply_update(zip_path, info, self._set_status)
        if not ok:
            return self._fail(msg)

        self._set_status("更新完成", 100)
        try:
            os.remove(zip_path)
        except Exception:
            pass
        self.after(500, self._finish)

    def _finish(self):
        self.destroy()
        launch_mfa()

    def _fail(self, message):
        log("更新失败：%s" % message)
        self.bar.stop()
        self.bar.configure(mode="determinate", value=0)
        self.status.configure(text=message, fg="#b00")
        self.btn_ok.configure(text="关闭", state="normal", command=self._close_fail)
        self.btn_skip.configure(text="启动游戏", state="normal", command=self._launch_now)

    def _close_fail(self):
        self.destroy()

    def _launch_now(self):
        self.destroy()
        launch_mfa()


# ---------------- 入口 ----------------

def main():
    log("---- 启动更新检查 ----")
    info = {"local": local_version()}

    remote = load_remote_version()
    if not remote or parse_version(remote.get("version")) <= parse_version(info["local"]):
        log("已是最新（本地 %s）" % info["local"])
        launch_mfa()
        return

    remote["url"] = remote.get("url") or remote.get("download_url") or ""
    if not package_urls(remote):
        log("版本清单里没有下载地址，跳过更新")
        launch_mfa()
        return
    if remote.get("version") in skipped_versions():
        log("玩家选择跳过 %s" % remote["version"])
        launch_mfa()
        return

    app = Updater(info, remote)
    app.mainloop()


if __name__ == "__main__":
    try:
        main()
    except Exception:
        log(traceback.format_exc())
        launch_mfa()
