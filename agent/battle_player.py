"""局内战斗录制回放 —— 回放部分。

设计约定
--------
录制文件放在项目根目录的 ``recordings/`` 下，JSON 格式：

    {
      "version": 1,
      "name": "万世冥阁-剑阵流",
      "base": [1147, 720],          // 录制时的截图尺寸，仅作参考
      "actions": [
        {"t": 0,    "op": "key",   "key": 72},
        {"t": 800,  "op": "down",  "x": 640, "y": 400},
        {"t": 60,   "op": "move",  "x": 700, "y": 450},
        {"t": 60,   "op": "up"},
        {"t": 1200, "op": "click", "x": 640, "y": 400}
      ]
    }

* ``t`` 是**相对上一个动作的毫秒数**，第一个动作相对回放开始。
* 坐标一律是 **720p 基准坐标**（MaaFramework 截图短边 720，与 pipeline ROI 同一套），
  所以换分辨率、换 DPI 都不用改录制文件 —— 框架在内部会换算回原图。
* ``op`` 支持：``key`` / ``keydown`` / ``keyup`` / ``click`` / ``down`` / ``move`` / ``up``
  / ``text`` / ``scroll``。

回放是一次性的：把 ``actions`` 从头执行到尾就结束，不循环、不判断「打没打完」。
想打几波，就把几波的完整操作一起录下来。
"""

import json
import os
import sys
import threading
import time
from typing import Any, Dict, List, Optional

from maa.agent.agent_server import AgentServer
from maa.custom_action import CustomAction

try:
    from toast import toast as _toast
except Exception:  # 通知不可用时不能让回放挂掉

    def _toast(*_a, **_k):
        pass

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RECORDING_DIR = os.path.join(_PROJECT_ROOT, "recordings")
# interface.json 的「回放文件」下拉里，这一项代表「别用列表里的，弹框让我自己挑」
# （必须和 tools/gen_recording_options.py 里的 MANUAL 保持一致）
MANUAL_CASE = "（手动选择文件…）"
# 「最新录制」伪 case：下拉里固定存在，运行时才去磁盘上找最新那条。
# 有了它，录完新战斗不用重启 MFA（interface.json 的 cases 是静态的，
# 重启前不会变），直接跑就能用上刚录的那份。
LATEST_CASE = "（最新录制）"
LATEST_TOKEN = "@latest"


def _ensure_tcl_env() -> None:
    """内嵌运行时的 tcl 不在默认搜索路径，tkinter 要靠这两个变量才找得到 init.tcl。"""
    if os.environ.get("TCL_LIBRARY"):
        return
    prefix = os.path.dirname(os.path.abspath(sys.executable))
    for name, sub in (("TCL_LIBRARY", "tcl8.6"), ("TK_LIBRARY", "tk8.6")):
        path = os.path.join(prefix, "tcl", sub)
        if os.path.isdir(path):
            os.environ[name] = path


LAST_FILE = os.path.join(RECORDING_DIR, ".last.json")


def _last_pick() -> str:
    try:
        with open(LAST_FILE, encoding="utf-8") as f:
            return str(json.load(f).get("file") or "")
    except Exception:
        return ""


def _remember_pick(path: str) -> None:
    try:
        os.makedirs(RECORDING_DIR, exist_ok=True)
        with open(LAST_FILE, "w", encoding="utf-8") as f:
            json.dump({"file": path}, f, ensure_ascii=False)
    except Exception:
        pass


def ask_recording_file() -> Optional[str]:
    """弹系统文件选择框挑一个录制 json，返回绝对路径；取消返回 None。

    和 `_ask_save_name` 同样的顾虑：Tk 必须在创建它的线程里用，而 MAA 的
    回调不在主线程，所以单开线程建窗口、结果用 Event 回传。
    """
    result: Dict[str, Optional[str]] = {"path": None}
    done = threading.Event()

    def worker() -> None:
        try:
            _ensure_tcl_env()
            import tkinter as tk
            from tkinter import filedialog

            root = tk.Tk()
            root.withdraw()
            try:
                root.attributes("-topmost", True)
            except Exception:
                pass
            os.makedirs(RECORDING_DIR, exist_ok=True)
            path = filedialog.askopenfilename(
                title="选择要回放的录制文件",
                initialdir=RECORDING_DIR,
                initialfile=_last_pick() or "",
                filetypes=[("录制文件", "*.json"), ("所有文件", "*.*")],
                parent=root,
            )
            root.destroy()
            result["path"] = path or None
        except Exception as exc:  # noqa: BLE001 - 弹框失败要让回放给出明确原因
            print("[battle_play] 文件选择框失败：%s" % exc, flush=True)
            result["path"] = None
        finally:
            done.set()

    threading.Thread(target=worker, daemon=True).start()
    done.wait(timeout=300)
    return result["path"]


def newest_recording() -> Optional[str]:
    """recordings/ 里最近修改的那条录制（不含点开头的辅助文件）。"""
    try:
        items = []
        for fn in os.listdir(RECORDING_DIR):
            if fn.startswith(".") or not fn.lower().endswith(".json"):
                continue
            fp = os.path.join(RECORDING_DIR, fn)
            items.append((os.path.getmtime(fp), fn[:-5]))
        if not items:
            return None
        items.sort(reverse=True)
        return items[0][1]
    except Exception:
        return None


def recording_path(name: str) -> str:
    """名字 → 录制文件路径。

    支持三种写法：
    * ``示例-最小``      → ``recordings/示例-最小.json``
    * ``示例-最小.json`` → ``recordings/示例-最小.json``
    * 绝对路径           → 原样使用（文件选择框选外部文件时走这条）
    """
    if os.path.isabs(name):
        return name
    if name.endswith(".json"):
        return os.path.join(RECORDING_DIR, name)
    return os.path.join(RECORDING_DIR, name + ".json")


def load_recording(name: str) -> Dict[str, Any]:
    path = recording_path(name)
    if not os.path.exists(path):
        raise FileNotFoundError("找不到录制文件：%s" % path)
    with open(path, encoding="utf-8") as f:
        rec = json.load(f)

    actions = rec.get("actions")
    if not isinstance(actions, list) or not actions:
        raise ValueError("录制文件里没有 actions：%s" % path)

    known = {"key", "keydown", "keyup", "click", "down", "move", "up", "text", "scroll"}
    for i, act in enumerate(actions):
        if not isinstance(act, dict):
            raise ValueError("第 %d 个动作不是对象：%r" % (i, act))
        op = act.get("op")
        if op not in known:
            raise ValueError("第 %d 个动作的 op 不认识：%r" % (i, op))
        if op in ("click", "down", "move") and ("x" not in act or "y" not in act):
            raise ValueError("第 %d 个动作（%s）缺少 x / y" % (i, op))
        if op in ("key", "keydown", "keyup") and "key" not in act:
            raise ValueError("第 %d 个动作（%s）缺少 key" % (i, op))
        if op == "text" and "text" not in act:
            raise ValueError("第 %d 个动作（text）缺少 text" % i)

    return rec


_TIMER_BOOSTED = [False]


def _boost_timer() -> None:
    """把系统计时器精度提到 1ms。

    Windows 默认的 `time.sleep` 粒度是 ~15.6ms，请求 26ms 实际可能睡 31ms，
    一次就多出一个 tick —— 这就是「回放忽快忽慢」的另一半原因。
    调一次 timeBeginPeriod(1) 就能把粒度降到 1ms（很多游戏自己也会调）。
    """
    if _TIMER_BOOSTED[0]:
        return
    try:
        ctypes.windll.winmm.timeBeginPeriod(1)
        _TIMER_BOOSTED[0] = True
    except Exception:
        pass


def _sleep_until(target: float) -> None:
    """睡到 monotonic 的 target 时刻。

    不能直接用 `time.sleep(gap)`：那样每个动作的实际间隔是
    「sleep 的 gap + 执行这个动作花的时间」，而执行耗时是变化的
    （按键 1~2ms、点击 5~15ms），误差会累积 —— 表现就是「回放忽快忽慢」。

    Windows 上 `time.sleep` 的粒度约 15.6ms，短间隔会被拉长，所以最后
    2ms 改成自旋，把误差压到 1ms 以内。
    """
    _boost_timer()
    # 先睡，最后 1.5ms 自旋。自旋窗口刻意压得很小：机器忙的时候
    # 长时间自旋反而会被调度器抢占，误差更大。
    remain = target - time.monotonic()
    if remain > 0.0015:
        time.sleep(remain - 0.0015)
    while time.monotonic() < target:
        pass


def _dispatch(ctrl, act: Dict[str, Any]) -> None:
    """执行单个动作。所有 job 都 wait，保证顺序。"""
    op = act["op"]
    if op == "key":
        ctrl.post_click_key(int(act["key"])).wait()
    elif op == "keydown":
        ctrl.post_key_down(int(act["key"])).wait()
    elif op == "keyup":
        ctrl.post_key_up(int(act["key"])).wait()
    elif op == "click":
        ctrl.post_click(int(act["x"]), int(act["y"])).wait()
    elif op == "down":
        ctrl.post_touch_down(int(act["x"]), int(act["y"])).wait()
    elif op == "move":
        ctrl.post_touch_move(int(act["x"]), int(act["y"])).wait()
    elif op == "up":
        ctrl.post_touch_up().wait()
    elif op == "text":
        ctrl.post_input_text(str(act["text"])).wait()
    elif op == "scroll":
        ctrl.post_scroll(int(act.get("dx", 0)), int(act.get("dy", 0))).wait()


@AgentServer.custom_action("battle_play")
class BattlePlay(CustomAction):
    """把一条录制序列原样回放一遍。

    ``custom_action_param`` 支持：
        {"file": "万世冥阁-剑阵流", "speed": 1.0}
    """

    def run(self, context, argv) -> bool:  # noqa: ANN001 - MAA 的接口签名
        raw = getattr(argv, "custom_action_param", None)
        # MAA 传过来的形态有三种：
        #   1) dict        —— pipeline 里直接写对象
        #   2) JSON 字符串  —— pipeline 里写 "{\"file\": \"xxx\", \"speed\": 1.0}"
        #   3) 纯文件名     —— pipeline 里写 "{回放文件}"，由 MFA 的 option 替换填充
        #
        # 注意（踩过的坑）：MFA 的占位符替换**只作用于字符串标量**，嵌套对象内部的
        # "{回放文件}" 会原样保留，于是读到的是 recordings\{回放文件}.json 然后报
        # 「找不到录制文件」。所以 interface.json 里 option 的 custom_action_param
        # 必须写成字符串，不能写成 {"file": "{回放文件}"}。
        param = {}
        if isinstance(raw, dict):
            param = raw
        elif isinstance(raw, str) and raw.strip():
            s = raw.strip()
            try:
                obj = json.loads(s)
            except Exception:
                obj = None
            if isinstance(obj, dict):
                param = obj
            elif obj is not None:
                param = {"file": str(obj)}
            else:
                param = {"file": s}
        else:
            param = {}

        name = str(param.get("file") or param.get("name") or "").strip()
        if name == LATEST_TOKEN:
            latest = newest_recording()
            if latest:
                name = latest
                self._log("[battle_play] 使用最新录制：%s" % latest)
            else:
                # 一个录制都还没有：退回文件选择框，别直接失败
                self._log("[battle_play] 还没有任何录制，改为手动选择文件")
                name = ""
        if not name or name in ("?", "？", "select", "选择", "选择文件", MANUAL_CASE):
            # 没写文件名（或写了 ?）就弹系统文件选择框，省得手打
            picked = ask_recording_file()
            if not picked:
                self._log("[battle_play] 没有选文件，取消回放")
                return False
            _remember_pick(picked)
            name = picked
            self._log("[battle_play] 已选择：%s" % picked)

        speed = float(param.get("speed", 1.0) or 1.0)
        if speed <= 0:
            speed = 1.0

        try:
            rec = load_recording(name)
        except Exception as exc:  # 文件缺失 / 格式错误都在这里
            self._log("[battle_play] 录制文件有问题：%s" % exc)
            return False

        actions: List[Dict[str, Any]] = rec["actions"]
        ctrl = context.tasker.controller
        self._log(
            "[battle_play] 回放「%s」：%d 个动作，倍率 %.2f"
            % (rec.get("name") or name, len(actions), speed)
        )
        # 第一个动作的 t 现在可能是非 0（录制开始后先等了一会儿），
        # 回放会照等，所以先把它报出来，免得用户以为卡住。
        _lead = max(0.0, float(actions[0].get("t", 0) or 0)) / 1000.0 / speed
        _total = sum(max(0.0, float(a.get("t", 0) or 0)) for a in actions) / 1000.0 / speed
        if _lead >= 0.5:
            self._log("[battle_play] 起手等待 %.1f 秒（按录制时的空档重现）" % _lead)
        self._log("[battle_play] 回放总时长 %.1f 秒" % _total)

        started = time.monotonic()
        # 绝对时刻调度：每个动作的目标时刻 = 起点 + 累计时间偏移。
        # 某个动作执行得慢，下一个动作就少睡一点，整体不会越跑越慢。
        offset = 0.0
        for i, act in enumerate(actions):
            # MAA 界面上点「停止任务」时 stopping 会变 True。
            # 不检查它，回放会一直跑到最后，任务也停不下来（按钮不回弹）。
            try:
                if context.tasker.stopping:
                    self._log("[battle_play] 收到停止任务指令，中止回放")
                    try:
                        ctrl.post_touch_up().wait()
                    except Exception:
                        pass
                    return False
            except Exception:
                pass

            offset += max(0.0, float(act.get("t", 0))) / 1000.0 / speed
            _sleep_until(started + offset)
            try:
                _dispatch(ctrl, act)
            except Exception as exc:
                self._log("[battle_play] 第 %d 个动作（%s）执行失败：%s" % (i, act.get("op"), exc))
                # 按键/触摸按下去没抬起来会让游戏卡在按住状态，兜底松一次
                try:
                    ctrl.post_touch_up().wait()
                except Exception:
                    pass
                return False

        elapsed = time.monotonic() - started
        self._log("[battle_play] 回放结束，耗时 %.1f 秒" % elapsed)
        return True

    @staticmethod
    def _log(msg: str) -> None:
        # agent 的 stdout 不会被 MAA 的 GUI 日志面板转发，
        # 但会进 debug/maafw.log 的 agent 输出段，排查够用。
        print(msg, flush=True)


def list_recordings() -> List[str]:
    """列出 recordings/ 下所有录制名（不含 .json），给录制工具和排查用。"""
    if not os.path.isdir(RECORDING_DIR):
        return []
    names = []
    for fn in sorted(os.listdir(RECORDING_DIR)):
        if fn.lower().endswith(".json"):
            names.append(fn[:-5])
    return names
