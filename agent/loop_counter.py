# -*- coding: utf-8 -*-
#
# 造梦西游4 · 生存大冒险 自动答题
# 作者：InkyuZero   (https://github.com/Inkyu-Zero/MAAZMXY4)
#
# 本项目基于 MaaFramework 开发，完全免费。
# 如果你是付费获得的，请及时申请退款 —— 本项目从未收费。
#
"""循环计数：给「刷取次数」这类循环节点补一行「第 N 次」日志。

为什么要单独一个 CustomAction：
  `刷取次数` 节点本身是 DirectHit + 空动作，循环几轮就在 MFA 面板里静静地跑几轮，
  出了岔子（第 3 轮没进去、第 5 轮卡住）完全看不出来。这里在每一轮回合开头
  把当前轮次写进 `debug/loop/panel.md`，由节点自己的 `focus` 取走显示到面板。

  轮次是「同一 label 连续命中」的计数，label 变化或空闲超过 120 秒即归零 ——
  不依赖任务开始/结束事件，也不会跨任务串号。
"""
from __future__ import annotations

import json
import os
import threading
import time
from typing import Any

from maa.agent.agent_server import AgentServer
from maa.custom_action import CustomAction

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_PANEL = os.path.join(_ROOT, "debug", "loop", "panel.md")

_IDLE_RESET = 120.0  # 秒：隔这么久没被叫到就当新的一轮开始
_lock = threading.Lock()
_state: dict = {"label": "", "n": 0, "at": 0.0}


def _param(argv: Any) -> dict:
    """取出 custom_action_param（MAA 可能给 dict，也可能给 JSON 字符串）。"""
    p = getattr(argv, "custom_action_param", None)
    if p is None and isinstance(argv, dict):
        p = argv.get("custom_action_param")
    if isinstance(p, str) and p.strip():
        try:
            p = json.loads(p)
        except Exception:
            return {}
    return p if isinstance(p, dict) else {}


def _write_panel(text: str) -> None:
    try:
        os.makedirs(os.path.dirname(_PANEL), exist_ok=True)
        with open(_PANEL, "w", encoding="utf-8", newline="\n") as fp:
            fp.write(text)
    except Exception:
        pass


@AgentServer.custom_action("loop_count")
class LoopCount(CustomAction):
    """每轮开头报一次「第 N 次」，写面板 + stdout。"""

    def run(self, context: Any, argv: Any) -> bool:
        p = _param(argv)
        label = str(p.get("label") or "").strip() or "循环"
        now = time.time()
        with _lock:
            if _state["label"] != label or now - _state["at"] > _IDLE_RESET:
                _state["label"] = label
                _state["n"] = 0
            _state["n"] += 1
            _state["at"] = now
            n = _state["n"]

        line = "%s：第 %d 次" % (label, n)
        # 面板用 markdown：任务名正常加粗，次数加粗以便一眼看到
        _write_panel("**%s** ｜ 第 **%d** 次" % (label, n) + "\n")
        print(line, flush=True)
        return True
