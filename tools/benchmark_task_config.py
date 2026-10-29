"""Repeatable read-only task-configuration benchmark; no controller or client."""
import argparse
import json
from pathlib import Path
import statistics
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'agent'))
from task_catalog import _configuration


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runs', type=int, default=20)
    parser.add_argument('--root', type=Path, default=ROOT)
    args = parser.parse_args()
    if not 1 <= args.runs <= 1000:
        parser.error('runs must be between 1 and 1000')
    times = []
    for _ in range(args.runs):
        start = time.perf_counter()
        interface, nodes = _configuration(args.root.resolve())
        times.append((time.perf_counter()-start)*1000)
    print(json.dumps(dict(runs=args.runs, nodes=len(nodes), tasks=len(interface.get('task',[])),
        median_ms=statistics.median(times), min_ms=min(times), max_ms=max(times)), indent=2))
