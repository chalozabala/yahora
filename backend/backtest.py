"""Sweep backtest engine.

Runs the same SweepDetector used by the live app over N days of historical
trades and measures what price does after each sweep (forward move at
several horizons, in the direction of the sweep).

Design goals:
  * one streaming pass per day — never holds a day of trades in memory;
  * downloaded Databento data is cached on disk per (dataset, symbol, day)
    so a re-run costs nothing;
  * a deterministic synthetic source ("demo") exercises the whole pipeline
    without credentials;
  * progress + cancellation callbacks so a web UI can show a live bar.

Only the `trades` schema is needed (detection and outcomes both work on
prints), which keeps 10-day pulls small and cheap compared to book data.
"""

from __future__ import annotations

import datetime as dt
import heapq
import itertools
import math
import os
import random
import statistics
from pathlib import Path
from typing import Callable, Iterator, List, Optional, Tuple

from sweeps import SweepDetector, Trade

PRICE_SCALE = 1e-9
UNDEF_PRICE = 2 ** 63 - 1

HORIZONS: List[Tuple[str, int]] = [       # label, ns after sweep end
    ("10s", 10 * 10 ** 9),
    ("1m", 60 * 10 ** 9),
    ("5m", 5 * 60 * 10 ** 9),
    ("15m", 15 * 60 * 10 ** 9),
]

REPO_ROOT = Path(__file__).resolve().parent.parent
CACHE_DIR = Path(os.getenv("SWEEPS_CACHE_DIR", REPO_ROOT / "data_cache"))
RESULTS_DIR = Path(os.getenv("SWEEPS_RESULTS_DIR", REPO_ROOT / "backtest_results"))

MAX_SWEEP_ROWS = 20_000        # cap on per-sweep rows kept in the report


class Cancelled(Exception):
    pass


# ---------------------------------------------------------------------------
# Forward-outcome tracking (single pass, heap of pending deadlines)
# ---------------------------------------------------------------------------

class OutcomeTracker:
    """Resolves, for each sweep, the first trade price at/after each
    horizon deadline. O(log n) per event via a deadline heap."""

    def __init__(self, horizons: List[Tuple[str, int]]):
        self.horizons = horizons
        self._heap: list = []
        self._seq = itertools.count()

    def add(self, row: dict) -> None:
        row["fwd_px"] = [None] * len(self.horizons)
        for i, (_, ns) in enumerate(self.horizons):
            heapq.heappush(self._heap,
                           (row["ts"] + ns, next(self._seq), i, row))

    def on_trade(self, ts: int, px: float) -> None:
        while self._heap and self._heap[0][0] <= ts:
            _, _, i, row = heapq.heappop(self._heap)
            row["fwd_px"][i] = px

    @property
    def unresolved(self) -> int:
        return len(self._heap)


# ---------------------------------------------------------------------------
# Data sources: (ts_ns, price, size, side) generators per day
# ---------------------------------------------------------------------------

class SyntheticSource:
    """Deterministic synthetic tape: random walk + injected sweep bursts.
    Same day -> same data, so demo backtests are reproducible."""

    trades_per_day = 60_000

    def __init__(self, symbol: str = "DEMO.ES", tick: float = 0.25):
        self.symbol = symbol
        self.tick = tick

    def day_trades(self, day: dt.date) -> Iterator[Tuple[int, float, int, str]]:
        rng = random.Random(f"{self.symbol}:{day.isoformat()}")
        t0 = int(dt.datetime.combine(day, dt.time(13, 30),
                                     dt.timezone.utc).timestamp() * 1e9)
        session_ns = int(6.5 * 3600 * 1e9)
        step = session_ns // self.trades_per_day
        px_i = round(6400 / self.tick) + rng.randint(-200, 200)
        ts = t0
        n = 0
        while n < self.trades_per_day:
            ts += rng.randint(step // 2, step * 3 // 2)
            if rng.random() < 0.004:            # sweep burst
                side = "B" if rng.random() < 0.5 else "A"
                levels = rng.choices([2, 3, 4, 5, 6],
                                     weights=[35, 30, 18, 11, 6])[0]
                for k in range(levels):
                    px_i += 1 if side == "B" else -1
                    sz = rng.randint(25, 220)
                    yield ts, px_i * self.tick, sz, side
                    n += 1
                # mild continuation drift after the sweep
                px_i += (1 if side == "B" else -1) * rng.choice([0, 0, 1])
            else:
                px_i += rng.choice([-1, 0, 0, 0, 1])
                side = "B" if rng.random() < 0.5 else "A"
                yield ts, px_i * self.tick, rng.randint(1, 25), side
                n += 1


class DatabentoSource:
    """Day-by-day trades from Databento historical, cached on disk."""

    def __init__(self, dataset: str, symbol: str, stype_in: str,
                 api_key: Optional[str] = None):
        self.dataset = dataset
        self.symbol = symbol
        self.stype_in = stype_in
        self.api_key = api_key or os.getenv("DATABENTO_API_KEY", "")
        if not self.api_key:
            raise RuntimeError(
                "No Databento API key configured (DATABENTO_API_KEY). "
                "Use demo mode to try the backtest without one.")
        self._client = None

    def _db(self):
        import databento as db
        return db

    def client(self):
        if self._client is None:
            self._client = self._db().Historical(key=self.api_key)
        return self._client

    def cache_path(self, day: dt.date) -> Path:
        safe = "".join(c if c.isalnum() or c in ".-" else "_"
                       for c in f"{self.dataset}_{self.symbol}_{self.stype_in}")
        return CACHE_DIR / safe / f"trades_{day.isoformat()}.dbn.zst"

    def is_cached(self, day: dt.date) -> bool:
        return self.cache_path(day).exists()

    def estimate_day_cost(self, day: dt.date) -> Optional[float]:
        if self.is_cached(day):
            return 0.0
        try:
            return float(self.client().metadata.get_cost(
                dataset=self.dataset, symbols=[self.symbol],
                stype_in=self.stype_in, schema="trades",
                start=day.isoformat(),
                end=(day + dt.timedelta(days=1)).isoformat()))
        except Exception:
            return None

    def _store(self, day: dt.date):
        db = self._db()
        path = self.cache_path(day)
        if path.exists():
            return db.DBNStore.from_file(str(path))
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".part")
        try:
            store = self.client().timeseries.get_range(
                dataset=self.dataset, schema="trades",
                symbols=[self.symbol], stype_in=self.stype_in,
                start=day.isoformat(),
                end=(day + dt.timedelta(days=1)).isoformat(),
                path=str(tmp))
        except Exception:
            tmp.unlink(missing_ok=True)
            raise
        tmp.rename(path)
        return store

    def day_trades(self, day: dt.date) -> Iterator[Tuple[int, float, int, str]]:
        try:
            store = self._store(day)
        except Exception as exc:
            msg = str(exc).lower()
            # out-of-range / empty days (weekends, holidays) are not errors
            if any(k in msg for k in ("no data", "not found", "422",
                                      "data_start", "data_end")):
                return
            raise
        for rec in store:
            price = getattr(rec, "price", None)
            if price is None or price == UNDEF_PRICE:
                continue
            side = str(getattr(rec, "side", "N"))
            yield (rec.ts_event, price * PRICE_SCALE, rec.size,
                   side if side in ("B", "A") else "N")


def make_source(cfg: dict):
    if cfg.get("mode", "demo") == "demo":
        return SyntheticSource()
    return DatabentoSource(
        dataset=(cfg.get("dataset") or "GLBX.MDP3").strip(),
        symbol=(cfg.get("symbol") or "ES.v.0").strip(),
        stype_in=(cfg.get("stype_in") or "continuous").strip(),
        api_key=cfg.get("api_key") or None)


# ---------------------------------------------------------------------------
# Backtest run
# ---------------------------------------------------------------------------

def backtest_days(cfg: dict) -> List[dt.date]:
    days = int(cfg.get("days", 10))
    days = max(1, min(31, days))
    end_s = cfg.get("end_date")
    end = (dt.date.fromisoformat(end_s) if end_s
           else dt.date.today() - dt.timedelta(days=1))
    return [end - dt.timedelta(days=i) for i in range(days - 1, -1, -1)]


def estimate_cost(cfg: dict) -> dict:
    """Pre-flight: what will this pull cost, given the local cache?"""
    if cfg.get("mode", "demo") == "demo":
        return {"mode": "demo", "usd": 0.0, "days": len(backtest_days(cfg)),
                "cached_days": 0, "unknown": False}
    src = make_source(cfg)
    days = backtest_days(cfg)
    cached = sum(1 for d in days if src.is_cached(d))
    total, unknown = 0.0, False
    for d in days:
        c = src.estimate_day_cost(d)
        if c is None:
            unknown = True
        else:
            total += c
    return {"mode": "databento", "usd": round(total, 2), "days": len(days),
            "cached_days": cached, "unknown": unknown}


def _detector_from(cfg: dict) -> SweepDetector:
    p = cfg.get("params") or {}
    return SweepDetector(
        window_ns=int(float(p.get("window_ms", 200)) * 1e6),
        min_levels=int(p.get("min_levels", 3)),
        min_size=int(p.get("min_size", 40)),
        auto_size=bool(p.get("auto_size", False)),
        sd_interval_ns=int(float(p.get("sd_interval_min", 5)) * 60 * 1e9),
        sd_multiplier=float(p.get("sd_multiplier", 6.0)),
    )


def _stats(moves: List[float]) -> dict:
    if not moves:
        return {"n": 0, "mean": None, "median": None, "std": None,
                "hit_rate": None}
    return {
        "n": len(moves),
        "mean": round(statistics.fmean(moves), 3),
        "median": round(statistics.median(moves), 3),
        "std": round(statistics.pstdev(moves), 3) if len(moves) > 1 else 0.0,
        "hit_rate": round(sum(1 for m in moves if m > 0) / len(moves), 4),
    }


def run_backtest(cfg: dict,
                 progress: Optional[Callable[[dict], None]] = None,
                 is_cancelled: Optional[Callable[[], bool]] = None) -> dict:
    """Run the full backtest synchronously (call from a worker thread)."""
    def report_progress(**kw):
        if progress:
            progress(kw)

    def check_cancel():
        if is_cancelled and is_cancelled():
            raise Cancelled()

    source = make_source(cfg)
    detector = _detector_from(cfg)
    tracker = OutcomeTracker(HORIZONS)
    days = backtest_days(cfg)

    sweep_rows: List[dict] = []
    day_summaries: List[dict] = []
    tick = float(cfg.get("tick") or 0) or None
    min_diff = math.inf
    last_px: Optional[float] = None
    total_trades = 0
    overflowed = False

    def record_sweep(sw, day: dt.date):
        nonlocal overflowed
        row = {"ts": sw.ts_end, "date": day.isoformat(), "side": sw.side,
               "size": sw.total_size, "levels": sw.levels,
               "vwap": round(sw.vwap, 6), "px_min": sw.price_min,
               "px_max": sw.price_max, "trades": sw.trade_count}
        tracker.add(row)
        if len(sweep_rows) < MAX_SWEEP_ROWS:
            sweep_rows.append(row)
        else:
            overflowed = True

    for di, day in enumerate(days):
        check_cancel()
        report_progress(phase="day", day=day.isoformat(), day_i=di,
                        days=len(days),
                        pct=round(di / len(days) * 100, 1),
                        detail=f"processing {day.isoformat()}")
        day_trades = 0
        day_sweeps = {"B": 0, "A": 0}
        day_volume = {"B": 0, "A": 0}
        for ts, px, sz, side in source.day_trades(day):
            day_trades += 1
            total_trades += 1
            if total_trades % 50_000 == 0:
                check_cancel()
                report_progress(phase="day", day=day.isoformat(), day_i=di,
                                days=len(days),
                                pct=round(di / len(days) * 100, 1),
                                detail=f"{day.isoformat()} · "
                                       f"{total_trades:,} trades · "
                                       f"{len(sweep_rows):,} sweeps")
            if last_px is not None:
                d = abs(px - last_px)
                if 1e-9 < d < min_diff:
                    min_diff = d
            last_px = px
            tracker.on_trade(ts, px)
            for sw in detector.on_trade(Trade(ts=ts, price=px, size=sz,
                                              side=side)):
                record_sweep(sw, day)
                day_sweeps[sw.side] += 1
                day_volume[sw.side] += sw.total_size
        # a day boundary is a hard gap: close anything still open
        for sw in detector.finalize():
            record_sweep(sw, day)
            day_sweeps[sw.side] += 1
            day_volume[sw.side] += sw.total_size
        day_summaries.append({"date": day.isoformat(), "trades": day_trades,
                              "sweeps_buy": day_sweeps["B"],
                              "sweeps_sell": day_sweeps["A"],
                              "volume_buy": day_volume["B"],
                              "volume_sell": day_volume["A"]})

    if tick is None:
        tick = min_diff if math.isfinite(min_diff) else 1.0

    report_progress(phase="aggregate", pct=99.0, detail="computing statistics")

    # ---- aggregate ----
    horizon_stats = []
    for i, (label, _) in enumerate(HORIZONS):
        per_side = {}
        alls: List[float] = []
        for side in ("B", "A"):
            moves = []
            for row in sweep_rows:
                if row["side"] != side or row["fwd_px"][i] is None:
                    continue
                mv = (row["fwd_px"][i] - row["vwap"]) / tick
                mv = mv if side == "B" else -mv     # in sweep direction
                moves.append(mv)
            per_side[side] = _stats(moves)
            alls.extend(moves)
        horizon_stats.append({"label": label, "buy": per_side["B"],
                              "sell": per_side["A"], "all": _stats(alls)})

    n_buy = sum(1 for r in sweep_rows if r["side"] == "B")
    n_sell = len(sweep_rows) - n_buy
    sizes = [r["size"] for r in sweep_rows]
    top = sorted(sweep_rows, key=lambda r: -r["size"])[:20]

    for row in sweep_rows:   # ticks version of forward moves, for the CSV
        row["fwd_ticks"] = [
            None if p is None else round((p - row["vwap"]) / tick *
                                         (1 if row["side"] == "B" else -1), 2)
            for p in row["fwd_px"]]

    notes = []
    if overflowed:
        notes.append(f"More than {MAX_SWEEP_ROWS:,} sweeps detected; "
                     "per-sweep rows were capped (statistics use the "
                     "captured rows only). Raise the size filter.")
    if tracker.unresolved:
        notes.append("Some horizon outcomes near the end of each day had "
                     "no later trade and were excluded.")
    days_with_data = sum(1 for d in day_summaries if d["trades"])
    if days_with_data < len(days):
        notes.append(f"{len(days) - days_with_data} of {len(days)} calendar "
                     "days had no data (weekends/holidays).")

    report = {
        "config": {k: cfg.get(k) for k in
                   ("mode", "dataset", "symbol", "stype_in", "days",
                    "end_date", "params")},
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "tick": tick,
        "horizon_labels": [h[0] for h in HORIZONS],
        "totals": {"sweeps": len(sweep_rows), "buy": n_buy, "sell": n_sell,
                   "trades": total_trades, "days": len(days),
                   "days_with_data": days_with_data,
                   "avg_sweep_size": round(statistics.fmean(sizes), 1)
                   if sizes else None,
                   "max_sweep_size": max(sizes) if sizes else None},
        "daily": day_summaries,
        "horizons": horizon_stats,
        "top_sweeps": top,
        "sweeps": sweep_rows,
        "notes": notes,
    }
    report_progress(phase="done", pct=100.0, detail="finished")
    return report


def report_to_csv(report: dict) -> str:
    labels = report["horizon_labels"]
    head = ["date", "time_utc", "side", "size", "levels", "vwap",
            "px_min", "px_max", "trades"]
    head += [f"move_ticks_{lb}" for lb in labels]
    lines = [",".join(head)]
    for r in report["sweeps"]:
        t = dt.datetime.fromtimestamp(r["ts"] / 1e9,
                                      dt.timezone.utc).strftime("%H:%M:%S.%f")
        row = [r["date"], t, r["side"], str(r["size"]), str(r["levels"]),
               f'{r["vwap"]:.6f}', f'{r["px_min"]}', f'{r["px_max"]}',
               str(r["trades"])]
        row += ["" if v is None else str(v) for v in r.get("fwd_ticks", [])]
        lines.append(",".join(row))
    return "\n".join(lines) + "\n"
