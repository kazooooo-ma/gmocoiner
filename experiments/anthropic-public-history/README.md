# Anthropic public history probe

Read-only, credential-free Lighter and Variational market-history experiment.
The host repository's trading package and existing workflows are neither executed nor changed.

The dedicated Actions workflow triggers only on pushes to
`feature/probe-anthropic-public-history-20260911` affecting this folder or its workflow.
It runs offline tests, a one-day smoke request, then up to 30 days for individually validated streams.
No schedule, deployment, trading, account API, API key, or cookie is used.

Lighter uses documented `/api/v1/orderBooks`, `/api/v1/candles`,
`/api/v1/markPriceCandles`, and `/api/v1/fundings`.
Variational uses public `/metadata/stats` and an unofficial `/api/candles` candidate.
HTTP 401/403 is recorded and not bypassed. Responses are never saved wholesale.
Only validated timestamps, OHLC, and numeric funding fields are written, with bounded diagnostics.

```bash
python3 -m unittest discover -s experiments/anthropic-public-history -v
python3 experiments/anthropic-public-history/probe_anthropic.py --venue lighter --days 1 --out output/lighter
python3 experiments/anthropic-public-history/probe_anthropic.py --venue variational --days 1 --out output/variational
```

Output artifacts expire after seven days. Empty, incomplete, and rejected streams are failures, not zero values.
Funding units/direction, price kinds, contract units, and convergence remain unproven until analyzed separately.
Request timestamps default to seconds; returned seconds/milliseconds are validated independently.

Sources:
- https://github.com/elliottech/lighter-python/blob/main/docs/CandlestickApi.md
- https://github.com/elliottech/lighter-python/blob/main/docs/Funding.md
- https://docs.variational.io/technical-documentation/api
- https://github.com/Caio-Fl/Airdrop_Points/blob/87a4de00d4a8b3e7a41685305b20a6df6793b138/exchanges_klines.py
