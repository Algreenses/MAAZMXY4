"""量一次「整表常驻」到底省了多少拉表时间（与旧策略对照）。

线上一个任务里连打多局，角色每局随机。旧策略（累计 > 40 万就清表）几乎等于
每局都重建；新策略整表常驻，7 个角色各付一次。这里用同一串角色序跑两遍对比。

用法：python tools/measure_table_warm.py [局数]   默认 14 局（7 角色各出现两次）
"""
from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent import adventure_logic as logic  # noqa: E402
from tools import simulate_adventure as sim  # noqa: E402

OLD_THRESHOLD = 400_000  # 旧 EXACT_RESET_THRESHOLD，仅用于对比


def run(rounds: int, old_policy: bool) -> tuple:
    """按角色序跑 rounds 局「第一题」，返回 (总拉表秒数, 逐局明细)。"""
    names = sorted(logic.CHARACTERS)
    order = [names[i % len(names)] for i in range(rounds)]
    logic.reset_exact_memo()
    total = 0.0
    rows = []
    for i, name in enumerate(order):
        if old_policy:
            if len(logic._exact_memo) > OLD_THRESHOLD:
                logic._exact_memo.clear()
                logic._exact_states = 0
        t0 = time.time()
        logic.choose(sim.DECK.events, "冒险前没好武器",
                     dict(logic.CHARACTERS[name]), question=0)
        dt = time.time() - t0
        total += dt
        rows.append((i + 1, name, dt, logic.exact_stats()[0]))
    return total, rows


def main() -> None:
    rounds = int(sys.argv[1]) if len(sys.argv) > 1 else 14
    print(f"角色序：{' -> '.join(sorted(logic.CHARACTERS))} 循环，共 {rounds} 局\n")

    for label, old in (("旧策略（>40 万就清表 = 每局重建）", True),
                       ("新策略（整表常驻，7 角色各付一次）", False)):
        total, rows = run(rounds, old)
        print(f"=== {label} ===")
        for i, name, dt, st in rows:
            print(f"  第 {i:2d} 局 {name:<3} 拉表 {dt:6.2f}s   表 {st:>9,}")
        print(f"  合计拉表 {total:6.2f}s，平均每局 {total / rounds:5.2f}s\n")

    print(f"EXACT_MAX_STATES = {logic.EXACT_MAX_STATES:,}")


if __name__ == "__main__":
    main()
