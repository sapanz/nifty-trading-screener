"""Weekly Change-In-Polarity strategy (runs Fridays after close).

Same CIP rules as cip_daily.py, applied to weekly candles - see
signals/strategies/cip.py for the shared detection logic.
"""
from __future__ import annotations

import pandas as pd

from signals import config
from signals.models import Signal
from signals.strategies.cip import scan_cip


def scan(weekly_data: dict[str, pd.DataFrame]) -> list[Signal]:
    return scan_cip(
        weekly_data,
        touch_lookback=config.CIP_WEEKLY_TOUCH_LOOKBACK,
        breakout_search=config.CIP_WEEKLY_BREAKOUT_SEARCH,
        timeframe_label="weekly",
    )
