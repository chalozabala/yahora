"""Data feeds for the sweeps indicator.

Three interchangeable async feeds, all yielding the same event dicts:

  {"type": "snapshot", "ts": ns, "bids": [[px, sz] * 10], "asks": [[px, sz] * 10]}
  {"type": "trade",    "ts": ns, "px": float, "sz": int, "side": "B"|"A"|"N"}
  {"type": "status",   ...}   informational
  {"type": "error",    ...}   terminal

* DemoFeed        — synthetic order book + trades, no credentials needed.
* LiveFeed        — Databento live gateway (requires DATABENTO_API_KEY).
* ReplayFeed      — Databento historical range replayed on a clock.

Sweep detection itself happens in the caller (server.py) so all feeds share
one code path.
"""

from __future__ import annotations

import asyncio
import math
import os
import random
import time
from typing import AsyncIterator, Optional

PRICE_SCALE = 1e-9          # Databento fixed-precision prices are int * 1e-9
UNDEF_PRICE = 2 ** 63 - 1   # INT64_MAX sentinel for "no price"

BOOK_DEPTH = 10


def _px(raw: int) -> Optional[float]:
    if raw is None or raw == UNDEF_PRICE:
        return None
    return raw * PRICE_SCALE


# ---------------------------------------------------------------------------
# Demo feed
# ---------------------------------------------------------------------------

class DemoFeed:
    """Synthetic ES-like market: 0.25 tick, drifting book, periodic sweeps."""

    def __init__(self, symbol: str = "DEMO.ES", tick: float = 0.25,
                 start_price: float = 6400.0):
        self.symbol = symbol
        self.tick = tick
        # integer tick index of the best ask; best bid = best_ask - 1 tick
        self.best_ask_i = round(start_price / tick)
        self.bid_sz = [self._lvl_size(i) for i in range(BOOK_DEPTH)]
        self.ask_sz = [self._lvl_size(i) for i in range(BOOK_DEPTH)]
        self._stop = asyncio.Event()

    @staticmethod
    def _lvl_size(depth: int) -> int:
        base = random.randint(15, 120)
        return base + depth * random.randint(5, 40)

    def _price(self, i: int) -> float:
        return round(i * self.tick, 10)

    def _snapshot(self, ts: int) -> dict:
        bids = [[self._price(self.best_ask_i - 1 - d), self.bid_sz[d]]
                for d in range(BOOK_DEPTH)]
        asks = [[self._price(self.best_ask_i + d), self.ask_sz[d]]
                for d in range(BOOK_DEPTH)]
        return {"type": "snapshot", "ts": ts, "bids": bids, "asks": asks}

    def stop(self) -> None:
        self._stop.set()

    async def events(self) -> AsyncIterator[dict]:
        yield {"type": "status", "state": "running", "mode": "demo",
               "symbol": self.symbol, "detail": "synthetic data"}
        next_sweep = time.monotonic() + random.uniform(3.0, 8.0)
        while not self._stop.is_set():
            ts = time.time_ns()

            # jitter resting liquidity
            for arr in (self.bid_sz, self.ask_sz):
                for d in range(BOOK_DEPTH):
                    arr[d] = max(5, arr[d] + random.randint(-15, 15))

            # occasional 1-tick drift without prints
            if random.random() < 0.06:
                self._shift(1 if random.random() < 0.5 else -1)

            yield self._snapshot(ts)

            # background single-level trades at the touch
            if random.random() < 0.55:
                for ev in self._small_trade(ts):
                    yield ev
                yield self._snapshot(ts)

            # periodic sweep burst
            if time.monotonic() >= next_sweep:
                for ev in self._sweep_burst(ts):
                    yield ev
                yield self._snapshot(ts)
                next_sweep = time.monotonic() + random.uniform(3.0, 9.0)

            await asyncio.sleep(0.1)

    # -- internals ---------------------------------------------------------

    def _shift(self, direction: int) -> None:
        """Move the whole book by one tick, refreshing the vacated edge."""
        self.best_ask_i += direction
        if direction > 0:
            self.ask_sz.pop(0)
            self.ask_sz.append(self._lvl_size(BOOK_DEPTH))
            self.bid_sz.insert(0, self._lvl_size(0))
            self.bid_sz.pop()
        else:
            self.bid_sz.pop(0)
            self.bid_sz.append(self._lvl_size(BOOK_DEPTH))
            self.ask_sz.insert(0, self._lvl_size(0))
            self.ask_sz.pop()

    def _small_trade(self, ts: int):
        side = "B" if random.random() < 0.5 else "A"
        if side == "B":
            px = self._price(self.best_ask_i)
            sz = random.randint(1, max(2, self.ask_sz[0] // 3))
            self.ask_sz[0] = max(1, self.ask_sz[0] - sz)
        else:
            px = self._price(self.best_ask_i - 1)
            sz = random.randint(1, max(2, self.bid_sz[0] // 3))
            self.bid_sz[0] = max(1, self.bid_sz[0] - sz)
        yield {"type": "trade", "ts": ts, "px": px, "sz": sz, "side": side}

    def _sweep_burst(self, ts: int):
        side = "B" if random.random() < 0.5 else "A"
        levels = random.choices([2, 3, 4, 5, 6], weights=[38, 28, 18, 10, 6])[0]
        for k in range(levels):
            if side == "B":
                px = self._price(self.best_ask_i + k)
                lvl = self.ask_sz[k]
            else:
                px = self._price(self.best_ask_i - 1 - k)
                lvl = self.bid_sz[k]
            sz = lvl if k < levels - 1 else max(1, int(lvl * random.uniform(0.3, 0.9)))
            if k == levels - 1:      # last level only partially consumed
                if side == "B":
                    self.ask_sz[k] = max(1, self.ask_sz[k] - sz)
                else:
                    self.bid_sz[k] = max(1, self.bid_sz[k] - sz)
            # fills of one order share the same matching-engine timestamp
            yield {"type": "trade", "ts": ts, "px": px, "sz": sz, "side": side}
        # the book actually moves through the consumed levels
        for _ in range(levels - 1):
            self._shift(1 if side == "B" else -1)


# ---------------------------------------------------------------------------
# Databento feeds
# ---------------------------------------------------------------------------

def _get_key(explicit: Optional[str]) -> str:
    key = explicit or os.getenv("DATABENTO_API_KEY", "")
    if not key:
        raise RuntimeError(
            "No Databento API key. Set the DATABENTO_API_KEY environment "
            "variable (or provide one in the UI).")
    return key


def _import_databento():
    try:
        import databento as db  # noqa: WPS433 (heavy optional dependency)
        return db
    except ImportError as exc:
        raise RuntimeError(
            "The 'databento' Python package is not installed on the server "
            "(pip install databento). Demo mode works without it.") from exc


def _record_events(rec, symbol_map: dict) -> list:
    """Convert a databento DBN record into our wire events."""
    import databento_dbn as dbn

    events = []
    if isinstance(rec, dbn.TradeMsg):
        px = _px(rec.price)
        if px is not None:
            # rec.side is a databento_dbn.Side enum: normalize to a plain
            # str at the boundary or json.dumps chokes downstream
            side = str(rec.side)
            side = side if side in ("B", "A") else "N"
            events.append({"type": "trade", "ts": rec.ts_event, "px": px,
                           "sz": rec.size, "side": side})
    elif isinstance(rec, dbn.MBP10Msg):
        bids, asks = [], []
        for lvl in rec.levels:
            bpx, apx = _px(lvl.bid_px), _px(lvl.ask_px)
            if bpx is not None:
                bids.append([bpx, lvl.bid_sz])
            if apx is not None:
                asks.append([apx, lvl.ask_sz])
        events.append({"type": "snapshot", "ts": rec.ts_event,
                       "bids": bids, "asks": asks})
    elif isinstance(rec, dbn.SymbolMappingMsg):
        # continuous contracts roll by re-mapping to a new instrument_id
        symbol_map[rec.instrument_id] = rec.stype_out_symbol
        events.append({"type": "status", "state": "mapped",
                       "detail": f"instrument {rec.stype_out_symbol}"})
    elif isinstance(rec, dbn.SystemMsg):
        if not getattr(rec, "is_heartbeat", False):
            events.append({"type": "status", "state": "info",
                           "detail": str(rec.msg)})
    elif isinstance(rec, dbn.ErrorMsg):
        events.append({"type": "error", "message": str(rec.err)})
    return events


class LiveFeed:
    """Databento live gateway → trades + mbp-10 for one symbol."""

    def __init__(self, dataset: str, symbol: str, stype_in: str = "continuous",
                 api_key: Optional[str] = None):
        self.dataset = dataset
        self.symbol = symbol
        self.stype_in = stype_in
        self.api_key = _get_key(api_key)
        self._client = None

    def stop(self) -> None:
        client = self._client
        if client is not None:
            try:
                client.stop()
            except Exception:
                pass

    def _connect(self):
        """Create the client and subscribe. Runs in a worker thread: the
        first subscribe() blocks on TCP connect + CRAM auth (up to tens of
        seconds) and must not stall the server's event loop."""
        db = _import_databento()
        client = db.Live(key=self.api_key, reconnect_policy="reconnect")
        for schema in ("trades", "mbp-10"):
            client.subscribe(dataset=self.dataset, schema=schema,
                             stype_in=self.stype_in, symbols=[self.symbol])
        return client

    async def events(self) -> AsyncIterator[dict]:
        yield {"type": "status", "state": "loading", "mode": "live",
               "symbol": self.symbol,
               "detail": f"connecting to databento {self.dataset}…"}
        client = await asyncio.to_thread(self._connect)
        self._client = client
        yield {"type": "status", "state": "running", "mode": "live",
               "symbol": self.symbol,
               "detail": f"databento live {self.dataset}"}
        symbol_map: dict = {}
        try:
            async for rec in client:
                for ev in _record_events(rec, symbol_map):
                    yield ev
        finally:
            self.stop()
        # reaching here means the gateway closed the session cleanly —
        # tell the UI instead of leaving it "running" over a frozen chart
        yield {"type": "error",
               "message": "live feed disconnected by the gateway"}


class ReplayFeed:
    """Databento historical range, replayed against a wall clock."""

    def __init__(self, dataset: str, symbol: str, start: str, end: str,
                 stype_in: str = "continuous", speed: float = 1.0,
                 api_key: Optional[str] = None, limit: int = 2_000_000):
        self.dataset = dataset
        self.symbol = symbol
        self.start = start
        self.end = end
        self.stype_in = stype_in
        self.speed = max(0.1, min(1000.0, speed))
        self.api_key = _get_key(api_key)
        self.limit = limit
        self._stop = asyncio.Event()

    def stop(self) -> None:
        self._stop.set()

    def _fetch(self):
        db = _import_databento()
        client = db.Historical(key=self.api_key)
        per_schema = {}
        for schema in ("trades", "mbp-10"):
            store = client.timeseries.get_range(
                dataset=self.dataset, schema=schema,
                symbols=[self.symbol], stype_in=self.stype_in,
                start=self.start, end=self.end, limit=self.limit)
            per_schema[schema] = [r for r in store
                                  if hasattr(r, "ts_event")]
        # a schema that hit the record limit stops mid-range; trim the
        # others to the same instant so the replay stays synchronized
        truncated = [s for s, recs in per_schema.items()
                     if len(recs) >= self.limit]
        cutoff = None
        if truncated:
            cutoff = min(per_schema[s][-1].ts_event for s in truncated)
            per_schema = {s: [r for r in recs if r.ts_event <= cutoff]
                          for s, recs in per_schema.items()}
        recs = [r for recs in per_schema.values() for r in recs]
        recs.sort(key=lambda r: r.ts_event)
        return recs, truncated, cutoff

    async def events(self) -> AsyncIterator[dict]:
        yield {"type": "status", "state": "loading", "mode": "replay",
               "symbol": self.symbol,
               "detail": f"downloading {self.start} → {self.end}"}
        recs, truncated, cutoff = await asyncio.to_thread(self._fetch)
        if not recs:
            yield {"type": "error", "message": "No records in range "
                   f"{self.start} → {self.end} for {self.symbol}"}
            return
        if truncated:
            yield {"type": "status", "state": "info",
                   "detail": f"record limit hit for {', '.join(truncated)}; "
                             f"replay trimmed to {cutoff} ns — "
                             "request a shorter range for full coverage"}
        yield {"type": "status", "state": "running", "mode": "replay",
               "symbol": self.symbol,
               "detail": f"replaying {len(recs)} records at {self.speed}x"}
        symbol_map: dict = {}
        t0_data = recs[0].ts_event
        t0_wall = time.monotonic()
        for rec in recs:
            if self._stop.is_set():
                break
            due = t0_wall + (rec.ts_event - t0_data) / 1e9 / self.speed
            delay = due - time.monotonic()
            if delay > 0:
                # cap so a quiet stretch never freezes the stream for minutes
                await asyncio.sleep(min(delay, 2.0))
            for ev in _record_events(rec, symbol_map):
                yield ev
        yield {"type": "status", "state": "finished", "mode": "replay",
               "symbol": self.symbol, "detail": "replay complete"}


def make_feed(cfg: dict):
    """Build a feed from the client's start message."""
    mode = cfg.get("mode", "demo")
    symbol = (cfg.get("symbol") or "ES.v.0").strip()
    dataset = (cfg.get("dataset") or "GLBX.MDP3").strip()
    stype_in = (cfg.get("stype_in") or "continuous").strip()
    if mode == "demo":
        return DemoFeed(symbol="DEMO.ES")
    if mode == "live":
        return LiveFeed(dataset=dataset, symbol=symbol, stype_in=stype_in,
                        api_key=cfg.get("api_key") or None)
    if mode == "replay":
        start = cfg.get("start")
        end = cfg.get("end")
        if not start or not end:
            raise RuntimeError("Replay mode needs 'start' and 'end' (ISO 8601).")
        return ReplayFeed(dataset=dataset, symbol=symbol, start=start, end=end,
                          stype_in=stype_in, speed=float(cfg.get("speed", 1.0)),
                          api_key=cfg.get("api_key") or None)
    raise RuntimeError(f"Unknown mode '{mode}'")
