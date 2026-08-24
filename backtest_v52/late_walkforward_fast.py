from __future__ import annotations

import argparse
import importlib.util
import inspect
import sys
from pathlib import Path


def load_module():
    path = Path(__file__).with_name('late_walkforward.py')
    spec = importlib.util.spec_from_file_location('v52_late_walkforward_base', path)
    if spec is None or spec.loader is None:
        raise RuntimeError('cannot load late_walkforward.py')
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def patch_simulation(mod):
    src = inspect.getsource(mod.simulate_window)
    old = """        for c,g in bycode.items():\n            v=getp(d,c,'AdjClose')\n            if v is not None: last_close[c]=v\n"""
    new = """        # Equivalent mark update restricted to positions actually held.\n        # The previous implementation scanned every price series every market day,\n        # which does not change NAV but is O(universe x days).\n        for c in list(positions):\n            v=getp(d,c,'AdjClose')\n            if v is not None: last_close[c]=v\n"""
    if old not in src:
        raise RuntimeError('expected simulation block not found; refuse silent patch')
    namespace = dict(mod.__dict__)
    exec(src.replace(old, new), namespace)
    mod.simulate_window = namespace['simulate_window']


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest='cmd', required=True)
    p1 = sub.add_parser('fundamentals')
    p1.add_argument('--stock-list', type=Path, required=True)
    p1.add_argument('--out-dir', type=Path, required=True)
    p2 = sub.add_parser('prices-run')
    p2.add_argument('--pre-dir', type=Path, required=True)
    p2.add_argument('--out-dir', type=Path, required=True)
    args = ap.parse_args()

    mod = load_module()
    if args.cmd == 'fundamentals':
        # Execution-only concurrency increase. Scoring, dates, thresholds and universe are unchanged.
        mod.THREADS_DETAIL = 18
        mod.THREADS_IXBRL = 28
        mod.stage_fundamentals(args.stock_list, args.out_dir)
    else:
        patch_simulation(mod)
        mod.THREADS_PRICE = 12
        mod.stage_prices_run(args.pre_dir, args.out_dir)


if __name__ == '__main__':
    main()
