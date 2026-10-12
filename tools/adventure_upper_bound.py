# -*- coding: utf-8 -*-
#
# 造梦西游4 · 生存大冒险 —— 精确最优上界
#
"""用记忆化后向归纳，算出「不知道未来题序」前提下的**真正最优**平均分。

意义：上帝视角（已知这一局的事件顺序）能拿约 86.7 分，但那是不可达的。
真正有意义的上界是这里算的：策略只知道各阶段的题库分布，不知道这一局会抽到
哪几个事件。这个上界才决定「脚本还有多少提升空间」—— 目前是 81.33，
而线上策略是 80.23，剩下 1.10 分的空间。

    V(q, attrs) = E_{事件 e ~ 阶段(q) 的题库} [ max_{选项 o} ( 本题得分 + V(q+1, attrs') ) ]

用法：
    py tools/adventure_upper_bound.py
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
print(f"属性截断上限：{dict(zip(ATTRS, CAP))}")


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
    t0 = time.time()
    print(f"初始六维 {dict(zip(ATTRS, INIT))}")
    print("开始后向归纳（记忆化）…\n")

    root = V(0, INIT)
    dt = time.time() - t0

    print(f"★ 「不知道未来题序」前提下的最优平均分 = {root:.3f}")
    print(f"  记忆化状态数 = {len(memo):,}   递归展开节点 = {NODES:,}   耗时 {dt:.1f}s\n")

    print("各深度（第 q 题作答前）被访问到的不同属性组合数：")
    for q in sorted(DEPTH_STATES):
        print(f"  q={q + 1:>2}: {DEPTH_STATES[q]:>10,}")
    print()

    # 用精确最优策略实跑，验证 root 与实际一致
    import random

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
    for i in range(RUNS):
        rng = random.Random(SEED + i)
        seq = [rng.choice(DECK.pools[QS[q]]) for q in range(10)]
        total += sim.play_seq(seq, exact_pick, sim.INIT_ATTRS)
    print(f"实跑 {RUNS} 局验证：平均 {total / RUNS:.3f}（与理论值 {root:.3f} 应当吻合）")


if __name__ == "__main__":
    main()
