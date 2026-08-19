"""Sweep backtest engine.

Runs the same SweepDetector used by the live app over N days of historical
trades and measures what price does after each sweep (forward move at
several horizons, in the direction of the sweep).

Design goals:
  * one streaming pass — never holds a day of trades in memory;
  * downloaded Databento data is cached on disk per (dataset, symbol, day)
    so a re-run costs nothing;
  * a deterministic synthetic source ("demo") exercises the whole pipeline
    without credentials;
  * progress + cancellation callbacks so a web UI can show a live bar.

Correctness notes (the subtle parts):
  * Forward outcomes resolve with the first trade at/after each deadline,
    BUT a resolution that lands too far past the deadline (session close,
    weekend, maintenance halt) is discarded — otherwise the next session's
    open would be recorded as a "1m move".
  * Data files are split at 00:00 UTC, which is mid-session for CME, so
    chains are NOT force-closed at day boundaries; the detector's own gap
    rule closes them when the next print arrives. finalize() runs once,
    at the very end. Sweeps are dated by their own timestamp.

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
import threading
import uuid
from pathlib import Path
from typing import Callable, Iterator, List, Optional, Tuple

from feeds import normalize_symbol
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
TOP_SWEEPS = 20


class Cancelled(Exception):
    pass


# ---------------------------------------------------------------------------
# Forward-outcome tracking (single pass, heap of pending deadlines)
# ---------------------------------------------------------------------------

class OutcomeTracker:
    """Resolves, for each sweep, the first trade price at/after each
    horizon deadline — unless that trade is too far past the deadline
    (max(2× horizon, 30 s)), which means the deadline fell into a session
    gap; those outcomes are discarded, not polluted by the next open."""

    def __init__(self, horizons: List[Tuple[str, int]]):
        self.horizons = horizons
        self._heap: list = []
        self._seq = itertools.count()
        self.expired = 0               # deadlines that fell into a gap

    def _max_stale_ns(self, horizon_ns: int) -> int:
        return max(2 * horizon_ns, 30 * 10 ** 9)

    def add(self, row: dict) -> None:
        row["fwd_px"] = [None] * len(self.horizons)
        for i, (_, ns) in enumerate(self.horizons):
            heapq.heappush(self._heap,
                           (row["ts"] + ns, next(self._seq), i, row))

    def on_trade(self, ts: int, px: float) -> None:
        while self._heap and self._heap[0][0] <= ts:
            deadline, _, i, row = heapq.heappop(self._heap)
            if ts - deadline <= self._max_stale_ns(self.horizons[i][1]):
                row["fwd_px"][i] = px
            else:
                self.expired += 1

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
                levels = min(levels, self.trades_per_day - n)
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


# one lock per cache file: concurrent jobs must not download the same
# day twice (double billing) or clobber each other's temp files
_dl_guard = threading.Lock()
_dl_locks: dict = {}


def _path_lock(path: Path) -> threading.Lock:
    with _dl_guard:
        return _dl_locks.setdefault(str(path), threading.Lock())


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
                "Falta la clave de Databento. Corré datos-reales.bat "
                "(Windows) o ./datos-reales.sh (Mac/Linux) y pegá tu clave "
                "cuando te la pida. El modo Demo funciona sin clave.")
        self._client = None

    def _db(self):
        try:
            import databento as db
        except ImportError as exc:
            raise RuntimeError(
                "Falta instalar el módulo de datos reales. Corré "
                "datos-reales.bat (Windows) o ./datos-reales.sh "
                "(Mac/Linux) y volvé a intentar.") from exc
        return db

    def client(self):
        if self._client is None:
            self._client = self._db().Historical(key=self.api_key)
        return self._client

    @staticmethod
    def _looks_like_no_data(exc: Exception) -> bool:
        """Only availability-range errors count as 'empty day'. Anything
        else (bad symbol, wrong dataset, auth) must surface to the user —
        never be silently converted into a weekend."""
        msg = str(exc).lower()
        return any(k in msg for k in ("no data", "data_start", "data_end"))

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
        except Exception as exc:
            if self._looks_like_no_data(exc):
                return 0.0
            raise RuntimeError(
                f"No se pudo estimar el costo de datos: {exc}") from exc

    def _get_range(self, day: dt.date, path: Optional[str] = None):
        return self.client().timeseries.get_range(
            dataset=self.dataset, schema="trades",
            symbols=[self.symbol], stype_in=self.stype_in,
            start=day.isoformat(),
            end=(day + dt.timedelta(days=1)).isoformat(),
            path=path)

    def _store(self, day: dt.date):
        db = self._db()
        path = self.cache_path(day)
        # an incomplete day (today / future) must never be cached, or the
        # partial file would be reused forever as if it were the full day
        complete = day < dt.datetime.now(dt.timezone.utc).date()
        with _path_lock(path):
            if path.exists():
                return db.DBNStore.from_file(str(path))
            if not complete:
                return self._get_range(day)
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.parent / f"{path.name}.{uuid.uuid4().hex[:8]}.part"
            try:
                self._get_range(day, path=str(tmp))
                tmp.rename(path)
            except Exception:
                tmp.unlink(missing_ok=True)
                raise
            # re-open from the final path: the store returned by get_range
            # is bound to the temp file name we just renamed away
            return db.DBNStore.from_file(str(path))

    def day_trades(self, day: dt.date) -> Iterator[Tuple[int, float, int, str]]:
        try:
            store = self._store(day)
        except Exception as exc:
            if self._looks_like_no_data(exc):
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
        dataset=(cfg.get("dataset") or "GLBX.MDP3").strip().upper(),
        symbol=normalize_symbol(cfg.get("symbol") or "ES.v.0"),
        stype_in=(cfg.get("stype_in") or "continuous").strip(),
        api_key=cfg.get("api_key") or None)


# ---------------------------------------------------------------------------
# Backtest run
# ---------------------------------------------------------------------------

def backtest_days(cfg: dict) -> List[dt.date]:
    days = int(cfg.get("days", 10))
    days = max(1, min(31, days))
    yesterday = dt.datetime.now(dt.timezone.utc).date() - dt.timedelta(days=1)
    end_s = cfg.get("end_date")
    end = dt.date.fromisoformat(end_s) if end_s else yesterday
    end = min(end, yesterday)       # today's data is incomplete: never use it
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


def _date_of(ts_ns: int) -> str:
    return dt.datetime.fromtimestamp(ts_ns / 1e9,
                                     dt.timezone.utc).date().isoformat()


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
    daily = {d.isoformat(): {"date": d.isoformat(), "trades": 0,
                             "sweeps_buy": 0, "sweeps_sell": 0,
                             "volume_buy": 0, "volume_sell": 0}
             for d in days}
    totals = {"sweeps": 0, "buy": 0, "sell": 0, "size_sum": 0, "size_max": 0}
    top_heap: list = []               # min-heap of (size, seq, row), size TOP
    top_seq = itertools.count()
    tick = float(cfg.get("tick") or 0) or None
    min_diff = math.inf
    last_px: Optional[float] = None
    total_trades = 0
    overflowed = False

    def record_sweep(sw):
        nonlocal overflowed
        date = _date_of(sw.ts_end)
        row = {"ts": sw.ts_end, "date": date, "side": sw.side,
               "size": sw.total_size, "levels": sw.levels,
               "vwap": round(sw.vwap, 6), "px_min": sw.price_min,
               "px_max": sw.price_max, "trades": sw.trade_count}
        tracker.add(row)
        totals["sweeps"] += 1
        totals["buy" if sw.side == "B" else "sell"] += 1
        totals["size_sum"] += sw.total_size
        totals["size_max"] = max(totals["size_max"], sw.total_size)
        d = daily.get(date)
        if d is not None:
            d["sweeps_buy" if sw.side == "B" else "sweeps_sell"] += 1
            d["volume_buy" if sw.side == "B" else "volume_sell"] += sw.total_size
        if len(top_heap) < TOP_SWEEPS:
            heapq.heappush(top_heap, (sw.total_size, next(top_seq), row))
        elif sw.total_size > top_heap[0][0]:
            heapq.heapreplace(top_heap, (sw.total_size, next(top_seq), row))
        if len(sweep_rows) < MAX_SWEEP_ROWS:
            sweep_rows.append(row)
        else:
            overflowed = True

    for di, day in enumerate(days):
        check_cancel()
        report_progress(phase="day", day=day.isoformat(), day_i=di,
                        days=len(days),
                        pct=round(di / len(days) * 100, 1),
                        detail=f"procesando {day.isoformat()}")
        day_key = day.isoformat()
        first = True
        for ts, px, sz, side in source.day_trades(day):
            if first:
                first = False
                check_cancel()      # react promptly after a long download
            daily[day_key]["trades"] += 1
            total_trades += 1
            if total_trades % 50_000 == 0:
                check_cancel()
                report_progress(phase="day", day=day_key, day_i=di,
                                days=len(days),
                                pct=round(di / len(days) * 100, 1),
                                detail=f"{day_key} · "
                                       f"{total_trades:,} trades · "
                                       f"{totals['sweeps']:,} sweeps")
            if last_px is not None:
                d = abs(px - last_px)
                if 1e-9 < d < min_diff:
                    min_diff = d
            last_px = px
            # order matters: close/emit sweeps FIRST so this same trade can
            # resolve a just-added deadline that is already in the past
            closed = detector.on_trade(Trade(ts=ts, price=px, size=sz,
                                             side=side))
            closed += detector.flush(ts)   # quiet opposite-side chains too
            for sw in closed:
                record_sweep(sw)
            tracker.on_trade(ts, px)
        # NOTE: no per-day finalize — 00:00 UTC is mid-session for CME; the
        # detector's gap rule closes chains when the next print arrives.

    for sw in detector.finalize():     # the feed truly ended
        record_sweep(sw)

    if tick is None:
        # prices are int64 * 1e-9, so the true tick is exact at 9 decimals
        tick = round(min_diff, 9) if math.isfinite(min_diff) else 1.0

    report_progress(phase="aggregate", pct=99.0,
                    detail="calculando estadísticas")

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

    top = [r for _, _, r in sorted(top_heap, key=lambda t: -t[0])]

    def add_fwd_ticks(row):
        row["fwd_ticks"] = [
            None if p is None else round((p - row["vwap"]) / tick *
                                         (1 if row["side"] == "B" else -1), 2)
            for p in row.get("fwd_px", [None] * len(HORIZONS))]

    for row in sweep_rows:
        add_fwd_ticks(row)
    for row in top:                    # top rows can be outside the cap
        if "fwd_ticks" not in row:
            add_fwd_ticks(row)

    notes = []
    if overflowed:
        notes.append(
            f"Se detectaron más de {MAX_SWEEP_ROWS:,} sweeps: las "
            "estadísticas de horizontes y el CSV cubren solo los primeros "
            f"{MAX_SWEEP_ROWS:,}; los totales y el gráfico por día cuentan "
            "todos. Subí el tamaño mínimo para un análisis completo.")
    dropped = tracker.expired + tracker.unresolved
    if dropped:
        notes.append(
            f"{dropped:,} mediciones de horizonte se descartaron por caer "
            "en huecos de sesión (cierres, fines de semana) o al final de "
            "los datos — así los gaps nocturnos no contaminan los números.")
    days_with_data = sum(1 for d in daily.values() if d["trades"])
    if days_with_data < len(days):
        notes.append(f"{len(days) - days_with_data} de {len(days)} días de "
                     "calendario no tuvieron datos (fines de semana o "
                     "feriados).")
    if cfg.get("mode") != "demo" and days_with_data == 0:
        raise RuntimeError(
            "Ningún día del rango devolvió datos. Revisá el símbolo, el "
            "dataset y las fechas (el mercado pudo estar cerrado).")

    sizes_n = totals["sweeps"]
    report = {
        "config": {k: cfg.get(k) for k in
                   ("mode", "dataset", "symbol", "stype_in", "days",
                    "end_date", "params")},
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "tick": tick,
        "horizon_labels": [h[0] for h in HORIZONS],
        "totals": {"sweeps": sizes_n, "buy": totals["buy"],
                   "sell": totals["sell"], "trades": total_trades,
                   "days": len(days), "days_with_data": days_with_data,
                   "avg_sweep_size": round(totals["size_sum"] / sizes_n, 1)
                   if sizes_n else None,
                   "max_sweep_size": totals["size_max"] or None},
        "daily": [daily[d.isoformat()] for d in days],
        "horizons": horizon_stats,
        "top_sweeps": top,
        "sweeps": sweep_rows,
        "notes": notes,
    }
    report_progress(phase="done", pct=100.0, detail="terminado")
    return report


def report_to_csv(report: dict) -> str:
    labels = report["horizon_labels"]
    head = ["date", "time_utc", "side", "size", "levels", "vwap",
            "px_min", "px_max", "trades"]
    head += [f"move_ticks_{lb}" for lb in labels]
    lines = ["sep=,", ",".join(head)]   # sep= hint keeps Excel happy in
    for r in report["sweeps"]:          # comma-decimal locales
        t = dt.datetime.fromtimestamp(r["ts"] / 1e9,
                                      dt.timezone.utc).strftime("%H:%M:%S.%f")
        row = [r["date"], t, r["side"], str(r["size"]), str(r["levels"]),
               f'{r["vwap"]:.6f}', format(r["px_min"], ".10g"),
               format(r["px_max"], ".10g"), str(r["trades"])]
        row += ["" if v is None else str(v) for v in r.get("fwd_ticks", [])]
        lines.append(",".join(row))
    return "\n".join(lines) + "\n"
