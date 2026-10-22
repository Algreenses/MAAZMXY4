# -*- coding: utf-8 -*-
"""统计「已知题序 = 最优」策略下的单局分数分布与一周 7 天总分概率。

只做离线模拟，不连游戏。用途：给任务说明填「平均分 / 满分概率 / 一周总分概率」。

跑法：
    python tools/sim_score_stats.py            # 每角色 3000 局
    python tools/sim_score_stats.py --runs 8000
"""
import argparse
import json
import os
import random
import sys
from typing import Dict, List

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "agent"))

import adventure_logic as logic  # noqa: E402

ATTRS = ["战力", "智慧", "魅力", "灵巧", "运气", "健康"]
NQ = 10


def load_events():
    with open(os.path.join(REPO, "assets", "data", "adventure.json"), encoding="utf-8") as f:
        return json.load(f)["events"]


def stage_pools(events) -> Dict[int, List[str]]:
    pools: Dict[int, List[str]] = {}
    for name, ev in events.items():
        pools.setdefault(int(ev["stage"]), []).append(name)
    return pools


def draw_sequence(pools, rng: random.Random) -> List[str]:
    """每阶段内部不放回地抽题，阶段之间独立。"""
    seq: List[str] = []
    for st in (1, 2, 3, 4, 5):
        k = logic.QUESTION_STAGES.count(st) if hasattr(logic, "QUESTION_STAGES") else {1: 1, 2: 2, 3: 3, 4: 3, 5: 1}[st]
        pool = pools[st]
        seq.extend(rng.sample(pool, k))
    return seq


def step(attrs: Dict[str, int], opt) -> bool:
    """返回是否成功，并把属性变化写回 attrs。"""
    if opt.get("always_fail"):
        ok = False
    else:
        req = opt.get("require") or {}
        ok = all(attrs.get(k, 0) >= v for k, v in req.items())
    delta = opt.get("success" if ok else "fail") or {}
    for k, v in delta.items():
        attrs[k] = max(0, attrs.get(k, 0) + int(v))
    return ok


def play(events, pools, init, rng: random.Random, policy: str = "seq") -> int:
    seq = draw_sequence(pools, rng)
    attrs = dict(init)
    total = 0
    for q in range(NQ):
        name = seq[q]
        if policy == "seq":
            picked = logic.seq_plan(events, seq, attrs, q, name)
            if picked is None or picked[0] is None:
                idx = logic.choose(events, name, attrs, question=q).index
            else:
                idx = int(picked[0])
        else:
            # 发布版：读不到内存题序，只按阶段后向归纳
            idx = logic.choose(events, name, attrs, question=q).index
        opt = events[name]["options"][idx]
        ok = step(attrs, opt)
        st = int(events[name]["stage"])
        total += logic.question_score(q, ok) if hasattr(logic, "question_score") else 0
        if attrs.get("健康", 0) <= 0 and q < NQ - 1:
            break
    return total


def week_probs(scores: List[int]) -> Dict[str, float]:
    """用卷积从单局分布算 7 天总分的累积概率，比再采样 10 万次更准。"""
    from collections import defaultdict
    dist = defaultdict(int)
    for s in scores:
        dist[s] += 1
    n = len(scores)
    cur = {0: 1.0}
    for _ in range(7):
        nxt: Dict[int, float] = defaultdict(float)
        for a, pa in cur.items():
            for b, pb in dist.items():
                nxt[a + b] += pa * pb / n
        cur = nxt
    return {
        ">=480": sum(p for s, p in cur.items() if s >= 480),
        ">=620": sum(p for s, p in cur.items() if s >= 620),
        "==700": sum(p for s, p in cur.items() if s == 700),
        "mean": sum(s * p for s, p in cur.items()),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=3000)
    ap.add_argument("--seed", type=int, default=20261022)
    ap.add_argument("--policy", choices=("seq", "stage"), default="seq",
                    help="seq=已知题序最优（自用版）；stage=按阶段后向归纳（发布版）")
    args = ap.parse_args()

    events = load_events()
    pools = stage_pools(events)
    rng = random.Random(args.seed)

    all_scores: List[int] = []
    print("策略 = %s" % args.policy)
    print("%-4s %6s %6s %7s %8s %8s" % ("角色", "平均", "满分%", "≥90%", "周≥480", "周≥620"))
    per_char = {}
    for name, init in logic.CHARACTERS.items():
        scores = [play(events, pools, init, rng, args.policy) for _ in range(args.runs)]
        all_scores.extend(scores)
        wp = week_probs(scores)
        per_char[name] = (sum(scores) / len(scores), scores.count(100) / len(scores), wp)
        print("%-4s %6.2f %6.2f %7.2f %8.2f %8.2f"
              % (name, per_char[name][0], per_char[name][1] * 100,
                 sum(1 for s in scores if s >= 90) / len(scores) * 100,
                 wp[">=480"] * 100, wp[">=620"] * 100))

    wp = week_probs(all_scores)
    print("-" * 48)
    print("等权7角色 平均 %.2f  满分 %.2f%%  ≥90 %.2f%%  周≥480 %.2f%%  周≥620 %.2f%%  周满分 %.2f%%"
          % (sum(all_scores) / len(all_scores),
             all_scores.count(100) / len(all_scores) * 100,
             sum(1 for s in all_scores if s >= 90) / len(all_scores) * 100,
             wp[">=480"] * 100, wp[">=620"] * 100, wp["==700"] * 100))
    print("一周期望总分 %.1f / 700" % wp["mean"])


if __name__ == "__main__":
    main()
