"""用「截图实测的真实初始六维」重放 adventure.log 的每一局，检验模拟器判定能否复现。

动机：之前用固定的小玉初值重放，复现率只有 78.2%，当时误判为「模拟器失真」。
现在从 debug/screencap 的「角色」面板读出了每局的真实初始六维，
按时间把 adventure.log 的 15 局与截图局一一对齐后重放。
"""
from __future__ import annotations

import re
import sys
from typing import Dict, List, Mapping, Sequence, Tuple

import simulate_adventure as sim

logic = sim.logic

LOG = "debug/adventure/adventure.log"

# adventure.log 的局号（1 起） -> 截图实测初始六维
# 对齐依据：每个 log 局的首条决策时间距对应截图首帧 2~5 秒
TRUE_INIT: Dict[int, Tuple[str, Dict[str, int]]] = {
    7: ("未知A", {"健康": 7, "战力": 8, "智慧": 4, "魅力": 4, "运气": 3, "灵巧": 2}),
    8: ("小猪", {"健康": 6, "战力": 4, "智慧": 3, "魅力": 3, "运气": 3, "灵巧": 3}),
    9: ("小沙", {"健康": 3, "战力": 3, "智慧": 4, "魅力": 3, "运气": 3, "灵巧": 6}),
    10: ("小沙", {"健康": 3, "战力": 3, "智慧": 4, "魅力": 3, "运气": 3, "灵巧": 6}),
    11: ("小唐", {"健康": 3, "战力": 4, "智慧": 5, "魅力": 5, "运气": 4, "灵巧": 3}),
    12: ("小唐", {"健康": 3, "战力": 4, "智慧": 5, "魅力": 5, "运气": 4, "灵巧": 3}),
    13: ("小沙", {"健康": 3, "战力": 3, "智慧": 4, "魅力": 3, "运气": 3, "灵巧": 6}),
    14: ("小沙", {"健康": 3, "战力": 3, "智慧": 4, "魅力": 3, "运气": 3, "灵巧": 6}),
    15: ("小沙", {"健康": 3, "战力": 3, "智慧": 4, "魅力": 3, "运气": 3, "灵巧": 6}),
    16: ("小沙", {"健康": 3, "战力": 3, "智慧": 4, "魅力": 3, "运气": 3, "灵巧": 6}),
    17: ("小猪", {"健康": 6, "战力": 4, "智慧": 3, "魅力": 3, "运气": 3, "灵巧": 3}),
    18: ("小吒", {"健康": 5, "战力": 4, "智慧": 4, "魅力": 4, "运气": 3, "灵巧": 4}),
    19: ("小沙", {"健康": 3, "战力": 3, "智慧": 4, "魅力": 3, "运气": 3, "灵巧": 6}),
    21: ("小玉", {"健康": 3, "战力": 3, "智慧": 4, "魅力": 5, "运气": 3, "灵巧": 6}),
    22: ("小吒", {"健康": 5, "战力": 4, "智慧": 4, "魅力": 4, "运气": 3, "灵巧": 4}),
    23: ("小吒", {"健康": 5, "战力": 4, "智慧": 4, "魅力": 4, "运气": 3, "灵巧": 4}),
    24: ("小猪", {"健康": 6, "战力": 4, "智慧": 3, "魅力": 3, "运气": 3, "灵巧": 3}),
}

TS = re.compile(r"^\[(\d\d:\d\d:\d\d)\]")
DEC = re.compile(
    r"^\[(\d\d:\d\d:\d\d)\] \[([^\]]+)\] 选第\s*(\d)\s*项「([^」]*)」（(必定成功|必然失败)"
)
A = ("战力", "智慧", "魅力", "灵巧", "运气", "健康")


def parse_runs() -> List[Tuple[str, str, List[Tuple[str, int, str, str]]]]:
    """返回 [(首条时刻, 末条时刻, 决策列表)]，以「账本为空」分段。"""
    runs: List[Tuple[str, str, List[Tuple[str, int, str, str]]]] = []
    cur: List[Tuple[str, int, str, str]] | None = None
    first = last = ""
    for ln in open(LOG, encoding="utf-8", errors="replace"):
        ts = TS.match(ln)
        if "账本为空" in ln:
            if cur is not None:
                runs.append((first, last, cur))
            cur, first, last = [], (ts.group(1) if ts else ""), ""
            continue
        m = DEC.match(ln)
        if m and cur is not None:
            last = ts.group(1) if ts else last
            cur.append((m.group(2), int(m.group(3)), m.group(4), m.group(5)))
    if cur is not None:
        runs.append((first, last, cur))
    return runs


def secs(hms: str) -> int:
    h, m, s = (int(x) for x in hms.split(":"))
    return h * 3600 + m * 60 + s


def judge(option: Mapping[str, object], attrs: Mapping[str, int]) -> str:
    """按模拟器的判定规则给出 必定成功/必然失败/不确定。"""
    req = option.get("require")
    if not req:
        if option.get("always_fail"):
            return "必然失败"
        return "必定成功" if option.get("success") else "必定失败?"
    req = req  # type: ignore[assignment]
    ok = all(attrs.get(k, 0) >= v for k, v in req.items())  # type: ignore[union-attr]
    return "必定成功" if ok else "必然失败"


def main() -> None:
    events = sim.DECK.events
    runs = parse_runs()
    print(f"adventure.log 共 {len(runs)} 局\n")
    tot = hit = 0
    prev_end: str | None = None
    prev_name = ""
    prev_attrs: Dict[str, int] | None = None
    for no, (start, end, decs) in enumerate(runs, 1):
        if no in TRUE_INIT:
            name, init = TRUE_INIT[no]
        elif (
            prev_attrs is not None
            and prev_end
            and 0 <= secs(start) - secs(prev_end) < 60
        ):
            # 上一局被「未知事件」提前切断，本段是同一局的后半
            name, init = f"{prev_name}·续", dict(prev_attrs)
        else:
            prev_end, prev_name, prev_attrs = end, "", None
            continue
        attrs = dict(init)
        good = 0
        rows = []
        for ev, idx, text, said in decs:
            opts = logic.get_options(events[ev]) if ev in events else None
            if opts is None:
                rows.append(f"    ?  {ev} 第{idx}项「{text}」题库无此题")
                tot += 1
                continue
            o = opts[idx - 1] if 1 <= idx <= len(opts) else None
            if o is None:
                rows.append(f"    ?  {ev} 第{idx}项「{text}」越界")
                tot += 1
                continue
            mine = "必定成功" if logic.can_succeed(o, attrs) else "必然失败"
            tot += 1
            if mine == said:
                hit += 1
                good += 1
            else:
                rows.append(
                    f"    ✗  {ev} 第{idx}项「{text}」 日志={said} 重放={mine}  当时六维={attrs}"
                )
            attrs = sim.apply_delta(attrs, sim._delta(o, attrs))  # noqa: SLF001
        tag = "" if no in TRUE_INIT else "  (续上一局)"
        print(
            f"  第{no:>2}局 {start} {name} {[init[a] for a in A]}  "
            f"决策{len(decs):>2} 复现{good}/{len(decs)}{tag}"
        )
        for r in rows:
            print(r)
        prev_end, prev_name, prev_attrs = end, name, dict(attrs)
    print(f"\n合计复现率 {hit}/{tot} = {hit / tot * 100:.1f}%")


if __name__ == "__main__":
    sys.exit(main())
