# -*- coding: utf-8 -*-
#
# 造梦西游4 · 生存大冒险 —— 精确最优上界
#
"""用记忆化后向归纳，算出「不知道未来题序」前提下的**真正最优**平均分。

意义：上帝视角（已知这一局的事件顺序）能拿约 87 分，但那是不可达的。
真正有意义的上界是这里算的：策略只知道各阶段的题库分布，不知道这一局会抽到
哪几个事件。这个上界才决定「脚本还有多少提升空间」——线上策略已经就是
这个最优策略本身（agent/adventure_logic.py 的精确后向归纳），所以真正剩下的
空间只有它自己「不知道题序」与「上帝视角」之间的差。

    V(q, attrs) = E_{事件 e ~ 阶段(q) 的题库} [ max_{选项 o} ( 本题得分 + V(q+1, attrs') ) ]

初始六维不是一组而是 7 组（游戏里 7 个角色各一组，见
tools/simulate_adventure.py 的 CHARACTERS），角色之间差距极大：
小猪最弱 66.2、小唐最强 83.3。所以默认把 7 个角色全算一遍。

用法：
    py tools/adventure_upper_bound.py [角色名 ...]     # 不给就全算
"""

from __future__ import annotations

import os
import sys
import time
from typing import Any, Dict, List, Mapping, Sequence, Tuple

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
sys.path.insert(0, _HERE)
sys.path.insert(0, os.path.join(_ROOT, "agent"))

import adventure_logic as logic  # noqa: E402
import simulate_adventure as sim  # noqa: E402

DECK = sim.DECK
QS = sim.QUESTION_STAGES
SS = sim.STAGE_SCORE
OPTIONS: Dict[str, list] = {n: logic.get_options(b) for n, b in DECK.events.items()}
ATTRS = logic.ATTRS
H_IDX = ATTRS.index("健康")

INIT = tuple(int(sim.INIT_ATTRS[a]) for a in ATTRS)

IDX = {a: i for i, a in enumerate(ATTRS)}

# 属性值超过「该属性在题库里的最高门槛」之后，再高也不改变任何判定，
# 因此可以安全截断 —— 这是把状态数压下来的关键。健康不截断（其负增量会累积）。
CAP: List[int] = []
for _a in ATTRS:
    _mx = 0
    for _opts in OPTIONS.values():
        for _o in _opts:
            _v = (_o.require or {}).get(_a)
            if _v:
                _mx = max(_mx, int(_v))
    CAP.append(_mx if _a != "健康" else 99)

# 注意：这里不能在模块层 print —— adventure_xcheck.py 会 import 本模块，
# 否则每次导入都会往标准输出塞一行。需要打印请放 main()。


def can_ok(o, t: Tuple[int, ...]) -> bool:
    """tuple 版门槛判定（logic.can_succeed 只接受 Mapping，这里避开建 dict 的开销）。"""
    if getattr(o, "always_fail", False):
        return False
    req = o.require
    if not req:
        return True
    for k, v in req.items():
        if t[IDX[k]] < int(v):
            return False
    return True

memo: Dict[Tuple[int, Tuple[int, ...]], float] = {}
DEPTH_STATES: Dict[int, int] = {}
NODES = 0


def add(attrs: Tuple[int, ...], delta: Mapping[str, int]) -> Tuple[int, ...]:
    if not delta:
        return attrs
    out = list(attrs)
    for i, a in enumerate(ATTRS):
        v = delta.get(a)
        if v:
            out[i] = min(out[i] + int(v), CAP[i])
    return tuple(out)


def V(q: int, attrs: Tuple[int, ...]) -> float:
    """从第 q 题（0-based）开始、属性为 attrs 时的最优期望剩余得分。"""
    global NODES
    if q >= 10 or attrs[H_IDX] <= 0:
        return 0.0
    key = (q, attrs)
    got = memo.get(key)
    if got is not None:
        return got
    NODES += 1
    stage = QS[q]
    gain, lose = SS[stage]
    pool = DECK.pools[stage]
    total = 0.0
    for name in pool:
        best = -1e18
        for o in OPTIONS[name]:
            ok = can_ok(o, attrs)
            nxt = add(attrs, o.success if ok else o.fail)
            val = (gain if ok else lose) + V(q + 1, nxt)
            if val > best:
                best = val
        total += best
    res = total / len(pool)
    memo[key] = res
    DEPTH_STATES[q] = DEPTH_STATES.get(q, 0) + 1
    return res


def greedy_over_optimal(q: int, attrs: Tuple[int, ...]) -> int:
    """按精确 V 取最优选项（用于实际对局）。"""
    stage = QS[q]
    gain, lose = SS[stage]
    best_idx, best_val = 0, -1e18
    for o in OPTIONS[greedy_over_optimal.current]:
        ok = can_ok(o, attrs)
        nxt = add(attrs, o.success if ok else o.fail)
        val = (gain if ok else lose) + V(q + 1, nxt)
        if val > best_val:
            best_val, best_idx = val, o.index
    return best_idx


def main() -> None:
    names = sys.argv[1:] or list(sim.CHARACTERS)
    unknown = [n for n in names if n not in sim.CHARACTERS]
    if unknown:
        raise SystemExit(f"未知角色：{unknown}；可选 {list(sim.CHARACTERS)}")

    t0 = time.time()
    print(f"属性截断上限：{dict(zip(ATTRS, CAP))}")
    print(f"角色 {len(names)} 个；共享记忆化表（默认角色小玉 {dict(zip(ATTRS, INIT))}）\n")
    print(f"{'角色':<5} {'最优均分':>9} {'状态数':>12}   六维（"
          + "/".join(ATTRS) + "）")
    print("-" * 78)

    bounds: Dict[str, float] = {}
    for name in names:
        init = tuple(int(sim.CHARACTERS[name][a]) for a in ATTRS)
        before = len(memo)
        root = V(0, init)
        bounds[name] = root
        print(f"{name:<5} {root:9.3f} {len(memo) - before:12,}   "
              + "/".join(str(v) for v in init))
    dt = time.time() - t0
    mean = sum(bounds.values()) / len(bounds)
    print("-" * 78)
    print(f"{'等权':<5} {mean:9.3f} {len(memo):12,}   （7 个角色等权平均）")
    print(f"\n记忆化状态数合计 = {len(memo):,}   递归展开节点 = {NODES:,}   耗时 {dt:.1f}s\n")

    # 用精确最优策略实跑，验证 root 与实际一致（只验第一个角色就够）
    import random

    first = names[0]
    init_d = sim.CHARACTERS[first]
    RUNS = int(os.environ.get("RUNS", "4000"))
    SEED = 20261012

    def exact_pick(name: str, attrs: Mapping[str, int], q: int) -> int:
        tup = tuple(int(attrs[a]) for a in ATTRS)
        stage = QS[q]
        gain, lose = SS[stage]
        best_idx, best_val = 0, -1e18
        for o in OPTIONS[name]:
            ok = can_ok(o, tup)
            nxt = add(tup, o.success if ok else o.fail)
            val = (gain if ok else lose) + V(q + 1, nxt)
            if val > best_val:
                best_val, best_idx = val, o.index
        return best_idx

    total = 0.0
    scores: list[int] = []
    for i in range(RUNS):
        rng = random.Random(SEED + i)
        seq = [rng.choice(DECK.pools[QS[q]]) for q in range(10)]
        s = sim.play_seq(seq, exact_pick, init_d)
        scores.append(s)
        total += s
    emp = total / RUNS
    var = sum((s - emp) ** 2 for s in scores) / (RUNS - 1)
    se = (var / RUNS) ** 0.5
    print(f"实跑 {RUNS} 局验证（{first}）：平均 {emp:.3f} ±{se:.3f}"
          f"（单局标准差 {var ** 0.5:.1f}；与理论值 {bounds[first]:.3f} 之差 "
          f"{emp - bounds[first]:+.3f}）")
    print("注：单局分数方差很大，几万局内二者相差零点几个标准差以内的波动属正常，"
          "不代表实现有分歧。\n     证明实现等价请用 tools/adventure_xcheck.py："
          "逐个角色比较参考值与生产逻辑的期望。")


if __name__ == "__main__":
    main()
