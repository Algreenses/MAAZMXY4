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

import ctypes
import hashlib
import json
import math
import os
import re
import sys
import threading
import time
from typing import Any, Dict, List, Optional

from maa.agent.agent_server import AgentServer
from maa.custom_action import CustomAction

from script_studio.model import MAX_FILE_BYTES, RecordingDocument
from script_library import resolve_recording_reference

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
SELECTED_CASE = "（脚本工具所选）"
SELECTED_TOKEN = "@selected"


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
            if not os.path.isfile(fp):
                continue
            items.append((os.path.getmtime(fp), fn[:-5]))
        if not items:
            return None
        items.sort(reverse=True)
        return items[0][1]
    except Exception:
        return None


def selected_recording() -> Optional[str]:
    """Resolve the live studio selection; a stale selection must never run another file."""
    selection = os.path.join(RECORDING_DIR, ".selected.json")
    if not os.path.exists(selection):
        return None
    try:
        with open(selection, encoding="utf-8-sig") as stream:
            data = json.load(stream)
    except Exception as exc:
        raise ValueError("脚本工具选择记录损坏，请重新选择脚本") from exc
    if not isinstance(data, dict):
        raise ValueError("脚本工具选择记录无效，请重新选择脚本")
    name = data.get("file")
    if name is None or name == "":
        return None
    if (not isinstance(name, str) or name.startswith(".") or not name.lower().endswith(".json")
            or os.path.basename(name) != name or any(char in name for char in '<>:"/\\|?*')):
        raise ValueError("脚本工具选择文件无效，请在脚本库重新选择")
    path = os.path.join(RECORDING_DIR, name)
    if not os.path.isfile(path):
        raise FileNotFoundError("脚本工具所选文件已不存在：%s；请重新选择，未自动改用其他脚本" % name)
    return name


def recording_path(name: str) -> str:
    """名字 → 录制文件路径。

    支持三种写法：
    * ``示例-最小``      → ``recordings/示例-最小.json``
    * ``示例-最小.json`` → ``recordings/示例-最小.json``
    * 绝对路径           → 原样使用（文件选择框选外部文件时走这条）
    """
    if os.path.isabs(name):
        return name
    if name.lower().endswith(".json"):
        return os.path.join(RECORDING_DIR, name)
    return os.path.join(RECORDING_DIR, name + ".json")


def load_recording(name: str, *, expected_sha256=None, strict=False,
                   expected_base=None, expected_short_side=None) -> Dict[str, Any]:
    path = str(resolve_recording_reference(RECORDING_DIR, name)) if strict else recording_path(name)
    if not os.path.exists(path):
        raise FileNotFoundError("找不到录制文件：%s" % path)
    if os.path.getsize(path) > MAX_FILE_BYTES:
        raise ValueError("录制文件不能超过 16 MiB")
    with open(path, "rb") as f:
        raw = f.read(MAX_FILE_BYTES + 1)
    if len(raw) > MAX_FILE_BYTES:
        raise ValueError("录制文件不能超过 16 MiB")
    if expected_sha256 is not None:
        if not isinstance(expected_sha256, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", expected_sha256):
            raise ValueError("录制版本校验值无效")
        if hashlib.sha256(raw).hexdigest() != expected_sha256.lower():
            raise ValueError("录制在本轮准备后已改变，本轮未回放；请重新开始以读取保存后的版本")
    rec = json.loads(raw.decode("utf-8-sig"))
    document = RecordingDocument.from_dict(rec)
    if not rec.get("actions"):
        raise ValueError("录制文件里没有 actions：%s" % path)
    if expected_base is not None:
        if (not isinstance(expected_base, (list, tuple)) or len(expected_base) != 2
                or any(type(value) is not int or value < 1 for value in expected_base)):
            raise ValueError("任务预期录制尺寸无效")
        if list(document.base) != list(expected_base):
            raise ValueError("录制基准尺寸与任务当前截图不一致，请重新导入或录制")
    short_side = 720 if strict and expected_short_side is None else expected_short_side
    if short_side is not None:
        if type(short_side) is not int or not 1 <= short_side <= 32768:
            raise ValueError("任务预期录制短边无效")
        if min(document.base) != short_side:
            raise ValueError("任务脚本必须使用短边%d坐标，不能直接回放原尺寸录制" % short_side)
    if strict:
        document.to_dict()  # New task bindings never silently repair input pairs.
    # Legacy recorder files can end while a key/button is still held. Preserve
    # their timing and report this, while the player guarantees a final release.
    pair_warnings = []
    for diagnostic in document.diagnostics:
        if re.fullmatch(r"(?:键 \d+|鼠标 (?:left|right|middle)) (?:未释放|重复按下|释放没有对应按下)", diagnostic):
            pair_warnings.append(diagnostic)
        else:
            raise ValueError(diagnostic)
    for action in rec["actions"]:
        if action["op"] == "scroll" and ("x" in action or "y" in action):
            raise ValueError("此脚本含有带坐标的滚轮动作，当前后台输入无法可靠保留滚轮位置；整次回放未开始，请在脚本工具调整")
    if pair_warnings:
        message = "旧录制存在键鼠配对问题，结束/停止时会补释放；建议在脚本工具修复：" + "；".join(pair_warnings[:4])
        print("[battle_play] 警告：" + message, flush=True)
        _toast("录制兼容提示", message)
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


def _sleep_until(target: float, should_stop=None) -> bool:
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
    while True:
        if should_stop is not None and should_stop():
            return False
        remain = target - time.monotonic()
        if remain <= 0:
            return True
        if remain > 0.0015:
            time.sleep(min(0.02, remain - 0.0015))


_BUTTON_CONTACTS = {"left": 0, "right": 1, "middle": 2}


def _wait_job(job) -> None:
    result = job.wait()
    if result is False or getattr(result, "succeeded", True) is False:
        raise RuntimeError("Maa 输入作业执行失败")


def _dispatch(ctrl, act: Dict[str, Any], active_buttons=None) -> None:
    """执行单个动作。所有 job 都 wait，保证顺序。"""
    op = act["op"]
    if op == "key":
        _wait_job(ctrl.post_click_key(int(act["key"])))
    elif op == "keydown":
        _wait_job(ctrl.post_key_down(int(act["key"])))
    elif op == "keyup":
        _wait_job(ctrl.post_key_up(int(act["key"])))
    elif op == "click":
        _wait_job(ctrl.post_click(int(act["x"]), int(act["y"])))
    elif op == "down":
        contact = _BUTTON_CONTACTS[act.get("button", "left")]
        _wait_job(ctrl.post_touch_down(int(act["x"]), int(act["y"]), contact=contact))
    elif op == "move":
        # Moves belong to every held mouse button; legacy standalone moves keep
        # their original left-contact behavior.
        for contact in sorted(active_buttons or {0}):
            _wait_job(ctrl.post_touch_move(int(act["x"]), int(act["y"]), contact=contact))
    elif op == "up":
        contact = _BUTTON_CONTACTS[act.get("button", "left")]
        if "x" in act and "y" in act:
            # New capture/import files retain the final drag endpoint even when
            # the preceding move was rate-limited. Old coordinate-free up stays unchanged.
            _wait_job(ctrl.post_touch_move(int(act["x"]), int(act["y"]), contact=contact))
        _wait_job(ctrl.post_touch_up(contact=contact))
    elif op == "text":
        _wait_job(ctrl.post_input_text(str(act["text"])))
    elif op == "scroll":
        _wait_job(ctrl.post_scroll(int(act.get("dx", 0)), int(act.get("dy", 0))))


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
        strict_task = param.get("strict_task", False)
        if type(strict_task) is not bool:
            self._log("[battle_play] strict_task 必须为布尔值")
            return False
        if strict_task or name.startswith("@task:"):
            strict_task = True
            try:
                name = str(resolve_recording_reference(RECORDING_DIR, name))
            except (OSError, ValueError) as exc:
                self._log("[battle_play] %s" % exc)
                _toast("任务脚本无法回放", str(exc))
                return False
        if name in (SELECTED_TOKEN, SELECTED_CASE):
            try:
                selected = selected_recording()
            except (ValueError, FileNotFoundError) as exc:
                self._log("[battle_play] %s" % exc)
                _toast("无法回放所选脚本", str(exc))
                return False
            if selected is not None:
                name = selected
                self._log("[battle_play] 使用脚本工具所选：%s" % selected)
            else:
                name = LATEST_TOKEN
                self._log("[battle_play] 脚本工具尚未选择文件，使用最新录制")
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

        try:
            speed_value = param.get("speed", 1.0)
            if isinstance(speed_value, bool):
                raise ValueError
            speed = float(speed_value)
            if not math.isfinite(speed) or not 0.01 <= speed <= 100:
                raise ValueError
        except (TypeError, ValueError, OverflowError):
            self._log("[battle_play] 回放速度必须是 0.01～100 的有限数值")
            return False

        try:
            rec = load_recording(name, expected_sha256=param.get("expected_sha256"), strict=strict_task,
                                 expected_base=param.get("expected_base"),
                                 expected_short_side=param.get("expected_short_side"))
        except Exception as exc:  # 文件缺失 / 格式错误都在这里
            self._log("[battle_play] 录制文件有问题：%s" % exc)
            _toast("录制文件无法回放", str(exc))
            return False

        actions: List[Dict[str, Any]] = rec["actions"]
        ctrl = context.tasker.controller
        self._log(
            "[battle_play] 回放「%s」：%d 个动作，倍率 %.2f"
            % (rec.get("name") or name, len(actions), speed)
        )
        # 第一个动作的 t 现在可能是非 0（录制开始后先等了一会儿），
        # 回放会照等，所以先把它报出来，免得用户以为卡住。
        _lead = float(actions[0].get("t", 0)) / 1000.0 / speed
        action_duration = sum(float(a.get("t", 0)) for a in actions)
        _total = float(rec.get("duration_ms", action_duration)) / 1000.0 / speed
        if _lead >= 0.5:
            self._log("[battle_play] 起手等待 %.1f 秒（按录制时的空档重现）" % _lead)
        self._log("[battle_play] 回放总时长 %.1f 秒" % _total)

        started = time.monotonic()
        # 绝对时刻调度：每个动作的目标时刻 = 起点 + 累计时间偏移。
        # 某个动作执行得慢，下一个动作就少睡一点，整体不会越跑越慢。
        offset, held_keys, held_buttons, loose_contacts = 0.0, set(), set(), set()
        def stopping():
            try:
                return bool(context.tasker.stopping)
            except Exception:
                return False
        completed, cleanup_errors = False, []
        try:
            for i, act in enumerate(actions):
                offset += float(act.get("t", 0)) / 1000.0 / speed
                if stopping() or not _sleep_until(started + offset, stopping) or stopping():
                    self._log("[battle_play] 收到停止任务指令，中止回放")
                    break
                # Remember a press before submission: a failing native job may
                # have pressed the device before reporting failure.
                op = act["op"]
                transient_key = int(act["key"]) if op == "key" else None
                had_key = transient_key in held_keys
                had_left = 0 in held_buttons
                press_key = int(act["key"]) if op in ("keydown", "key") else None
                press_contact = (_BUTTON_CONTACTS[act.get("button", "left")] if op == "down" else
                                 0 if op == "click" else None)
                key_was_held = press_key in held_keys
                contact_was_held = press_contact in held_buttons
                if op in ("keydown", "key"):
                    held_keys.add(int(act["key"]))
                if op in ("down", "click"):
                    held_buttons.add(_BUTTON_CONTACTS[act.get("button", "left")] if op == "down" else 0)
                try:
                    # Check once more immediately before dispatch, after local
                    # bookkeeping, so cancellation never schedules an old action.
                    if stopping():
                        if not key_was_held:
                            held_keys.discard(press_key)
                        if not contact_was_held:
                            held_buttons.discard(press_contact)
                        break
                    if op == "move" and not held_buttons:
                        # The legacy Win32 touch-move API carries a left-button
                        # flag even for recorded hover movement. Clear it on exit.
                        loose_contacts.add(0)
                    _dispatch(ctrl, act, held_buttons)
                except Exception as exc:
                    self._log("[battle_play] 第 %d 个动作（%s）执行失败：%s" % (i, op, exc))
                    break
                if op == "keyup":
                    held_keys.discard(int(act["key"]))
                elif op == "key" and not had_key:
                    held_keys.discard(transient_key)
                elif op == "up":
                    held_buttons.discard(_BUTTON_CONTACTS[act.get("button", "left")])
                    loose_contacts.discard(_BUTTON_CONTACTS[act.get("button", "left")])
                elif op == "click" and not had_left:
                    held_buttons.discard(0)
            else:
                # The editor preserves the empty interval after the final event.
                completed = _sleep_until(started + _total, stopping) and not stopping()
        finally:
            # Cleanup does not consult stopping: cancellation must never prevent
            # releases, and one failed release must not skip the remaining ones.
            for key in sorted(held_keys):
                try:
                    _wait_job(ctrl.post_key_up(key))
                except Exception as exc:
                    cleanup_errors.append("释放键 %d：%s" % (key, exc))
            for contact in sorted(held_buttons | loose_contacts):
                try:
                    _wait_job(ctrl.post_touch_up(contact=contact))
                except Exception as exc:
                    cleanup_errors.append("释放鼠标 %d：%s" % (contact, exc))
        if cleanup_errors:
            self._log("[battle_play] 输入清理未完成：%s" % "；".join(cleanup_errors))
            return False
        if not completed:
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
        if (not fn.startswith(".") and fn.lower().endswith(".json")
                and os.path.isfile(os.path.join(RECORDING_DIR, fn))):
            names.append(fn[:-5])
    return names
