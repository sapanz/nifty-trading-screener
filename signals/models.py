"""Shared result type produced by every strategy."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Signal:
    symbol: str
    entry: float
    stop_loss: float
    targets: list[float]
    sort_key: float = 0.0   # higher = shown first within its strategy section
    note: str = ""          # extra context, e.g. "breakout after 2y 3m"
    extra: dict = field(default_factory=dict)

    @property
    def risk_per_share(self) -> float:
        return self.entry - self.stop_loss
