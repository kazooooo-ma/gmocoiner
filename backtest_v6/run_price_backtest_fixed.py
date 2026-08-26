from pathlib import Path

src_path = Path(__file__).with_name("run_price_backtest.py")
src = src_path.read_text(encoding="utf-8")
old = "gate_bench=prices.get(-1) or prices.get(-2)"
new = "gate_bench=prices.get(-1) if -1 in prices else prices.get(-2)"
if old not in src:
    raise RuntimeError("Expected benchmark-selection expression not found; refuse silent patch")
src = src.replace(old, new, 1)
exec(compile(src, str(src_path), "exec"), {"__name__": "__main__", "__file__": str(src_path)})
