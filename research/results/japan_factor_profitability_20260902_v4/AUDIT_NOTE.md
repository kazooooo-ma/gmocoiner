# Independent audit note

Recorded: 2026-09-02 JST

## Verdict

`REJECT_PROFITABILITY_INCREMENT`

For the preregistered primary window 2015-07 through 2026-06 (132 monthly observations), adding the direct high-operating-profitability sleeve to the big-stock Value+Momentum portfolio reduced CAGR from 9.7784% to 9.1789% (-0.5995 percentage points/year). The annualized mean incremental return was -0.4398%, HAC t-statistic -0.4025, and the 12-month circular block bootstrap probability that the increment is positive was 18.82% (95% interval -3.5202% to +2.0986%). Maximum drawdown worsened from -23.39% to -26.10%.

The result was independently recomputed from the committed official monthly portfolio files and matches `incremental.csv` and `decision.json`.

## Cross-construction robustness

Profitability addition was also negative in all direct broad-combination constructions used by the frozen test:

- size-balanced six-portfolio blend: CAGR difference -0.7035%, bootstrap positive probability 18.20%
- 32-portfolio broad grid: CAGR difference -0.2863%, probability 30.70%
- big-stock 32-portfolio broad grid: CAGR difference -0.1649%, probability 34.14%
- zero-cost HML+RMW+Momentum vs HML+Momentum: CAGR difference -2.8024%, HAC t -2.0461, probability 3.38%

The targeted big-stock high-B/M × high-OP intersection plus Momentum improved CAGR by 2.2782%, but its HAC t-statistic was 0.8290 and 95% block-bootstrap interval was -3.0226% to +7.9254%; it is a separate `WATCH` hypothesis, not evidence for the rejected broad addition.

## Correction to protocol deviation record

The generated `protocol_deviation.json` contains stale hard-coded observed values from the prior failed run. The completed v4 run's actual `decision.json` and `reconstruction.csv` show:

- HML maximum reconstruction difference: 2.0 bp
- RMW maximum reconstruction difference: 34.0 bp
- Momentum maximum reconstruction difference: 1.5 bp

Thus RMW, not HML, exceeded the preregistered 3 bp validation threshold. The discrepancy is concentrated in 13 months (2004-07 through 2005-06 and 2019-07) between the official five-factor RMW series and the RMW reconstructed from the official six Size×OP portfolios. This does not change the direct long-only six-portfolio or 32-portfolio calculations, which use their published portfolio returns directly. Zero-cost factor-combination results remain secondary because the factor reconstruction gate failed.

## Cost interpretation

The fixed haircut model charges Momentum monthly and Value/Profitability at the July annual reconstitution. Under the doubled haircut, the three-sleeve portfolio mechanically pays less Momentum haircut than the two-sleeve portfolio because Momentum weight falls from 50% to 33.3%. Any reversal under that stress is a lower assumed-turnover effect, not evidence of profitability alpha. The production decision therefore rests on gross direct returns plus the incremental HAC and block-bootstrap tests.

## Scope limits

The source series are Kenneth French Data Library Japanese value-weighted portfolio returns in U.S. dollars including dividends and capital gains. They are actual published portfolio return histories, but not a J-Quants stock-level execution simulation. Currency conversion is common to direct long-only Japanese portfolios, so the sign of each same-month strategy difference and relative wealth ordering are unchanged by converting both legs to yen. Stock-level spreads, taxes, capacity, and borrowability require the next PIT security-level test.
