# 日本株 Value・Momentum・Profitability 実バックテスト結果

- Research ID: `R-20260902-JP-FF-VMP-INCREMENTAL`
- Primary period: 2015-07 to 2026-06, 132 months
- Currency: USD total returns
- Workflow run: `33597231102`
- Executed commit: `ffef582c81e81d0a7ad1e8d3d7248c1093de0554`
- Artifact digest: `sha256:f11f71dbed2aef4571c71dc7e85ee3b826e7367d745b53dc3d682b2397ec77f2`
- Implementation SHA-256: `f6657d2b3df80eea17cdf9dd81a85ec855f2974f8092f3f830496ce441de1416`

## Verdict

`REJECT_PROFITABILITY_INCREMENT`

Operating Profitability does not improve the Japanese Value+Momentum strategy under the preregistered tests. In the primary Big-only implementation, Value+Momentum CAGR was 9.78%, while adding Profitability reduced CAGR to 9.18%, a difference of -0.60 percentage points per year. The annualized arithmetic return difference was -0.62%, HAC t-stat was -0.883, the 12-month circular block-bootstrap 95% interval was [-2.01%, +0.70%], and the bootstrap probability that the increment is positive was 18.82%.

The increment was also negative in the other preregistered primary implementations:

| Implementation | CAGR difference | P(increment > 0) | Drawdown effect |
|---|---:|---:|---:|
| Big-only VPM minus VM | -0.60pt | 18.82% | -23.43% to -26.07% |
| Big 32-sleeve VPM minus VM | -0.17pt | 34.14% | -24.65% to -24.06% |
| Size-balanced VPM minus VM | -0.75pt | 19.08% | -26.70% to -27.75% |
| Big high-B/M high-OP intersection plus Momentum | -0.73pt | 31.36% | -24.65% to -33.45% |

The Big-only increment was negative in every major period: -0.65pt for 1990-12 to 2026-06, -0.67pt before 2015-07, -0.60pt in the primary period, -0.76pt from 2016-01, and -1.61pt from 2020-01. The corresponding bootstrap probabilities of a positive increment were 2.88%, 5.04%, 18.82%, 13.96%, and 5.26%.

## Primary gross results

| Strategy | CAGR | Excess CAGR | Sharpe | IR | Maximum drawdown | P(excess > 0) |
|---|---:|---:|---:|---:|---:|---:|
| Market | 8.23% | - | 0.483 | - | -28.93% | - |
| Big Value | 9.71% | +1.48pt | 0.528 | 0.190 | -30.84% | 73.66% |
| Big Momentum | 9.37% | +1.15pt | 0.508 | 0.186 | -30.25% | 75.22% |
| Big Profitability | 7.79% | -0.44pt | 0.447 | -0.077 | -31.33% | 35.00% |
| Big Value+Momentum | 9.78% | +1.55pt | 0.569 | 0.339 | -23.43% | 84.16% |
| Big Value+Profitability | 8.94% | +0.71pt | 0.531 | 0.185 | -25.70% | 70.84% |
| Big Value+Profitability+Momentum | 9.18% | +0.95pt | 0.545 | 0.326 | -26.07% | 83.14% |

## Cost stress changes the winner

The preregistered base haircut charges 20bp at each July reconstitution to Value and Profitability sleeves and 20bp each month to the Momentum sleeve. The double scenario uses 40bp.

| Strategy | Gross CAGR | Base haircut | Double haircut | Market CAGR |
|---|---:|---:|---:|---:|
| Big Value | 9.71% | 9.49% | 9.28% | 8.23% |
| Big Momentum | 9.37% | 6.79% | 4.27% | 8.23% |
| Big Value+Momentum | 9.78% | 8.37% | 6.98% | 8.23% |
| Big Value+Profitability | 8.94% | 8.72% | 8.51% | 8.23% |
| Big Value+Profitability+Momentum | 9.18% | 8.17% | 7.17% | 8.23% |

Big Value+Momentum is the gross winner and has the best gross drawdown, but its base-cost excess CAGR falls to +0.14pt and becomes -1.25pt under double cost. Big Value remains +1.27pt after base cost and +1.05pt after double cost. The next security-level PIT test should therefore prioritize Big Value, with Value+Momentum run in parallel to measure actual Momentum turnover and execution cost.

## Profitability is a risk-control component, not an alpha increment

The official RMW factor in the primary period had annualized arithmetic return -1.32%, unit-notional CAGR -1.53%, maximum drawdown -39.63%, HAC t-stat -0.546, and P(mean > 0) 30.46%.

Adding RMW to equal-weight HML+Momentum reduced maximum drawdown from -26.07% to -11.00%, but reduced CAGR by 1.33pt. Inverse-volatility weighting reduced maximum drawdown from -26.04% to -4.39%, but reduced CAGR by 1.45pt. Profitability can therefore be reconsidered only as a defensive overlay or quality floor, under a new preregistration and unused period.

## Data and audit

The run downloaded the current official Kenneth French Japanese 5-factor, Momentum, 6-portfolio, and 32-portfolio ZIP files and stored URLs, byte sizes, Last-Modified values, and SHA-256 hashes. HML reconstruction differed from the official factor by at most 2.0bp and Momentum by at most 1.5bp. RMW uses the official 5-factor series as canonical because the standalone six-portfolio dataset has non-identical eligibility rules; in the primary period its reconstructed correlation was 0.999991, mean absolute difference 0.576bp, and maximum difference 3.5bp.

Statistical tests used HAC with six lags and 5,000 twelve-month circular block-bootstrap draws with seed `20260902`.

## Limitations

Kenneth French Data Library rebuilds full historical returns when datasets are updated, so this is a frozen-snapshot replication rather than pristine vintage PIT OOS. Returns are in USD. Published portfolio returns do not include stock-level spreads, taxes, halts, measured turnover, or order-book capacity. Value+Momentum is a monthly equal-weight combination of published sleeves, not a single stock-level ranking. Independent adversarial review is still pending; no production or live-capital promotion is allowed.

## Next decision

1. Stop priority work on the full QMJ implementation and reject Profitability as a return-enhancing addition.
2. Rebuild Big Value with J-Quants security-level PIT data, historical universe membership, next-tradable execution, and measured turnover.
3. Run Value+Momentum under the identical universe and execution model. It only outranks Value if measured costs remain below its narrow break-even margin.
4. Treat Profitability only as a separately preregistered drawdown-control filter.
