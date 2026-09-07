"""Daily Change-In-Polarity strategy (runs every trading day after close).

Same CIP rules as cip_weekly.py, applied to daily candles - see
signals/strategies/cip.py for the shared detection logic.
"""
from __future__ import annotations

import pandas as pd

from signals import config
from signals.models import Signal
from signals.strategies.cip import scan_cip


def scan(daily_data: dict[str, pd.DataFrame]) -> list[Signal]:
    return scan_cip(
        daily_data,
        touch_lookback=config.CIP_DAILY_TOUCH_LOOKBACK,
        breakout_search=config.CIP_DAILY_BREAKOUT_SEARCH,
        min_touches=config.CIP_DAILY_MIN_TOUCHES,
        timeframe_label="daily",
    )
