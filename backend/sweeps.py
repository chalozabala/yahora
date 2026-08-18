"""Sweep order detection engine.

Faithful reimplementation of the behavior of Bookmap's "Sweeps" indicator,
as documented publicly (knowledge base + add-on API docs):

  * Trades are grouped into *chains* per aggressor side. Buys and sells are
    detected independently — a sell print does not interrupt a buy chain.
  * A chain keeps growing while the gap between consecutive prints of that
    side is <= the time limit (``window_ns``, Bookmap's ``timeLimitMillis``).
  * When a chain closes it is a sweep if BOTH hold:
      - total traded volume across the whole chain (all levels combined)
        reaches the size threshold (Bookmap's ``sizeLimit``), and
      - the prints touched at least ``min_levels`` distinct price levels
        (Bookmap's ``minLevels``).
  * The size threshold is either a fixed number or, in *automatic* mode,
    recomputed continuously as
        stdev(trade sizes over the last ``sd_interval``) * ``sd_multiplier``.
  * A sweep can only be reported once its chain has gone quiet, so marks
    appear with a delay of at least the time limit — same as the original.

The detector is a streaming state machine fed one trade at a time; it is
transport-agnostic and shared by the demo generator, Databento live and
historical replay feeds.
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field
from typing import Deque, Dict, List, Optional, Tuple

MAX_PRINTS_PER_SWEEP = 64      # cap on per-trade detail shipped to the UI
MIN_SD_SAMPLES = 8             # below this, automatic mode falls back to fixed


@dataclass
class Trade:
    ts: int          # nanoseconds since epoch (ts_event)
    price: float     # decimal price
    size: int
    side: str        # "B" = buy aggressor, "A" = sell aggressor, "N" = none


@dataclass
class Sweep:
    ts_start: int
    ts_end: int
    side: str                # "B" or "A"
    total_size: int
    levels: int              # distinct price levels swept
    price_first: float
    price_last: float
    price_min: float
    price_max: float
    vwap: float
    trade_count: int
    threshold: float         # size threshold in force when it qualified
    prints: List[Tuple[int, float, int]]   # (ts, price, size)

    def to_dict(self) -> dict:
        return {
            "type": "sweep",
            "ts_start": self.ts_start,
            "ts_end": self.ts_end,
            "side": self.side,
            "size": self.total_size,
            "levels": self.levels,
            "px_first": self.price_first,
            "px_last": self.price_last,
            "px_min": self.price_min,
            "px_max": self.price_max,
            "vwap": round(self.vwap, 9),
            "trades": self.trade_count,
            "threshold": round(self.threshold, 2),
            "prints": [[p[0], p[1], p[2]] for p in
                       self.prints[:MAX_PRINTS_PER_SWEEP]],
        }


@dataclass
class _Chain:
    side: str
    ts_first: int
    ts_last: int
    trades: List[Trade] = field(default_factory=list)

    def add(self, t: Trade) -> None:
        self.trades.append(t)
        if t.ts > self.ts_last:
            self.ts_last = t.ts


class SweepDetector:
    """Streaming sweep detector with fixed or SD-automatic size threshold."""

    def __init__(self, window_ns: int = 200_000_000, min_levels: int = 3,
                 min_size: int = 40, auto_size: bool = False,
                 sd_interval_ns: int = 5 * 60 * 1_000_000_000,
                 sd_multiplier: float = 6.0):
        self._chains: Dict[str, _Chain] = {}          # side -> open chain
        self._sizes: Deque[Tuple[int, int]] = deque() # (ts, size) history
        # route through update_params so construction and live updates
        # apply identical clamping
        self.update_params(window_ns=window_ns, min_levels=min_levels,
                           min_size=min_size, auto_size=auto_size,
                           sd_interval_ns=sd_interval_ns,
                           sd_multiplier=sd_multiplier)

    def update_params(self, window_ns: Optional[int] = None,
                      min_levels: Optional[int] = None,
                      min_size: Optional[int] = None,
                      auto_size: Optional[bool] = None,
                      sd_interval_ns: Optional[int] = None,
                      sd_multiplier: Optional[float] = None) -> None:
        if window_ns is not None:
            self.window_ns = max(0, int(window_ns))
        if min_levels is not None:
            self.min_levels = max(2, int(min_levels))
        if min_size is not None:
            self.min_size = max(0, int(min_size))
        if auto_size is not None:
            self.auto_size = bool(auto_size)
        if sd_interval_ns is not None:
            self.sd_interval_ns = max(60_000_000_000, int(sd_interval_ns))
        if sd_multiplier is not None:
            self.sd_multiplier = max(0.1, float(sd_multiplier))

    # ------------------------------------------------------------------

    def current_threshold(self, now_ns: int) -> float:
        """Size threshold in force at ``now_ns`` (SD-based when automatic).

        Only samples with ts <= now_ns count, so a chain that closed at
        ts_last is judged by the history that existed then — a later
        outlier print cannot retroactively raise its threshold.
        """
        if not self.auto_size:
            return float(self.min_size)
        cutoff = now_ns - self.sd_interval_ns
        samples = [s for ts, s in self._sizes if cutoff <= ts <= now_ns]
        n = len(samples)
        if n < MIN_SD_SAMPLES:
            return float(self.min_size)
        mean = sum(samples) / n
        var = sum((s - mean) ** 2 for s in samples) / n
        return math.sqrt(var) * self.sd_multiplier

    def on_trade(self, trade: Trade) -> List[Sweep]:
        """Feed one trade; returns any sweep completed by its arrival."""
        if trade.side not in ("B", "A"):
            return []                      # no aggressor: not part of a chain
        out: List[Sweep] = []

        # close a stale chain BEFORE recording this trade's size, so the
        # closing print does not contaminate the chain's SD threshold
        chain = self._chains.get(trade.side)
        if chain is not None and trade.ts - chain.ts_last > self.window_ns:
            out.extend(self._close(trade.side))
            chain = None

        self._sizes.append((trade.ts, trade.size))
        self._trim_sizes(trade.ts)

        if chain is None:
            chain = _Chain(side=trade.side, ts_first=trade.ts,
                           ts_last=trade.ts)
            self._chains[trade.side] = chain
        chain.add(trade)
        return out

    def flush(self, now_ns: int) -> List[Sweep]:
        """Close chains that have gone quiet. Call as the clock advances."""
        out: List[Sweep] = []
        for side in ("B", "A"):
            chain = self._chains.get(side)
            if chain is not None and now_ns - chain.ts_last > self.window_ns:
                out.extend(self._close(side))
        return out

    def finalize(self) -> List[Sweep]:
        """Force-close all open chains — call when a bounded feed ends."""
        out: List[Sweep] = []
        for side in ("B", "A"):
            out.extend(self._close(side))
        return out

    # ------------------------------------------------------------------

    def _trim_sizes(self, now_ns: int) -> None:
        """Bound the size-history deque in every mode, not just automatic."""
        cutoff = now_ns - self.sd_interval_ns
        while self._sizes and self._sizes[0][0] < cutoff:
            self._sizes.popleft()

    def _close(self, side: str) -> List[Sweep]:
        chain = self._chains.pop(side, None)
        if chain is None:
            return []
        prices = [t.price for t in chain.trades]
        levels = len(set(prices))
        total = sum(t.size for t in chain.trades)
        threshold = self.current_threshold(chain.ts_last)
        if levels < self.min_levels or total < threshold or total <= 0:
            return []
        vwap = sum(t.price * t.size for t in chain.trades) / total
        return [Sweep(
            ts_start=chain.ts_first,
            ts_end=chain.ts_last,
            side=side,
            total_size=total,
            levels=levels,
            price_first=chain.trades[0].price,
            price_last=chain.trades[-1].price,
            price_min=min(prices),
            price_max=max(prices),
            vwap=vwap,
            trade_count=len(chain.trades),
            threshold=threshold,
            prints=[(t.ts, t.price, t.size) for t in chain.trades],
        )]
