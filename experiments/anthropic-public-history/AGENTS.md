# Public market history probe

This folder is isolated from the host repository's trading code.

- Only anonymous HTTPS GET requests to the explicit public market-data endpoints.
- Do not use credentials, cookies, account APIs, trading, transfers, or access-control bypasses.
- Do not save environment dumps, response headers, or raw API bodies; allowlisted numeric market fields only.
- Preserve missing data and failures. Complete downloads do not establish price-kind equivalence, contract equivalence, funding PnL, or convergence.
- Run `python3 -m unittest discover -s experiments/anthropic-public-history -v` before publishing changes.
- The owner explicitly authorized branch-only experiments in this repository. Do not change master or merge this experiment.
