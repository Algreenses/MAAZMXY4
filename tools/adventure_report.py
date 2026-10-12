#!/usr/bin/env python3
"""最终收益对照：改造前 / 启发式 / 精确后向归纳（线上默认）。

用 4 个**全新**种子的题序池 × 7 个真实角色做成对比较（同一批题序跑所有策略，
把开局运气差消掉），并单独量精确路径的建表成本。

    python tools/adventure_report.py
"""

from __future__ import annotations

import math
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import simulate_adventure as sim  # noqa: E402
import adventure_tune as W  # noqa: E402

logic = sim.logic

SEEDS = [20270303, 20270404, 20270505, 20270606]
N = 400
PROD = ((3, 4, 5), 0.125, 0.7, 8.0, 2.0)  # 线上常量

# 每个角色「不知道题序」前提下的理论最优（tools/adventure_upper_bound.py 量出）
BOUND = {
    "小沙": 75.503, "小吒": 74.603, "小唐": 83.334, "小猪": 66.202,
    "小玉": 81.302, "小猴": 78.420, "小白": 74.885,
}
# 第 1 题可能抽到的全部事件（阶段 1）
STAGE1 = list(sim.DECK.pools[1])


def stats(vals, base):
    m = sum(vals) / len(vals)
    d = [v - b for v, b in zip(vals, base)]
    dm = sum(d) / len(d)
    var = sum((x - dm) ** 2 for x in d) / (len(d) - 1)
    sd = math.sqrt(var)
    t = dm / (sd / math.sqrt(len(d))) if sd > 0 else 0.0
    return m, dm, t


def build_cost() -> None:
    """每个角色最坏一次建表要多久（线上每局首题实测就是它）。"""
    print("\n精确路径的建表成本（每角色清表一次，再把阶段 1 的每个事件各决策一次，"
          "累计出的就是该角色一次会话最坏的建表量）：")
    print(f"  {'角色':<5} {'耗时秒':>8} {'累计状态':>10}   首题选择")
    worst_t = worst_s = 0.0
    for cname, init in W.CHARS.items():
        logic.reset_exact_memo()
        t0 = time.time()
        first = None
        for ev in STAGE1:
            d = logic.choose(sim.DECK.events, ev, dict(init), question=0)
            if first is None:
                first = d
        dt = time.time() - t0
        st = logic.exact_stats()[0]
        worst_t = max(worst_t, dt)
        worst_s = max(worst_s, st)
        print(f"  {cname:<5} {dt:8.1f} {st:10,d}   "
              f"{first.index + 1}「{first.text}」期望 {first.value:.2f}")
    print(f"  最坏：{worst_t:.1f}s / {worst_s:,} 状态"
          f"（EXACT_MAX_STATES = {logic.EXACT_MAX_STATES:,}，"
          f"EXACT_RESET_THRESHOLD = {logic.EXACT_RESET_THRESHOLD:,}）")


def main() -> None:
    seqs = [s for sd in SEEDS for s in W.make_seqs(N, sd)]
    cases = [(c, s) for c in W.CHARS.values() for s in seqs]
    print(f"题序池：{len(SEEDS)} 池 × {N} 条 = {len(seqs)} 条；"
          f"角色 {len(W.CHARS)} 个；成对样本数 = {len(cases):,}\n")

    def run(pick, per_char_reset=False):
        vals, deaths = [], 0
        for init in W.CHARS.values():
            # 每个角色清一次表：分数与记忆化无关，但不清会让 7 个角色的可达集
            # 叠在一起冲破 EXACT_MAX_STATES，精确策略被静默降级成启发式，
            # 于是「精确」那一列的分数其实是启发式的（曾这样骗过我一轮）。
            if per_char_reset:
                logic.reset_exact_memo()
            for seq in seqs:
                sc, died = W.run_one(seq, init, pick)
                vals.append(float(sc))
                deaths += 1 if died else 0
        return vals, deaths

    leg = [float(W.run_one(s, i, lambda n, a, q: sim.legacy_pick(sim.DECK, n, a, q))[0])
           for i, s in cases]

    # 启发式回退：不传 question，logic 内部只用软边距价值函数
    W.apply_cfg(PROD)
    heur, hd = run(lambda n, a, q: logic.choose(sim.DECK.events, n, a).index)

    exact, ed = run(lambda n, a, q: sim.greedy_pick(sim.DECK, n, a, q), per_char_reset=True)
    print(f"精确表最终状态数 {logic.exact_stats()[0]:,}；"
          f"{'EXACT_ENABLED 仍为 True' if logic.EXACT_ENABLED else '**已降级**：' + str(logic._exact_off_reason)}\n")

    print(f"{'配置':<30} {'均分':>7}  {'配对差':>7}  {'t':>7}   早死")
    print("-" * 70)
    for label, vals, deaths in (
        ("改造前（硬门槛 4+5, blend0.5）", leg, -1),
        ("启发式（软边距 + 生存因子）", heur, hd),
        ("★ 精确后向归纳（线上默认）", exact, ed),
    ):
        m, dm, t = stats(vals, leg)
        dtxt = f"{deaths:4d}" if deaths >= 0 else "   -"
        print(f"{label:<30} {m:7.2f}  {dm:+7.2f}  {t:+7.2f}   {dtxt}")
    bm = sum(BOUND.values()) / len(BOUND)
    print(f"{'理论最优（不知题序，等权）':<30} {bm:7.2f}  {bm - sum(leg) / len(leg):+7.2f}")

    print("\n各角色均分（六维按 健康/战力/智慧/魅力/运气/灵巧）：")
    print(f"  {'角色':<5} {'改造前':>7} {'启发式':>7} {'精确':>7} {'理论最优':>8}  六维")
    n = len(seqs)
    for i, (cname, init) in enumerate(W.CHARS.items()):
        sl = slice(i * n, (i + 1) * n)
        ml = sum(leg[sl]) / n
        mh = sum(heur[sl]) / n
        me = sum(exact[sl]) / n
        print(f"  {cname:<5} {ml:7.2f} {mh:7.2f} {me:7.2f} {BOUND[cname]:8.2f}"
              f"  {'/'.join(str(init[a]) for a in logic.ATTRS)}")
    print(f"  {'等权':<5} {sum(leg) / len(leg):7.2f} {sum(heur) / len(heur):7.2f} "
          f"{sum(exact) / len(exact):7.2f} {bm:8.2f}")

    build_cost()


if __name__ == "__main__":
    main()
