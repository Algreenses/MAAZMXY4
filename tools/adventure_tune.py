#!/usr/bin/env python3
"""在真实初始分布（7 个角色等概率）上重新调参。

之前的调参全部是在**固定小玉一组初值**上做的，而实测 7 个角色的初始六维各不相同
（合计 22~24），小玉是其中魅力最高、灵巧最高的一组之一。真实分布下最优点可能移位。

方法：所有候选配置跑**同一批题序**（成对比较），把开局运气差消掉；
每个角色等权（7 个角色 × N 条题序）。
"""

from __future__ import annotations

import math
import os
import random
import sys
from typing import Callable, Dict, List, Mapping, Sequence, Tuple

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import simulate_adventure as sim  # noqa: E402

logic = sim.logic
QS = sim.QUESTION_STAGES
NQ = len(QS)

# 截图实测量出的 7 个角色初始六维（原样照抄用户给的面板数值）
CHARS: Dict[str, Dict[str, int]] = {
    "小沙": {"健康": 3, "战力": 3, "智慧": 4, "魅力": 3, "运气": 3, "灵巧": 6},
    "小吒": {"健康": 5, "战力": 4, "智慧": 4, "魅力": 4, "运气": 3, "灵巧": 4},
    "小唐": {"健康": 3, "战力": 4, "智慧": 5, "魅力": 5, "运气": 4, "灵巧": 3},
    "小猪": {"健康": 6, "战力": 4, "智慧": 3, "魅力": 3, "运气": 3, "灵巧": 3},
    "小玉": {"健康": 3, "战力": 3, "智慧": 4, "魅力": 5, "运气": 3, "灵巧": 6},
    "小猴": {"健康": 4, "战力": 5, "智慧": 4, "魅力": 3, "运气": 4, "灵巧": 4},
    "小白": {"健康": 4, "战力": 3, "智慧": 4, "魅力": 4, "运气": 5, "灵巧": 4},
}


# --------------------------------------------------------------------------- #
# 题序池
# --------------------------------------------------------------------------- #
def make_seqs(n: int, seed: int) -> List[Sequence[str]]:
    rng = random.Random(seed)
    return [
        [rng.choice(sim.DECK.pools[QS[q]]) for q in range(NQ)] for _ in range(n)
    ]


# --------------------------------------------------------------------------- #
# 可参数化的价值函数（与 agent/adventure_logic.py 的 future_value 逐行等价）
# --------------------------------------------------------------------------- #
def make_fv(
    stages: Sequence[int], scale: float, free_margin: float, health_full: float
) -> Callable[[Mapping[str, int], Mapping[str, Mapping[str, object]]], float]:
    def fv(attrs, events):  # type: ignore[no-untyped-def]
        logic._ensure_value_index(events)
        key = tuple(int(attrs.get(a, 0)) for a in logic.ATTRS)
        got = logic._value_cache.get(key)
        if got is not None:
            return got
        if key[logic.ATTRS.index("健康")] <= 0:
            logic._value_cache[key] = 0.0
            return 0.0
        total = 0.0
        for st in stages:
            entries = logic._stage_entries.get(st) or []
            if not entries:
                continue
            win, lose = logic._stage_scores.get(st, (0, 0))
            acc = 0.0
            for _n, opts in entries:
                best_p = 0.0
                for o in opts:
                    if o.always_fail:
                        continue
                    req = o.require or {}
                    if not req:
                        margin = free_margin
                    else:
                        margin = min(
                            float(attrs.get(k, 0)) - float(v) for k, v in req.items()
                        )
                    p = 1.0 / (1.0 + math.exp(-margin / scale))
                    if p > best_p:
                        best_p = p
                acc += win * best_p + lose * (1.0 - best_p)
            total += acc / len(entries) * logic._stage_questions.get(st, 1)
        if health_full > 0:
            total *= min(1.0, int(attrs.get("健康", 0)) / health_full)
        logic._value_cache[key] = total
        return total

    return fv


# stages, blend, scale, free_margin, health_full
Cfg = Tuple[Tuple[int, ...], float, float, float, float]

BASE: Cfg = ((3, 4, 5), 0.25, 1.0, 8.0, 2.0)


def apply_cfg(cfg: Cfg) -> None:
    stages, blend, scale, free, hfull = cfg
    logic.VALUE_STAGES = stages
    logic.VALUE_BLEND = blend
    logic.VALUE_SCALE = scale
    logic.VALUE_FREE_MARGIN = free
    logic.VALUE_HEALTH_FULL = hfull
    logic.future_value = make_fv(stages, scale, free, hfull)  # type: ignore[assignment]
    logic._value_cache.clear()


# --------------------------------------------------------------------------- #
# 跑一局
# --------------------------------------------------------------------------- #
def run_one(
    seq: Sequence[str],
    init: Mapping[str, int],
    pick: Callable[[str, Mapping[str, int], int], int],
) -> Tuple[int, bool]:
    """返回 (总分, 是否**提前**结束)。

    只有「答不完 10 题」才算提前结束：在第 10 题倒下时 10 题的分都已经拿到，
    不损失任何分数，因此不计入。
    """
    attrs = dict(init)
    total = 0
    for q, name in enumerate(seq):
        opt = logic.get_options(sim.DECK.events[name])[pick(name, attrs, q)]
        ok = logic.can_succeed(opt, attrs)
        total += sim.STAGE_SCORE[QS[q]][0 if ok else 1]
        attrs = sim.apply_delta(attrs, opt.success if ok else opt.fail)
        if attrs.get("健康", 0) <= 0:
            return total, q < NQ - 1
    return total, False


def evaluate(cfg: Cfg, seqs: Sequence[Sequence[str]]) -> Tuple[List[float], int]:
    """返回 (逐 (角色, 题序) 的得分, 早死局数)。"""
    apply_cfg(cfg)
    pick = lambda name, attrs, q: sim.greedy_pick(sim.DECK, name, attrs, q)  # noqa: E731
    vals: List[float] = []
    deaths = 0
    for init in CHARS.values():
        for seq in seqs:
            sc, died = run_one(seq, init, pick)
            vals.append(float(sc))
            deaths += 1 if died else 0
    return vals, deaths


def legacy_scores(seqs: Sequence[Sequence[str]]) -> List[float]:
    pick = lambda name, attrs, q: sim.legacy_pick(sim.DECK, name, attrs, q)  # noqa: E731
    return [float(run_one(seq, init, pick)[0]) for init in CHARS.values() for seq in seqs]


def report(name: str, vals: Sequence[float], base: Sequence[float], deaths: int) -> None:
    m = sum(vals) / len(vals)
    bm = sum(base) / len(base)
    d = [v - b for v, b in zip(vals, base)]
    dm = sum(d) / len(d)
    var = sum((x - dm) ** 2 for x in d) / (len(d) - 1)
    sd = math.sqrt(var)
    t = dm / (sd / math.sqrt(len(d))) if sd > 0 else 0.0
    print(f"{name:<34} {m:7.3f}  {dm:+7.3f}  t={t:+6.2f}  早死 {deaths:4d}")


def main() -> None:
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 400
    seeds = [int(x) for x in (sys.argv[2:] or ["20261012", "20261111"])]
    seqs = [s for sd in seeds for s in make_seqs(n, sd)]
    print(f"题序池：{len(seeds)} 个种子 × {n} 条 = {len(seqs)} 条；角色 {len(CHARS)} 个")
    print(f"成对样本数 = {len(seqs) * len(CHARS)}\n")

    base, base_deaths = evaluate(BASE, seqs)
    print(f"{'配置':<34} {'均分':>7}  {'配对差':>7}  {'t':>8}  早死")
    print("-" * 72)
    report("★ 现状 (3,4,5) blend0.25 scale1.0", base, base, base_deaths)

    leg = legacy_scores(seqs)
    report("改造前（硬门槛 4+5, blend0.5）", leg, base, -1)

    tests: List[Tuple[str, Cfg]] = []
    for bl in (0.10, 0.15, 0.20, 0.30, 0.35, 0.40, 0.50):
        tests.append((f"blend={bl}", ((3, 4, 5), bl, 1.0, 8.0, 2.0)))
    for sc in (0.6, 0.8, 0.9, 1.1, 1.25, 1.5):
        tests.append((f"scale={sc}", ((3, 4, 5), 0.25, sc, 8.0, 2.0)))
    for st in ((4, 5), (2, 3, 4, 5), (1, 2, 3, 4, 5), (3, 4), (3, 5)):
        tests.append((f"stages={st}", (st, 0.25, 1.0, 8.0, 2.0)))
    for hf in (1.0, 1.5, 2.5, 3.0, 0.0):
        tests.append((f"health_full={hf}", ((3, 4, 5), 0.25, 1.0, 8.0, hf)))
    for fm in (4.0, 6.0, 12.0):
        tests.append((f"free_margin={fm}", ((3, 4, 5), 0.25, 1.0, fm, 2.0)))

    for name, cfg in tests:
        vals, deaths = evaluate(cfg, seqs)
        report(name, vals, base, deaths)

    # 每个角色的现状均分：看难度差多少
    print("\n各角色现状均分（现状配置）：")
    apply_cfg(BASE)
    pick = lambda name, attrs, q: sim.greedy_pick(sim.DECK, name, attrs, q)  # noqa: E731
    for cname, init in CHARS.items():
        sc = [run_one(seq, init, pick)[0] for seq in seqs]
        d = [run_one(seq, init, pick)[1] for seq in seqs]
        print(
            f"  {cname}  {sum(sc) / len(sc):6.2f}   "
            f"（{'/'.join(str(init[a]) for a in logic.ATTRS)}）  早死 {sum(d)}"
        )


if __name__ == "__main__":
    main()
