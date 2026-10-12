# -*- coding: utf-8 -*-
"""事件识别回归检查：找「某个事件被系统性认成另一个事件」这类静默 bug。

v1.4 修掉过一个隐蔽 bug：`adventure_ui.json` 里「危险任务」的关键词含一个光秃秃的
`"族长"`，于是「冒险前，族长准备送你一件礼物」也被判成危险任务（该局第 1 题按阶段 5
估值，实测平均掉 0.24 分）。它从日志上完全看不出来 —— 代码自认识别成功、阶段序列也正常。

可靠的指纹是**分布异常**：同一题号上，同阶段的几个事件本该接近均匀出现；
若某个事件出现 0 次、另一个占了绝大多数，就是识别被抢走的信号。

用法：
    python tools/adventure_check_events.py                    # 用 debug/adventure/adventure.log
    python tools/adventure_check_events.py <日志路径>

退出码：0 = 无异常；1 = 发现可疑的分布或关键词问题。
"""
from __future__ import annotations

import json
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agent"))

import adventure_logic as logic  # noqa: E402

LOG = ROOT / "debug" / "adventure" / "adventure.log"
UI = ROOT / "assets" / "data" / "adventure_ui.json"

DEC = re.compile(r"^\[\d\d:\d\d:\d\d\] \[([^\]]+)\] 选第\s*\d\s*项「[^」]*」")
RUN = re.compile(r"^\[\d\d:\d\d:\d\d\] 账本为空")


def scan(path: Path) -> int:
    data = logic.load_data()
    events = data["events"]
    stages = {q["questions"][0]: q for q in []}  # 占位，下面用 _meta 直接推
    qstage: list[int] = []
    for st in data["_meta"]["stages"]:
        for _ in st["questions"]:
            qstage.append(int(st["stage"]))
    pool: dict[int, list[str]] = {}
    for name, body in events.items():
        pool.setdefault(int(body.get("stage", 0)), []).append(name)

    # 每局的第 n 次决策 → 识别出的事件。只保留「整段正好 10 条决策」的局：
    # 「账本为空」在中途开局时也会触发，那种段的第 n 条并不对应第 n 题。
    by_pos: dict[int, Counter] = {}
    seg: list[str] = []
    runs = 0
    skipped = 0

    def flush() -> None:
        nonlocal skipped
        if not seg:
            return
        if len(seg) != len(qstage) or int(events[seg[0]].get("stage", 0)) != 1:
            skipped += 1
            return
        for i, name in enumerate(seg):
            by_pos.setdefault(i + 1, Counter())[name] += 1

    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        s = line.strip()
        if RUN.match(s):
            flush()
            seg = []
            runs += 1
            continue
        m = DEC.match(s)
        if m:
            seg.append(m.group(1))
    flush()

    print(f"日志：{path}")
    print(f"「账本为空」{runs} 次；其中整段 10 条决策且起点属于阶段 1 的算作完整局："
          f"{sum(by_pos[1].values())} 局（跳过 {skipped} 段不完整/中途开局的）\n")
    bad = 0
    print(f"{'题号':<5}{'阶段':<6}{'本阶段题库事件数':<17}识别到的事件分布")
    for q in sorted(by_pos):
        c = by_pos[q]
        st = qstage[q - 1]
        expect = pool.get(st, [])
        top, topn = c.most_common(1)[0]
        flags = []
        if top not in expect:
            flags.append(f"「{top}」不属于阶段 {st}")
        missing = [e for e in expect if not c.get(e)]
        if len(c) >= 2 and len(missing) == len(expect) - 1 and topn >= 5:
            flags.append(f"疑似抢答：只有「{top}」，本阶段其余 {len(missing)} 个事件 0 次")
        if flags:
            bad += 1
        dist = "  ".join(f"{k}×{v}" for k, v in c.most_common())
        print(f"{q:<5}{st:<6}{len(expect):<17}{dist}"
              + ("\n     ! " + "；".join(flags) if flags else ""))

    # 关键词静态检查：只报「过宽 / 跨事件重复」这两类真问题。
    # 「未登记」是设计使然 —— 题干关键词只登记阶段 1 的短名，其余事件靠选项文本兜底
    # （实测区分度 0.77 对 0.06），所以只作统计，不计入退出码。
    ui = json.loads(UI.read_text(encoding="utf-8"))
    print("\n题干关键词检查：")
    kw_bad = 0
    owner: dict[str, list[str]] = {}
    for name, body in ui.get("events", {}).items():
        for pat in body.get("match", []):
            owner.setdefault(pat, []).append(name)
    for name in sorted(ui.get("events", {})):
        for pat in ui["events"][name].get("match", []):
            if len(pat) <= 2:
                print(f"  ! 「{name}」关键词过宽：{pat!r}（{len(pat)} 字，容易抢别的题干）")
                kw_bad += 1
            if len(owner[pat]) > 1:
                print(f"  ! 关键词 {pat!r} 同时挂在 {owner[pat]} 上")
                kw_bad += 1
    total = sum(len(v) for v in pool.values())
    print(f"  题库共 {total} 个事件；题干关键词只登记了 {len(ui.get('events', {}))} 个"
          f"（其余走选项文本兜底），可疑关键词 {kw_bad} 项")
    return 0 if (bad + kw_bad) == 0 else 1


def main() -> int:
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else LOG
    if not path.exists():
        print(f"找不到日志：{path}")
        return 1
    return scan(path)


if __name__ == "__main__":
    raise SystemExit(main())
