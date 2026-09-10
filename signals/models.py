"""Shared result type produced by every strategy."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date


@dataclass
class Signal:
    symbol: str
    entry: float
    stop_loss: float
    targets: list[float]
    # "long" (buy - stop_loss below entry, targets above) or "short" (sell -
    # stop_loss above entry, targets below). Every strategy but Futures OI
    # Buildup only ever produces "long"; this defaults to it so none of
    # them need to think about direction at all.
    direction: str = "long"
    sort_key: float = 0.0   # higher = shown first within its strategy section
    note: str = ""          # extra context, e.g. "breakout after 2y 3m"
    extra: dict = field(default_factory=dict)
    # The actual date of the candle that qualified this signal (the last
    # row scan() looked at) - distinct from the date the screener happened
    # to run on. The two are usually the same day, but not always: a stale
    # Upstox fetch, a run landing very late (well after market close, even
    # past midnight IST), or a market holiday the code doesn't know about
    # can all leave the most recent available candle older than "today".
    # Surfacing this explicitly in the Telegram message means that gap is
    # visible instead of silently assumed away.
    candle_date: date | None = None

    @property
    def risk_per_share(self) -> float:
        # Always positive: stop_loss sits on the losing side of entry,
        # whichever direction that is (below for long, above for short).
        if self.direction == "short":
            return self.stop_loss - self.entry
        return self.entry - self.stop_loss
