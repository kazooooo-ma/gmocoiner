# 日本株 Value と Value+Momentum の費用再較正

- Research ID: `R-20260902-JP-FF-VMP-INCREMENTAL`
- Status: `POST_HOC_IMPLEMENTATION_DIAGNOSTIC`
- Date: 2026-09-02

## Correction

The preregistered base stress charged 20bp at every July Value/Profitability reconstitution and 20bp every month to the Momentum sleeve. It was intentionally conservative and was not derived from measured turnover. It remains a valid stress scenario, but it must not be used alone to declare Big Value economically superior to Big Value+Momentum.

This post-hoc diagnostic does not change the preregistered rejection of the Profitability increment. It only calibrates the Value versus Value+Momentum implementation decision.

## Turnover proxies

Official MSCI index pages reported the following last-12-month turnover as of 2026-07-31:

- MSCI Japan Momentum Index: 75.43%
- MSCI Japan Enhanced Value Index: 20.92%
- MSCI Japan Index: 5.47%

The backtest's monthly sleeve-rebalancing calculation added 8.36% estimated one-way turnover per year for the 50/50 Value+Momentum allocation.

Therefore the diagnostic turnover assumptions are:

- Value: 20.92% per year
- Value+Momentum: 0.5 × 20.92% + 0.5 × 75.43% + 8.36% = 56.535% per year

These are proxies, not the actual turnover of the Kenneth French portfolios.

## Net CAGR sensitivity

A constant annual cost equal to `turnover × cost_per_100pct_turnover` was spread evenly across months and deducted from the official monthly return series.

| Cost per 100% one-way turnover | Big Value CAGR | Big Value+Momentum CAGR | VM minus Value |
|---:|---:|---:|---:|
| 0bp | 9.7079% | 9.7784% | +7.05bp |
| 10bp | 9.6851% | 9.7168% | +3.17bp |
| 20bp | 9.6623% | 9.6552% | -0.71bp |
| 30bp | 9.6395% | 9.5936% | -4.59bp |
| 40bp | 9.6167% | 9.5321% | -8.46bp |

The exact break-even is approximately 18.17bp of all-in cost per 100% one-way turnover.

## Updated implementation decision

- Profitability increment: `REJECT`, unchanged.
- If measured all-in execution cost is below 18.17bp per 100% one-way turnover, Big Value+Momentum is preferred because it retains the higher CAGR and has materially better gross risk statistics: Information ratio 0.339 versus 0.190 and maximum drawdown -23.43% versus -30.84%.
- If measured cost is above 18.17bp, Big Value is preferred.
- At 20bp, the two strategies are economically tied on CAGR; Value+Momentum retains the drawdown advantage.

The next security-level PIT test must therefore run Value and Value+Momentum in parallel. It should not preselect Value based on the conservative monthly 20bp sleeve stress.

## Sources

- MSCI Japan Momentum Index: https://www.msci.com/indexes/index/703763/msci-japan-momentum-index
- MSCI Japan Enhanced Value Index: https://www.msci.com/indexes/index/706026/msci-japan-enhanced-value-index
- MSCI Japan Index: https://www.msci.com/indexes/index/939200/msci-japan-index
