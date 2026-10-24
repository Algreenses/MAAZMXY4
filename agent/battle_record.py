"""局内战斗录制 —— 核心 + MAA CustomAction 入口。

两个入口共用同一套逻辑：

* ``tools/battle_recorder.py``（命令行版，适合单独跑、能输入文件名）
* ``@AgentServer.custom_action("battle_record")``（pipeline 版，由 MAA 任务触发）

热键一致::

    F9   开始 / 停止录制
    F10  停止并保存（用默认时间戳名）
    F12  退出

录制只在游戏窗口是前台窗口时生效。坐标换算成 720p 基准，
与 MaaFramework 截图、pipeline ROI 同一套，所以换分辨率 / 换 DPI 都不用改。
"""

import ctypes
import ctypes.wintypes as wt
import json
import os
import sys
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

user32 = ctypes.windll.user32

try:
    from toast import toast as _toast
except Exception:  # 通知只是锦上添花，缺了也照常录
    def _toast(*_a, **_k):
        return None

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RECORDING_DIR = os.path.join(PROJECT_ROOT, "recordings")

TITLE_KEYWORD = "造梦盒子"
CLASS_KEYWORD = "ApolloRuntimeContentWindow"
SHORT_SIDE = 720

MOVE_MIN_PX = 4
MOVE_MS = 40


# ---------------------------------------------------------------- 窗口与坐标

def find_game_window(title_kw: str = TITLE_KEYWORD,
                     cls_kw: str = CLASS_KEYWORD) -> Tuple[Optional[int], str]:
    """按类名 / 标题找游戏窗口，返回 (hwnd, title)；找不到返回 (None, '')。"""
    found: List[Tuple[Tuple[int, int], int, str]] = []

    @ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    def _enum(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        title = ""
        if length:
            buf = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buf, length + 1)
            title = buf.value
        cls_buf = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, cls_buf, 256)
        cls = cls_buf.value
        rc = wt.RECT()
        user32.GetClientRect(hwnd, ctypes.byref(rc))
        w, h = rc.right - rc.left, rc.bottom - rc.top
        if w < 320 or h < 200:
            return True
        title_ok = title_kw in title
        cls_ok = cls_kw in cls
        if title_ok or cls_ok:
            found.append(((0 if cls_ok else 1, -w * h), hwnd, title))
        return True

    user32.EnumWindows(_enum, 0)
    if not found:
        return None, ""
    found.sort()
    return found[0][1], found[0][2]


def client_size(hwnd) -> Tuple[int, int]:
    rc = wt.RECT()
    user32.GetClientRect(hwnd, ctypes.byref(rc))
    return rc.right - rc.left, rc.bottom - rc.top


def client_origin(hwnd) -> Tuple[int, int]:
    pt = wt.POINT(0, 0)
    user32.ClientToScreen(hwnd, ctypes.byref(pt))
    return pt.x, pt.y


def target_size(w: int, h: int, short_side: int = SHORT_SIDE) -> Tuple[int, int]:
    """复刻 MaaFramework 的 calc_target_image_size：短边固定 short_side。"""
    if w <= 0 or h <= 0:
        return short_side, short_side
    if w > h:
        return int(round(short_side * w / h)), short_side
    return short_side, int(round(short_side * h / w))


class Mapper:
    """屏幕坐标 ↔ 720p 基准坐标。"""

    def __init__(self, hwnd):
        self.hwnd = hwnd
        self.refresh()

    def refresh(self) -> None:
        self.cw, self.ch = client_size(self.hwnd)
        self.ox, self.oy = client_origin(self.hwnd)
        self.tw, self.th = target_size(self.cw, self.ch)
        self.sx = (self.tw / self.cw) if self.cw else 1.0
        self.sy = (self.th / self.ch) if self.ch else 1.0

    def to_base(self, x: int, y: int) -> Tuple[int, int]:
        cx, cy = x - self.ox, y - self.oy
        return int(round(cx * self.sx)), int(round(cy * self.sy))

    def inside(self, x: int, y: int) -> bool:
        cx, cy = x - self.ox, y - self.oy
        return 0 <= cx < self.cw and 0 <= cy < self.ch

    def describe(self) -> str:
        return "客户区 %dx%d → 基准 %dx%d（比例 %.3f / %.3f）" % (
            self.cw, self.ch, self.tw, self.th, self.sx, self.sy)


# ---------------------------------------------------------------- 录制核心

class Recorder:
    """收集动作。线程安全，因为 pynput 的回调在别的线程。"""

    def __init__(self, hwnd: Optional[int] = None):
        self.hwnd = hwnd
        self.mapper: Optional[Mapper] = None
        self.recording = False
        self.actions: List[Dict[str, Any]] = []
        self._last_t = 0.0
        self._start_t = 0.0
        self._last_move = (-999, -999)
        self._last_move_t = 0.0
        self._held: set = set()  # 当前按下的键，用来过滤键盘自动重复
        self._lock = threading.Lock()
        self.on_log = lambda msg: None  # 由入口注入
        self.on_notify = lambda title, msg: _toast(title, msg)

    # -- 窗口 --------------------------------------------------------------
    def ensure_window(self) -> bool:
        if self.hwnd and user32.IsWindow(self.hwnd):
            if self.mapper is None:
                self.mapper = Mapper(self.hwnd)
                self.on_log("  " + self.mapper.describe())
            return True
        hwnd, title = find_game_window()
        if not hwnd:
            self.on_log("  找不到游戏窗口（标题含「%s」）" % TITLE_KEYWORD)
            self.hwnd = None
            self.mapper = None
            return False
        self.hwnd = hwnd
        self.mapper = Mapper(hwnd)
        self.on_log("  窗口：%s" % title)
        self.on_log("  " + self.mapper.describe())
        return True

    def is_foreground(self) -> bool:
        return bool(self.hwnd) and user32.GetForegroundWindow() == self.hwnd

    # -- 记录 --------------------------------------------------------------
    def _push(self, op: str, **kwargs: Any) -> None:
        if not self.recording:
            return
        with self._lock:
            now = time.monotonic()
            # 第一个动作：相对「开始录制」那一刻算，而不是强制 0。
            # 这样录制时若先等战斗开始再动手，回放会先把同样的等待重现。
            prev = self._last_t or self._start_t
            gap = int(round((now - prev) * 1000)) if prev else 0
            self._last_t = now
            act: Dict[str, Any] = {"t": max(0, gap), "op": op}
            act.update(kwargs)
            self.actions.append(act)

    def on_click(self, x, y, button, pressed) -> None:  # noqa: ANN001
        from pynput import mouse
        if not self.recording or button != mouse.Button.left:
            return
        if not self.mapper or not self.mapper.inside(x, y) or not self.is_foreground():
            return
        gx, gy = self.mapper.to_base(x, y)
        if pressed:
            self._push("down", x=gx, y=gy)
        else:
            self._push("up")

    def on_move(self, x, y) -> None:  # noqa: ANN001
        if not self.recording or not self.mapper or not self.is_foreground():
            return
        if not self.mapper.inside(x, y):
            return
        now = time.monotonic()
        cx, cy = x - self.mapper.ox, y - self.mapper.oy
        if (abs(cx - self._last_move[0]) < MOVE_MIN_PX
                and abs(cy - self._last_move[1]) < MOVE_MIN_PX):
            return
        if (now - self._last_move_t) * 1000 < MOVE_MS:
            return
        self._last_move = (cx, cy)
        self._last_move_t = now
        gx, gy = self.mapper.to_base(x, y)
        self._push("move", x=gx, y=gy)

    def on_scroll(self, x, y, dx, dy) -> None:  # noqa: ANN001
        if not self.recording or not self.is_foreground():
            return
        self._push("scroll", dx=int(dx), dy=int(dy))

    def on_key(self, key, pressed) -> None:  # noqa: ANN001
        if not self.recording or not self.is_foreground():
            return
        vk = getattr(key, "vk", None)
        if vk is None:
            return
        vk = int(vk)
        # 键盘按住不放时系统会持续发 keydown（自动重复），这里必须去重：
        # 否则录出来是 4 个连续的 keydown 65 只有 1 个 keyup，回放时
        # 会把同一个键重复按 4 次，时长也被拉长。
        with self._lock:
            if pressed:
                if vk in self._held:
                    return
                self._held.add(vk)
            else:
                self._held.discard(vk)
        self._push("keydown" if pressed else "keyup", key=vk)

    # -- 控制 --------------------------------------------------------------
    def start(self) -> None:
        # 计时起点 = 按下 F9 的这一刻（含找窗口的耗时）。
        # 第一个动作的 t 就相对它算，所以「录制时等多久，回放就等多久」，
        # 不设默认值、也不设上限。
        self._start_t = time.monotonic()
        if not self.ensure_window():
            return
        self.mapper.refresh()
        self.actions = []
        self._last_t = 0.0
        self._last_move = (-999, -999)
        self._last_move_t = 0.0
        self._held.clear()
        self.recording = True
        self.on_log("● 开始录制 —— 切到游戏窗口随便打，打完按 F9 停。")
        self.on_notify("战斗录制 · 开始", "正在录制，F9 停止 / F10 保存 / F12 退出")

    def stop(self) -> int:
        if not self.recording:
            return len(self.actions)
        self.recording = False
        self.on_log("■ 录制停止，共 %d 个动作。" % len(self.actions))
        self.on_notify("战斗录制 · 停止", "本段共 %d 个动作" % len(self.actions))
        return len(self.actions)

    def clear(self) -> None:
        self.actions = []
        self.on_log("○ 已清空。")

    def save(self, name: str) -> Optional[str]:
        if not self.actions:
            self.on_log("  没有录到任何动作，不保存。")
            return None
        safe = (name or time.strftime("录制-%m%d-%H%M%S")).replace("/", "_").replace("\\", "_")
        safe = safe.strip() or time.strftime("录制-%m%d-%H%M%S")
        os.makedirs(RECORDING_DIR, exist_ok=True)
        path = os.path.join(RECORDING_DIR, safe + ".json")
        # 重名时自动加 -2 / -3 … 而不是覆盖别人已经录好的文件
        if os.path.exists(path):
            for n in range(2, 100):
                alt = "%s-%d" % (safe, n)
                alt_path = os.path.join(RECORDING_DIR, alt + ".json")
                if not os.path.exists(alt_path):
                    safe, path = alt, alt_path
                    self.on_log("  同名文件已存在，改存为 %s.json" % safe)
                    break
        payload = {
            "version": 1,
            "name": safe,
            "base": [self.mapper.tw, self.mapper.th] if self.mapper else [1147, 720],
            "recorded_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "actions": self.actions,
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=1)
        self.on_log("  [OK] 已保存：%s（%d 个动作）" % (path, len(self.actions)))
        self.on_notify("战斗录制 · 已保存", "%s.json（%d 个动作）" % (safe, len(self.actions)))
        # 顺手刷新 interface.json 的「回放文件」下拉列表，
        # 这样重启 MFA 后新录的这条就直接出现在下拉框里、且默认被选中
        _refresh_option_list(self.on_log)
        return path


def _refresh_option_list(log=None) -> bool:
    """把 recordings/ 里的录制同步进 interface.json 的「回放文件」下拉。

    ⚠️ 关键（踩过两次坑）：这个下拉是 `select` 类型，而 **MFA 的 `{名称}`
    占位符替换只对 `input` / `hotkey` 类型生效**。`select` 的 case 里写
    `{"file": "{回放文件}"}` 永远不会被替换，运行时报
    「找不到录制文件：recordings/{回放文件}.json」。
    所以必须**每个 case 自带完整的 pipeline_override**，把文件名硬写进去。
    """
    import json as _json
    try:
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        iface_path = os.path.join(root, "interface.json")
        if not os.path.exists(iface_path):
            if log:
                log("  找不到 interface.json，跳过下拉列表刷新")
            return False
        with open(iface_path, encoding="utf-8") as f:
            iface = _json.load(f)
        opts = iface.get("option")
        if not isinstance(opts, dict):
            return False
        opt = opts.get("回放文件")
        if not isinstance(opt, dict):
            if log:
                log("  interface.json 里没有「回放文件」option，跳过")
            return False

        # 扫出所有「-回放战斗」节点，每个 case 都要覆盖它们
        import glob as _glob
        play_nodes = []
        pdir = os.path.join(root, "resource", "base", "pipeline")
        for fp in sorted(_glob.glob(os.path.join(pdir, "*.json"))):
            try:
                with open(fp, encoding="utf-8") as f:
                    pj = _json.load(f)
            except Exception:
                continue
            for k in pj:
                if k.endswith("-回放战斗"):
                    play_nodes.append(k)
        if not play_nodes:
            if log:
                log("  没找到「-回放战斗」节点，跳过下拉列表刷新")
            return False

        rec_dir = os.path.join(root, "recordings")
        items = []
        if os.path.isdir(rec_dir):
            for fn in os.listdir(rec_dir):
                if fn.startswith(".") or not fn.lower().endswith(".json"):
                    continue
                items.append((fn[:-5], os.path.getmtime(os.path.join(rec_dir, fn))))
        items.sort(key=lambda t: -t[1])

        manual = "（手动选择文件…）"
        latest_case = "（最新录制）"
        latest_token = "@latest"

        def _ov(file_value):
            return {
                n: {"action": {"param": {"custom_action_param": {
                    "file": file_value, "speed": 1.0}}}}
                for n in play_nodes
            }

        # 「（最新录制）」放第一个并默认选中：它由 battle_player 在运行时
        # 现查磁盘，所以录完新战斗**不用重启 MFA** 就能用上。
        cases = [{"name": latest_case, "pipeline_override": _ov(latest_token)},
                 {"name": manual, "pipeline_override": _ov("?")}]
        cases += [{"name": n, "pipeline_override": _ov(n)} for n, _ in items]
        default = latest_case

        new = {
            "type": "select",
            "label": "回放文件",
            "default_case": default,
            "cases": cases,
        }
        if new == opt:
            if log:
                log("  「回放文件」下拉列表没有变化（%d 条）" % len(items))
            return True

        opts["回放文件"] = new
        with open(iface_path, "w", encoding="utf-8") as f:
            _json.dump(iface, f, ensure_ascii=False, indent=2)
        if log:
            log("  已刷新「回放文件」下拉列表：%d 条，默认 %s（重启 MFA 后生效）"
                % (len(items), default))
        return True
    except Exception as e:
        if log:
            log("  下拉列表同步失败：%s" % e)
        return False


def _foreground(win) -> None:
    """把 tk 窗口对应的 Win32 窗口拉到最前（tk 的 topmost 抢不到焦点时兜底）。"""
    try:
        hwnd = int(win.winfo_id())
        parent = ctypes.windll.user32.GetParent(hwnd)
        target = parent or hwnd
        ctypes.windll.user32.ShowWindow(target, 9)
        ctypes.windll.user32.SetForegroundWindow(target)
    except Exception:
        pass


def _ensure_tcl_env() -> None:
    """给内嵌运行时补上 TCL_LIBRARY / TK_LIBRARY。

    MAA 启动 agent 时既不带这两个环境变量、工作目录也不是 runtimes/python，
    于是 tkinter 会报 `Can't find a usable init.tcl in .../tcl8.6`。
    这里从 sys.executable 往上推出 runtimes/python/tcl 的位置再写回去。
    已经设过就不覆盖（尊重外部配置）。
    """
    try:
        exe_dir = os.path.dirname(os.path.abspath(sys.executable))
        tcl_root = os.path.join(exe_dir, "tcl")
        cand = {
            "TCL_LIBRARY": os.path.join(tcl_root, "tcl8.6"),
            "TK_LIBRARY": os.path.join(tcl_root, "tk8.6"),
        }
        for key, path in cand.items():
            if not os.environ.get(key) and os.path.isdir(path):
                os.environ[key] = path
    except Exception:
        pass


def _ask_save_name(default: str) -> Optional[str]:
    """弹一个输入框让用户写保存名，返回 None 表示用户取消了。

    tkinter 的 Tk 实例必须在自己创建它的那个线程里使用，而 MAA 的
    CustomAction 回调不在 Python 主线程，所以这里**单开一个线程**建 Tk、
    弹完就销毁，结果通过 Event 回传。这样既不会踩线程问题，也不会
    因为弹窗卡住录制回调。
    """
    result = {"name": None}
    done = threading.Event()

    def worker() -> None:
        try:
            _ensure_tcl_env()
            import tkinter as tk
            from tkinter import simpledialog

            root = tk.Tk()
            root.withdraw()
            try:
                root.attributes("-topmost", True)
            except Exception:
                pass
            # 窗口图标换成本项目的图标（tk 只认 .ico）
            ico = os.path.join(os.path.dirname(os.path.abspath(__file__)), "新图标.ico")
            if not os.path.exists(ico):
                ico = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "新图标.ico")
            if os.path.exists(ico):
                try:
                    root.iconbitmap(ico)
                except Exception:
                    pass

            def _grab() -> None:
                """对话框出来之后把它顶到最前。

                root 是 withdraw 的，simpledialog 会另建一个 Toplevel 当真正的窗口，
                topmost 只设在 root 上对它无效，所以这里遍历子窗口逐个顶。
                """
                try:
                    root.lift()
                    root.focus_force()
                except Exception:
                    pass
                for w in root.winfo_children():
                    try:
                        w.attributes("-topmost", True)
                        if os.path.exists(ico):
                            w.iconbitmap(ico)
                        w.lift()
                        w.focus_force()
                        w.after(60, lambda: _foreground(w))
                    except Exception:
                        pass

            root.after(120, _grab)
            name = simpledialog.askstring(
                "保存录制",
                "给这段录制起个名字：\n（留空 = 用默认名 %s；点取消 = 本次不保存）" % default,
                initialvalue="",
                parent=root,
            )
            root.destroy()
            result["name"] = None if name is None else name.strip()
        except Exception as exc:  # noqa: BLE001 - 弹窗失败不能让录制挂掉
            print("[battle_record] 弹输入框失败：%s" % exc, flush=True)
            result["name"] = default
        finally:
            done.set()

    threading.Thread(target=worker, daemon=True).start()
    done.wait(timeout=180)
    return result["name"]


def _run_session(rec: Recorder, name_hint: str = "",
                 allow_input: bool = False,
                 should_stop=None) -> Optional[str]:
    """跑一次录制会话，阻塞到 F12（或 Ctrl-C）。返回保存路径。

    ⚠️ 这里刻意**只用一个 keyboard.Listener**，不再叠加
    `keyboard.GlobalHotKeys`。两个 Listener 同时装 `WH_KEYBOARD_LL` 钩子会
    互相抢事件，表现就是「F10 时灵时不灵」；而且 GlobalHotKeys 会在钩子
    线程里直接跑回调，一旦回调里做存盘这类 IO，超过系统的
    LowLevelHooksTimeout（默认 300ms）后这个钩子的事件会被整段丢掉。

    所以：钩子线程里只做「判断 + 起后台线程」，任何耗时操作都扔出去。
    """
    import threading

    from pynput import keyboard, mouse

    # saving：正在弹输入框/存盘中。连按两次 F10 时第二次会被它挡住，
    # 否则会起两个 on_f10 线程、弹两个框、存出两份录制。
    # saved：F10 已经存过盘了。退出路径原来还会再问一次名字，
    # 于是「按一次 F10 弹两个框」——第一个存盘、第二个走退出分支又弹。
    state = {"stop": False, "saver": None, "stopped_by_ma": False,
             "saving": False, "saved": False}
    lock = threading.Lock()

    def on_f9():
        with lock:
            if rec.recording:
                rec.stop()
            else:
                rec.start()

    def on_f10():
        # 防重入：弹框是阻塞的，期间再按 F10 会在另一个线程里再进来一次，
        # 于是弹两个框、存出两份录制（用户实测遇到过）。
        with lock:
            if state["saving"]:
                rec.on_log("  [F10] 上一次保存还没结束，忽略这次。")
                return
            if not rec.recording:
                # 已经停下来但还没保存（上次弹窗被取消）时，再按 F10 直接补存
                if not rec.actions:
                    rec.on_log("  [F10] 当前没在录制，忽略。")
                    return
            else:
                rec.stop()
            state["saving"] = True
        default = name_hint or time.strftime("录制-%m%d-%H%M%S")
        # 弹输入框让用户自己起名（这里已经不在 lock 里，弹多久都不影响录制状态）
        try:
            chosen = _ask_save_name(default)
            if chosen is None:
                rec.on_log("  已取消保存（录制内容保留在内存，可再按 F10）。")
                return
            with lock:
                rec.save(chosen or default)
                state["saved"] = True
        except Exception as e:
            rec.on_log("  [F10] 保存出错：%s" % e)
            return
        finally:
            with lock:
                state["saving"] = False
        # 保存完就结束这次录制会话（不用再按 F12）
        state["stop"] = True

    def hotkey(key) -> None:
        """在钩子线程里跑，必须立刻返回。"""
        if key == keyboard.Key.f9:
            threading.Thread(target=on_f9, daemon=True).start()
        elif key == keyboard.Key.f10:
            t = threading.Thread(target=on_f10, daemon=True)
            state["saver"] = t
            t.start()
        elif key == keyboard.Key.f12:
            state["stop"] = True

    # 热键去重用的集合：系统对按住不放的键会持续发 keydown（自动重复），
    # 而 hotkey() 之前是**每次 on_press 都调**，没有任何去重。表现就是
    # 「只按了一次 F10，却弹出两个命名框」—— 一次物理按键发来了两次
    # keydown，于是起了两个 on_f10 线程。Recorder.on_key 有自己的 _held
    # 去重，但那条逻辑不覆盖热键。
    _hot_held: set = set()

    def on_press(key):
        try:
            rec.on_key(key, True)
        except Exception:
            pass
        if key in _hot_held:
            return
        _hot_held.add(key)
        hotkey(key)

    def on_release(key):
        _hot_held.discard(key)
        try:
            rec.on_key(key, False)
        except Exception:
            pass

    m_listener = mouse.Listener(
        on_click=rec.on_click, on_move=rec.on_move, on_scroll=rec.on_scroll)
    k_listener = keyboard.Listener(
        on_press=on_press,
        on_release=on_release,
    )

    m_listener.start()
    k_listener.start()
    try:
        # should_stop：MAA 界面上点「停止任务」时 context.tasker.stopping 会变 True。
        # 不加这个判断，这个循环只认 F12，点了停止任务也停不下来，
        # 表现就是「提示已经停止，但按钮一直不回弹」。
        while not state["stop"]:
            if should_stop is not None and should_stop():
                rec.on_log("  收到停止任务指令，退出录制。")
                state["stopped_by_ma"] = True
                break
            time.sleep(0.2)
    except KeyboardInterrupt:
        pass
    finally:
        m_listener.stop()
        k_listener.stop()
        saver = state.get("saver")
        if saver is not None:
            saver.join(10.0)  # 等 F10 那次存盘落盘

    if rec.recording:
        rec.stop()
    if not rec.actions:
        rec.on_log("  没有录到动作，退出。")
        return None
    if allow_input:
        try:
            ans = input("  文件名（回车用默认）：").strip()
        except EOFError:
            ans = ""
        return rec.save(ans or name_hint or time.strftime("录制-%m%d-%H%M%S"))
    default = name_hint or time.strftime("录制-%m%d-%H%M%S")
    if state.get("saved"):
        # F10 已经存过了，这里再问一次就是第二个弹框
        rec.on_log("  F10 已保存过，直接退出（不再弹框）。")
        return None
    if state.get("stopped_by_ma"):
        # 从 MAA 界面点了「停止任务」：这是无人值守场景，弹窗会卡住任务停止，
        # 直接用默认名落盘
        return rec.save(default)
    chosen = _ask_save_name(default)
    if chosen is None:
        rec.on_log("  已取消保存（录制内容未落盘）。")
        return None
    return rec.save(chosen or default)


def run_pipeline_session(name_hint: str = "", log=None,
                         should_stop=None) -> Optional[str]:
    """给 CustomAction 用：没有 stdin，不交互，F12 退出时自动按时间戳保存。"""
    rec = Recorder()
    rec.on_log = log or (lambda m: print(m, flush=True))
    if not rec.ensure_window():
        rec.on_log("  请先打开游戏再运行这个任务。")
        return None
    rec.on_log("热键：F9 开始/停止   F10 保存   F12 退出")
    return _run_session(rec, name_hint=name_hint, allow_input=False,
                        should_stop=should_stop)


# ---------------------------------------------------------------- MAA 入口

from maa.agent.agent_server import AgentServer  # noqa: E402
from maa.custom_action import CustomAction  # noqa: E402


@AgentServer.custom_action("battle_record")
class BattleRecord(CustomAction):
    """启动一次录制会话，按 F12 结束。

    ``custom_action_param`` 可选：
        {"name": "录制名前缀"}
    """

    def run(self, context, argv) -> bool:  # noqa: ANN001 - MAA 接口签名
        raw = getattr(argv, "custom_action_param", None)
        try:
            param = json.loads(raw) if raw else {}
        except Exception:
            param = {}
        name_hint = str(param.get("name") or "").strip()

        def log(msg: str) -> None:
            text = "[battle_record] %s" % msg
            # MFA 把 agent 的 stdout 按 GBK 解码，像 [OK] 这种符号编不出来会抛
            # UnicodeEncodeError，整个录制流程就断在那里。先探测再降级。
            enc = getattr(sys.stdout, "encoding", None) or "utf-8"
            try:
                text.encode(enc)
            except (UnicodeEncodeError, LookupError):
                text = text.replace("[OK]", "[OK]").replace("✗", "[X]").replace("→", "->")
                try:
                    text.encode(enc)
                except (UnicodeEncodeError, LookupError):
                    text = text.encode(enc, "replace").decode(enc, "replace")
            print(text, flush=True)

        log("任务开始：请在游戏里操作，F9 开始/停止，F10 保存，F12 退出。")

        def _stopping() -> bool:
            try:
                return bool(context.tasker.stopping)
            except Exception:
                return False

        try:
            saved = run_pipeline_session(name_hint=name_hint, log=log,
                                         should_stop=_stopping)
        except Exception as exc:
            log("录制出错：%s" % exc)
            return False

        if saved:
            log("录制结束，文件：%s" % saved)
            log("要在回放任务里用它，把「回放战斗」节点的 "
                "custom_action_param.file 改成：%s"
                % os.path.splitext(os.path.basename(saved))[0])
        else:
            log("没有录制内容。")
        return True


# ---------------------------------------------------------------- 窗口置顶
# os.startfile 打开资源管理器后，新窗口经常被别的窗口（尤其是置顶的 MFA 主窗）
# 压在下面，用户看到的是「点了没反应」。下面两步把窗口强制拉到最前。
_k32 = ctypes.windll.kernel32
_u32 = ctypes.windll.user32
_u32.GetWindowThreadProcessId.argtypes = [ctypes.wintypes.HWND, ctypes.POINTER(ctypes.wintypes.DWORD)]
_u32.GetWindowThreadProcessId.restype = ctypes.wintypes.DWORD
_u32.SetForegroundWindow.argtypes = [ctypes.wintypes.HWND]
_u32.SetForegroundWindow.restype = ctypes.wintypes.BOOL
_u32.BringWindowToTop.argtypes = [ctypes.wintypes.HWND]
_u32.BringWindowToTop.restype = ctypes.wintypes.BOOL
_u32.ShowWindow.argtypes = [ctypes.wintypes.HWND, ctypes.c_int]
_u32.ShowWindow.restype = ctypes.wintypes.BOOL

_WNDENUMPROC = ctypes.WINFUNCTYPE(
    ctypes.wintypes.BOOL, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM)
_u32.EnumWindows.argtypes = [_WNDENUMPROC, ctypes.wintypes.LPARAM]
_u32.EnumWindows.restype = ctypes.wintypes.BOOL


def _bring_to_front(hwnd):
    """把窗口恢复并拉到最前。

    Windows 只允许「前台进程」调用 SetForegroundWindow，否则只闪任务栏。
    用 AttachThreadInput 把本线程挂进目标窗口的输入队列即可绕过这个限制。
    """
    if not hwnd:
        return False
    try:
        _u32.ShowWindow(hwnd, 9)  # SW_RESTORE
        fg = _u32.GetForegroundWindow()
        tid_self = _k32.GetCurrentThreadId()
        tid_fg = _u32.GetWindowThreadProcessId(fg, None) if fg else 0
        tid_tgt = _u32.GetWindowThreadProcessId(hwnd, None)
        attached = []
        for tid in (tid_fg, tid_tgt):
            if tid and tid != tid_self:
                if _u32.AttachThreadInput(tid_self, tid, True):
                    attached.append(tid)
        _u32.BringWindowToTop(hwnd)
        ok = bool(_u32.SetForegroundWindow(hwnd))
        for tid in attached:
            _u32.AttachThreadInput(tid_self, tid, False)
        return ok
    except Exception:
        return False


def _find_explorer_window(folder_name, timeout=5.0):
    """在 timeout 内找标题里带 folder_name 的资源管理器窗口，返回 hwnd。"""
    want = folder_name.lower()
    end = time.time() + timeout
    while time.time() < end:
        hits = []

        def _cb(hwnd, _lparam):
            cls = ctypes.create_unicode_buffer(256)
            _u32.GetClassNameW(hwnd, cls, 256)
            if cls.value != "CabinetWClass":
                return True
            ttl = ctypes.create_unicode_buffer(512)
            _u32.GetWindowTextW(hwnd, ttl, 512)
            if want in ttl.value.lower():
                hits.append(hwnd)
            return True

        _u32.EnumWindows(_WNDENUMPROC(_cb), 0)
        if hits:
            return hits[-1]
        time.sleep(0.15)
    return 0

@AgentServer.custom_action("open_recordings")
class OpenRecordings(CustomAction):
    """用资源管理器打开 recordings 目录。

    MAA 的配置项（option）只有 input / hotkey / select / checkbox / switch 五种，
    没有「按钮」这种类型，所以这里做成一个小任务 —— 在任务列表里点它等于点按钮。
    """

    def run(self, context, argv) -> bool:  # noqa: ANN001 - MAA 接口签名
        os.makedirs(RECORDING_DIR, exist_ok=True)
        # 必须在后台线程里开：os.startfile 走 ShellExecute，资源管理器忙的时候
        # 会阻塞好几秒甚至更久。run() 被卡住期间 MAA 认为任务还没结束，
        # 于是点了「停止任务」之后按钮一直不恢复。
        def _open():
            try:
                os.startfile(RECORDING_DIR)  # noqa: S606 - 只打开目录，不执行文件
                # 等资源管理器把窗口建出来，再把它顶到最前
                hwnd = _find_explorer_window(os.path.basename(RECORDING_DIR))
                if hwnd:
                    _bring_to_front(hwnd)
                    print("[open_recordings] 已打开并置顶：%s" % RECORDING_DIR, flush=True)
                else:
                    print("[open_recordings] 已打开（未找到窗口句柄，无法置顶）：%s" % RECORDING_DIR,
                          flush=True)
            except Exception as exc:
                print(
                    "[open_recordings] 打开目录失败：%s（路径：%s）" % (exc, RECORDING_DIR),
                    flush=True,
                )

        threading.Thread(target=_open, daemon=True).start()
        return True
