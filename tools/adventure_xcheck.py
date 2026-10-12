#!/usr/bin/env python3
"""交叉核对三方：参考实现 ub 的 V(0, init)、生产逻辑的精确期望、以及实跑均分。

前两者必须**逐位相等** —— 这一条就是「生产实现与参考实现等价」的证明。
实跑是蒙特卡洛，单局分数标准差约 12，几千局内与理论值差零点几个标准误属正常。

    python tools/adventure_xcheck.py [每角色局数]
"""

from __future__ import annotations

import os
import random
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
sys.path.insert(0, _HERE)
sys.path.insert(0, os.path.join(_ROOT, "agent"))

import adventure_logic as logic  # noqa: E402
import adventure_upper_bound as ub  # noqa: E402
import simulate_adventure as sim  # noqa: E402

N = int(sys.argv[1]) if len(sys.argv) > 1 else 3000
STAGE1 = list(sim.DECK.pools[1])
QS = sim.QUESTION_STAGES

print(f"ATTRS = {logic.ATTRS}")
print(f"阶段 1 的事件 {len(STAGE1)} 个：{STAGE1}")
print(f"ub 的截断上限 = {dict(zip(logic.ATTRS, ub.CAP))}")
print(f"生产的截断上限 = {dict(zip(logic.ATTRS, logic._exact_cap))}\n")

print(f"{'角色':<5} {'ub.V(0)':>10} {'生产 DP':>10} {'实跑':>10}   {'实跑-DP':>9}")
print("-" * 62)
for char, d in sim.CHARACTERS.items():
    init_t = tuple(int(d[a]) for a in logic.ATTRS)

    logic.reset_exact_memo()
    tot = 0.0
    for ev in STAGE1:
        tot += logic.choose(sim.DECK.events, ev, dict(d), question=0).value
    v_dp = tot / len(STAGE1)
    v_ub = ub.V(0, init_t)

    rng = random.Random(20261012)
    pick = lambda n, a, q: sim.greedy_pick(sim.DECK, n, a, q)  # noqa: E731
    scores: list[int] = []
    for _ in range(N):
        seq = [rng.choice(sim.DECK.pools[QS[q]]) for q in range(10)]
        scores.append(sim.play_seq(seq, pick, d))
    v_emp = sum(scores) / N
    # 单局分数的方差远大于 3：必须用实测标准差算标准误，否则会误判出「系统偏差」
    var = sum((s - v_emp) ** 2 for s in scores) / (N - 1)
    se = (var / N) ** 0.5
    print(
        f"{char:<5} {v_ub:10.4f} {v_dp:10.4f} {v_emp:10.4f}   "
        f"{v_emp - v_dp:+9.4f}  ±{se:.3f}  SD={var ** 0.5:5.1f}"
    )

print(f"\n每格 {N:,} 局；标准误按实测 SD 计算（见右列），SD≈10 时 SE≈0.18")
