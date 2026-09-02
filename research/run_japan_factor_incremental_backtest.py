from __future__ import annotations

"""HTTPS compatibility wrapper for the frozen Japan factor backtest.

The frozen implementation uses pandas-datareader's Fama/French reader. Version
0.10.0 still defaults to an HTTP base URL, while the Kenneth French Data Library
is available over HTTPS. Patch only the transport base URL, then execute the
frozen script unchanged.
"""

import runpy
from pathlib import Path

import pandas_datareader.famafrench as famafrench

famafrench._URL = "https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/"

script = Path(__file__).with_name("japan_factor_incremental_backtest.py")
runpy.run_path(str(script), run_name="__main__")
