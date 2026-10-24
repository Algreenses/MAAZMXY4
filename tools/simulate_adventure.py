# -*- coding: utf-8 -*-
#
# 造梦西游4 · 生存大冒险 —— 策略模拟器
# 作者：InkyuZero   (https://github.com/Inkyu-Zero/MAAZMXY4)
#
"""离线模拟「生存大冒险」一局 10 题，用来量化策略得分。

计分规则（推导自 assets/data/adventure.json 的 _meta.stages，
并用 debug/adventure/adventure.log 里真实结算面板的分数反验证过）：

    题号 Q1   -> 阶段1  成功 6 / 失败 3
    题号 Q2,Q3-> 阶段2  成功 6 / 失败 3
    题号 Q4-6 -> 阶段3  成功 9 / 失败 4
    题号 Q7-9 -> 阶段4  成功 12 / 失败 6
    题号 Q10  -> 阶段5  成功 19 / 失败 9
    满分 100，全失败 48。

验证：日志中一局（01:56:08 开局）依次答
    6,6,6,9,4,9,6,6,6,9 = 67
与结算面板 OCR 到的「生存大冒险中获得67积分」一致。

分数现状（4 个全新题序池 × 400 条 × 7 个真实角色 = 11,200 对，成对比较，
见 tools/adventure_report.py）：
    改造前的线上贪心（阈值分档 + 硬门槛价值函数）  73.21
    启发式「贪心 + 软边距价值函数 + 生存因子」      75.00    (+1.79)
    ★ 现采用的「精确后向归纳」                    75.88    (+2.67)
    不知题序时的理论最优（7 角色等权）            76.32    （剩下 0.44）
    上帝视角：预知这一局的事件顺序再最优            约 87      （不可达，仅供参考）

「精确后向归纳」就是把上面那个 DP 直接搬进线上：
    V(q, attrs) = E_{事件 ~ 阶段题库}[ max_o (本题得分 + V(q+1, attrs + delta)) ]
    第 1 题当场建表（75~163 万状态，74~80 秒；落盘缓存后 0.8 秒载入），之后每题命中缓存；
    表跨局保留，超过 40 万条才清空（见 agent/adventure_logic.py 的
    EXACT_RESET_THRESHOLD）。所以线上已经就是理论最优策略，「不知题序」与
    「上帝视角」之间那 11 分是信息差，抢不到。

初始六维不是一组而是 7 组（游戏每局随机给一个角色），角色之间差距极大：
小猪最弱 66.20、小唐最强 83.33（见 tools/adventure_upper_bound.py）。

用法：
    py tools/simulate_adventure.py                  # 跑对比
    py tools/simulate_adventure.py --runs 2000      # 加大样本（每角色）
    py tools/simulate_adventure.py --stats          # 只看题库门槛统计
"""

from __future__ import annotations

import argparse
import json
import os
import random
import statistics
import sys
from collections import Counter, defaultdict
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
sys.path.insert(0, os.path.join(_ROOT, "agent"))

import adventure_logic as logic  # noqa: E402

# --------------------------------------------------------------------------- #
# 一局的固定结构
# --------------------------------------------------------------------------- #

# 题号 -> 阶段
QUESTION_STAGES: Sequence[int] = (1, 2, 2, 3, 3, 3, 4, 4, 4, 5)

# 阶段 -> (成功分, 失败分)
STAGE_SCORE: Dict[int, Tuple[int, int]] = {
    1: (6, 3),
    2: (6, 3),
    3: (9, 4),
    4: (12, 6),
    5: (19, 9),
}

# 真实初始分布：游戏每局随机给一个角色，共 7 个。
# 数值由 debug/screencap 的「生存属性」面板截图逐格读出，并用
# debug/adventure/adventure.log 的 24 局决策反推验证过（21 局能被唯一角色复现，
# 且用真实初值重放 98% 的「必定成功/必然失败」判定都对得上，见
# tools/adventure_check_chars.py）。
# 每个角色的理论上界见 tools/adventure_upper_bound.py。
INIT_ATTRS: Dict[str, int] = {
    "健康": 3,
    "战力": 3,
    "智慧": 4,
    "魅力": 5,
    "运气": 3,
    "灵巧": 6,
}

CHARACTERS: Dict[str, Dict[str, int]] = {
    "小沙": {"健康": 3, "战力": 3, "智慧": 4, "魅力": 3, "运气": 3, "灵巧": 6},
    "小吒": {"健康": 5, "战力": 4, "智慧": 4, "魅力": 4, "运气": 3, "灵巧": 4},
    "小唐": {"健康": 3, "战力": 4, "智慧": 5, "魅力": 5, "运气": 4, "灵巧": 3},
    "小猪": {"健康": 6, "战力": 4, "智慧": 3, "魅力": 3, "运气": 3, "灵巧": 3},
    "小玉": {"健康": 3, "战力": 3, "智慧": 4, "魅力": 5, "运气": 3, "灵巧": 6},
    "小猴": {"健康": 4, "战力": 5, "智慧": 4, "魅力": 3, "运气": 4, "灵巧": 4},
    "小白": {"健康": 4, "战力": 3, "智慧": 4, "魅力": 4, "运气": 5, "灵巧": 4},
}

assert CHARACTERS["小玉"] == INIT_ATTRS, "小玉必须与 agent/adventure.py 的 DEFAULT_ATTRS 一致"

ATTRS = logic.ATTRS


# --------------------------------------------------------------------------- #
# 题库
# --------------------------------------------------------------------------- #
class Deck:
    def __init__(self) -> None:
        self.events: Dict[str, Any] = logic.load_events()
        self.pools: Dict[int, List[str]] = defaultdict(list)
        for name, body in self.events.items():
            self.pools[int(body["stage"])].append(name)
        for s in self.pools:
            self.pools[s].sort()

    def stage_of_question(self, q: int) -> int:
        return QUESTION_STAGES[q]

    def summary(self) -> None:
        print("=== 题库门槛统计 ===")
        for s in sorted(self.pools):
            print(f"  阶段{s}: {len(self.pools[s])} 个事件")
        print()

        req = defaultdict(list)
        for name, body in self.events.items():
            for o in body["options"]:
                for k, v in (o.get("require") or {}).items():
                    req[k].append(int(v))
        print("各属性被要求门槛（次数 / 最高 / 中位）:")
        for k in ATTRS:
            xs = req.get(k, [])
            if xs:
                print(
                    f"  {k}: {len(xs)} 处，最高 {max(xs)}，中位 {statistics.median(xs):.0f}"
                )
            else:
                print(f"  {k}: 无门槛")
        print()

        # 每个阶段的门槛分布：能否靠初始属性直接过
        print("各阶段「初始六维直接可达」的选项占比:")
        for s in sorted(self.pools):
            tot = hit = 0
            for name in self.pools[s]:
                for o in self.events[name]["options"]:
                    tot += 1
                    if logic.can_succeed(logic.get_options(self.events[name])[0], INIT_ATTRS):
                        pass
            # 重新按选项统计
            tot = hit = 0
            for name in self.pools[s]:
                for o in logic.get_options(self.events[name]):
                    tot += 1
                    if logic.can_succeed(o, INIT_ATTRS):
                        hit += 1
            print(f"  阶段{s}: {hit}/{tot} = {hit/tot:.1%}")
        print()

        # 有哪个事件是「初始属性下 4 个选项全不可达」
        print("初始属性下 4 选项全不可达的事件数（按阶段）:")
        for s in sorted(self.pools):
            bad = 0
            for name in self.pools[s]:
                opts = logic.get_options(self.events[name])
                if not any(logic.can_succeed(o, INIT_ATTRS) for o in opts):
                    bad += 1
            print(f"  阶段{s}: {bad}/{len(self.pools[s])}")
        print()

        # 成功分支里会扣健康的选项
        print("成功分支会扣健康 的选项（可能导致早死）:")
        n = 0
        for name, body in self.events.items():
            for o in body["options"]:
                succ = o.get("success") or {}
                if succ.get("健康", 0) < 0:
                    n += 1
                    print(f"  [{name}] {o['text']}: {succ}")
        print(f"  共 {n} 个")
        print()

        # 健康门槛
        print("含健康门槛的选项:")
        for name, body in self.events.items():
            for o in body["options"]:
                r = o.get("require") or {}
                if "健康" in r:
                    print(f"  [{name}] {o['text']}: 需 {r}")
        print()

        def req_str(r: Optional[Mapping[str, int]]) -> str:
            if not r:
                return "无要求"
            return ",".join(f"{k}{v}" for k, v in r.items())

        print("阶段4 / 阶段5 各事件选项门槛（后 4 题 55 分能不能拿到，全看这里）:")
        for s in (4, 5):
            print(f"  ── 阶段{s}（{len(self.pools[s])} 事件）──")
            for name in self.pools[s]:
                opts = logic.get_options(self.events[name])
                parts = [f"{o.index + 1}.{o.text}[{req_str(o.require)}]" for o in opts]
                easy = min(
                    (len(o.require or {}) for o in opts),
                    default=0,
                )
                print(f"    [{name}] " + " ".join(parts))
        print()

        # 后 4 题里「最容易达成」的那条门槛组合：需要哪几项属性到多少
        print("阶段4/5 每题的最低门槛（取 4 选项里门槛最松的一项，按属性数排序）:")
        need = Counter()
        for s in (4, 5):
            for name in self.pools[s]:
                opts = logic.get_options(self.events[name])
                def hardness(o: logic.Option) -> Tuple[int, int]:
                    r = o.require or {}
                    return (len(r), max(r.values()) if r else 0)
                o = min(opts, key=hardness)
                print(f"    [{name}] {o.text}: {req_str(o.require)}")
                for k, v in (o.require or {}).items():
                    need[k] = max(need.get(k, 0), int(v))
        print(f"    → 想全拿下后 4 题，需要至少：{dict(need)}")
        print()


# --------------------------------------------------------------------------- #
# 一局模拟
# --------------------------------------------------------------------------- #
def apply_delta(attrs: Dict[str, int], delta: Mapping[str, int]) -> Dict[str, int]:
    out = dict(attrs)
    for k, v in delta.items():
        out[k] = out.get(k, 0) + int(v)
    return out


def greedy_pick(deck: Deck, name: str, attrs: Mapping[str, int], q: int = 0) -> int:
    """当前线上策略：精确后向归纳（题号交给逻辑层定位剩余阶段）。

    `logic.EXACT_ENABLED` 为假或题库元信息残缺时，logic 内部会自动退回启发式，
    所以这里不需要分支 —— 模拟器跑的永远是线上那条真实路径。
    """
    return logic.choose(deck.events, name, attrs, question=q).index


# 改造前的参数（健康目标 6、排序键只看阈值评分、没有价值函数），仅用于对照实验
_LEGACY_HEALTH_TARGET = 6


def legacy_pick(deck: Deck, name: str, attrs: Mapping[str, int], q: int = 0) -> int:
    """改造前的策略。不参与线上运行，只用来量出这次改动的真实收益。"""
    saved = logic.THRESHOLD_TARGETS["健康"]
    logic.THRESHOLD_TARGETS["健康"] = _LEGACY_HEALTH_TARGET
    try:
        scored = []
        for o in logic.get_options(deck.events[name]):
            ok = logic.can_succeed(o, attrs)
            sc = logic._gain_score(_delta(o, attrs), attrs, logic.DEFAULT_WEIGHTS)
            if ok:
                sc += logic.DEFAULT_SUCCESS_BONUS
            scored.append((o, ok, sc))
        return max(scored, key=lambda x: (x[1], x[2]))[0].index
    finally:
        logic.THRESHOLD_TARGETS["健康"] = saved


def safe_pick(deck: Deck, name: str, attrs: Mapping[str, int], q: int) -> int:
    """安全策略：能成功优先，但拒绝「这一步成功就会让健康归零」的选项。

    早死会丢掉剩下的全部题目分，比这一题的成败重要得多。
    """
    opts = logic.get_options(deck.events[name])
    alive = [o for o in opts if attrs.get("健康", 0) + _delta(o, attrs).get("健康", 0) > 0]
    pool = alive or opts
    scored = []
    for o in pool:
        ok = logic.can_succeed(o, attrs)
        sc = logic._gain_score(_delta(o, attrs), attrs, logic.DEFAULT_WEIGHTS)
        if ok:
            sc += logic.DEFAULT_SUCCESS_BONUS
        scored.append((o, ok, sc))
    return max(scored, key=lambda x: (x[1], x[2]))[0].index


def _delta(o: logic.Option, attrs: Mapping[str, int]) -> Dict[str, int]:
    return dict(o.success if logic.can_succeed(o, attrs) else o.fail)


def play(
    deck: Deck,
    rng: random.Random,
    policy: Callable[[Deck, str, Mapping[str, int], int], int],
    init: Mapping[str, int],
    trace: Optional[list] = None,
) -> int:
    """跑完一局，返回总分。健康归零则立即结束（本题分仍计入）。"""
    attrs = dict(init)
    total = 0
    for q in range(10):
        stage = deck.stage_of_question(q)
        name = rng.choice(deck.pools[stage])
        idx = policy(deck, name, attrs, q)
        opt = logic.get_options(deck.events[name])[idx]
        ok = logic.can_succeed(opt, attrs)
        delta = dict(opt.success if ok else opt.fail)
        gain, lose = STAGE_SCORE[stage]
        total += gain if ok else lose
        attrs = apply_delta(attrs, delta)
        if trace is not None:
            trace.append((q + 1, stage, name, opt.text, ok, delta, total, dict(attrs)))
        if attrs.get("健康", 0) <= 0:
            break
    return total


# 模块级题库实例：让 play_seq 等函数不必每次重新读 JSON
DECK = Deck()


def play_seq(
    seq: Sequence[str],
    policy: Callable[[str, Mapping[str, int], int], int],
    init: Mapping[str, int],
) -> int:
    """在**给定**的事件序列上跑一局，返回总分。

    与 play() 的区别：事件序列由调用方提供，因此多个策略可以跑同一批序列，
    做「成对比较」，把策略差和开局运气差彻底分开。
    """
    attrs = dict(init)
    total = 0
    for q, name in enumerate(seq):
        stage = QUESTION_STAGES[q]
        opt = logic.get_options(DECK.events[name])[policy(name, attrs, q)]
        ok = logic.can_succeed(opt, attrs)
        total += STAGE_SCORE[stage][0 if ok else 1]
        attrs = apply_delta(attrs, opt.success if ok else opt.fail)
        if attrs.get("健康", 0) <= 0:
            break
    return total


def lookahead_pick(
    deck: Deck,
    name: str,
    attrs: Mapping[str, int],
    q: int,
    rollouts: int,
    rng: random.Random,
    base_policy: Callable[[Deck, str, Mapping[str, int], int], int],
) -> int:
    """单步前瞻：对每个选项做 K 次随机后续 rollout，取期望总分最高者。"""
    stage = deck.stage_of_question(q)
    gain, lose = STAGE_SCORE[stage]
    opts = logic.get_options(deck.events[name])
    best_idx, best_val = opts[0].index, -1e18
    for o in opts:
        ok = logic.can_succeed(o, attrs)
        imm = gain if ok else lose
        nxt = apply_delta(attrs, o.success if ok else o.fail)
        if nxt.get("健康", 0) <= 0:
            val = float(imm)
        elif q == 9:
            val = float(imm)
        else:
            tot = 0
            for _ in range(rollouts):
                tot += _rollout(deck, rng, nxt, q + 1, base_policy)
            val = imm + tot / rollouts
        if val > best_val:
            best_val, best_idx = val, o.index
    return best_idx


def _rollout(
    deck: Deck,
    rng: random.Random,
    attrs: Mapping[str, int],
    q: int,
    policy: Callable[[Deck, str, Mapping[str, int], int], int],
) -> int:
    total = 0
    cur = dict(attrs)
    for i in range(q, 10):
        stage = deck.stage_of_question(i)
        name = rng.choice(deck.pools[stage])
        opt = logic.get_options(deck.events[name])[policy(deck, name, cur, i)]
        ok = logic.can_succeed(opt, cur)
        total += STAGE_SCORE[stage][0 if ok else 1]
        cur = apply_delta(cur, opt.success if ok else opt.fail)
        if cur.get("健康", 0) <= 0:
            break
    return total


# --------------------------------------------------------------------------- #
# 上帝视角：已知本题序时的最优解（上界，用来判断"还有多少分可抢"）
# --------------------------------------------------------------------------- #
def oracle_score(deck: Deck, rng: random.Random, init: Mapping[str, int]) -> int:
    seq = [
        rng.choice(deck.pools[deck.stage_of_question(q)]) for q in range(10)
    ]
    memo: Dict[Tuple[int, Tuple[int, ...]], int] = {}

    def rec(q: int, attrs_t: Tuple[int, ...]) -> int:
        if q == 10:
            return 0
        attrs = dict(zip(ATTRS, attrs_t))
        if attrs.get("健康", 0) <= 0:
            return 0
        key = (q, attrs_t)
        if key in memo:
            return memo[key]
        stage = deck.stage_of_question(q)
        gain, lose = STAGE_SCORE[stage]
        best = -1
        for o in logic.get_options(deck.events[seq[q]]):
            ok = logic.can_succeed(o, attrs)
            pts = gain if ok else lose
            nxt = apply_delta(attrs, o.success if ok else o.fail)
            v = pts + rec(q + 1, tuple(nxt[k] for k in ATTRS))
            if v > best:
                best = v
        memo[key] = best
        return best

    return rec(0, tuple(init[k] for k in ATTRS))


# --------------------------------------------------------------------------- #
# 对比
# --------------------------------------------------------------------------- #
def compare(deck: Deck, runs: int, seed: int, rollouts: int) -> None:
    nchar = len(CHARACTERS)
    print(f"=== 策略对比（每策略每角色 {runs} 局，seed={seed}）===")
    print(f"真实初始分布：{nchar} 个角色等权，合计 {nchar * runs} 局")
    print("满分 100，全失败 48\n")

    lookahead = (
        lambda d, n, a, q: lookahead_pick(  # noqa: E731
            d, n, a, q, rollouts, random.Random(seed + q), greedy_pick
        )
    )
    policies: Dict[str, Any] = {
        "改造前（旧参数）": legacy_pick,
        "当前策略（贪心+价值函数）": greedy_pick,
        "安全策略（保命优先）": safe_pick,
        f"单步前瞻（{rollouts} 次 rollout）": lookahead,
    }

    def sweep(policy) -> Dict[str, List[int]]:
        out: Dict[str, List[int]] = {}
        for cname, init in CHARACTERS.items():
            rng = random.Random(seed)
            out[cname] = [play(deck, rng, policy, init) for _ in range(runs)]
        return out

    per_char: Dict[str, Dict[str, List[int]]] = {
        tag: sweep(p) for tag, p in policies.items()
    }
    oracle: Dict[str, List[int]] = {}
    for cname, init in CHARACTERS.items():
        rng_o = random.Random(seed)
        oracle[cname] = [oracle_score(deck, rng_o, init) for _ in range(runs)]
    per_char["★上帝视角（已知题序最优）"] = oracle

    base = statistics.mean(
        [s for v in per_char["改造前（旧参数）"].values() for s in v]
    )
    print(f"{'策略':<28}{'平均分':>8}{'中位':>7}{'最低':>7}{'最高':>7}{'满分率':>9}{'相对提升':>10}")
    print("-" * 78)
    for tag, d in per_char.items():
        scores = [s for v in d.values() for s in v]
        m = statistics.mean(scores)
        perfect = sum(1 for s in scores if s == 100) / len(scores)
        print(
            f"{tag:<28}{m:>8.2f}{statistics.median(scores):>7.0f}"
            f"{min(scores):>7}{max(scores):>7}{perfect:>9.1%}{m - base:>+10.2f}"
        )
    print()

    # 逐角色平均分：角色之间差距很大（小猪最弱、小唐最强），必须分开看
    print("逐角色平均分：")
    print(f"{'策略':<28}" + "".join(f"{c:>8}" for c in CHARACTERS))
    for tag, d in per_char.items():
        print(f"{tag:<28}" + "".join(f"{statistics.mean(d[c]):>8.2f}" for c in CHARACTERS))
    print()

    # 逐题成功率
    print("逐题成功率与平均得分：")
    for tag, policy in policies.items():
        print(f"{tag:<24}" + "".join(f"{'Q'+str(i+1):>7}" for i in range(10)))
        succ = [0] * 10
        pts = [0] * 10
        n = 0
        for init in CHARACTERS.values():
            rng = random.Random(seed + 999)
            for _ in range(runs):
                tr: list = []
                play(deck, rng, policy, init, trace=tr)
                n += 1
                for (qi, stage, nm, txt, ok, delta, tot, a2) in tr:
                    if ok:
                        succ[qi - 1] += 1
                    pts[qi - 1] += STAGE_SCORE[stage][0 if ok else 1]
        print(f"{'':<24}" + "".join(f"{succ[i]/n:>7.0%}" for i in range(10)))
        print(f"{'  平均得分':<22}" + "".join(f"{pts[i]/n:>7.1f}" for i in range(10)))
    print()

    # 死亡分布
    print("健康归零题号分布（未完赛占比）：")
    total_runs = runs * nchar
    for tag, policy in policies.items():
        die = Counter()
        for init in CHARACTERS.values():
            rng = random.Random(seed + 555)
            for _ in range(runs):
                attrs = dict(init)
                for q in range(10):
                    stage = deck.stage_of_question(q)
                    nm = rng.choice(deck.pools[stage])
                    opt = logic.get_options(deck.events[nm])[
                        policy(deck, nm, attrs, q)
                    ]
                    attrs = apply_delta(
                        attrs, opt.success if logic.can_succeed(opt, attrs) else opt.fail
                    )
                    if attrs.get("健康", 0) <= 0:
                        die[q + 1] += 1
                        break
        tot = sum(die.values())
        detail = " ".join(f"Q{k}:{v}" for k, v in sorted(die.items()))
        print(
            f"  {tag:<28} 共 {tot}/{total_runs} = {tot/total_runs:.1%}   {detail}"
        )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--runs", type=int, default=150, help="每个角色跑多少局（真实分布下总局数 = 7×）"
    )
    ap.add_argument("--seed", type=int, default=20261012)
    ap.add_argument("--rollouts", type=int, default=24)
    ap.add_argument("--stats", action="store_true")
    args = ap.parse_args()

    deck = Deck()
    if args.stats:
        deck.summary()
        return
    deck.summary()
    compare(deck, args.runs, args.seed, args.rollouts)


if __name__ == "__main__":
    main()
