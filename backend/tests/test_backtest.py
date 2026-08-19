import datetime as dt
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import backtest as bt  # noqa: E402


def test_symbol_normalization_accepts_what_people_type():
    from feeds import normalize_symbol as n
    assert n("nq") == "NQ"                    # lo que escribio el usuario
    assert n("nq.v.0") == "NQ.v.0"
    assert n("es.V.0") == "ES.v.0"            # la regla de roll va minuscula
    assert n("  mnq.c.0 ") == "MNQ.c.0"
    assert n("esz5") == "ESZ5"
    assert n("es.fut") == "ES.FUT"
    assert n("6e.v.0") == "6E.v.0"


def test_backtest_source_normalizes_symbol(monkeypatch):
    monkeypatch.setenv("DATABENTO_API_KEY", "db-test")
    src = bt.make_source({"mode": "databento", "symbol": "nq.v.0",
                          "dataset": "glbx.mdp3"})
    assert src.symbol == "NQ.v.0"
    assert src.dataset == "GLBX.MDP3"


def test_outcome_tracker_resolves_first_trade_at_or_after_deadline():
    tr = bt.OutcomeTracker([("1s", 1_000_000_000), ("2s", 2_000_000_000)])
    row = {"ts": 1_000_000_000}
    tr.add(row)
    tr.on_trade(1_500_000_000, 100.0)      # before both deadlines: no-op
    assert row["fwd_px"] == [None, None]
    tr.on_trade(2_200_000_000, 101.0)      # first trade past the 1s mark
    assert row["fwd_px"] == [101.0, None]
    tr.on_trade(3_100_000_000, 99.0)
    assert row["fwd_px"] == [101.0, 99.0]
    assert tr.unresolved == 0


def test_backtest_days_window():
    days = bt.backtest_days({"days": 10, "end_date": "2026-08-14"})
    assert len(days) == 10
    assert days[0] == dt.date(2026, 8, 5)
    assert days[-1] == dt.date(2026, 8, 14)
    assert days == sorted(days)


def test_synthetic_source_is_deterministic():
    src = bt.SyntheticSource()
    day = dt.date(2026, 8, 10)
    a = list(src.day_trades(day))[:100]
    b = list(src.day_trades(day))[:100]
    assert a == b
    ts = [t[0] for t in a]
    assert ts == sorted(ts)


def test_demo_backtest_end_to_end():
    progress = []
    cfg = {"mode": "demo", "days": 2, "end_date": "2026-08-12",
           "params": {"min_levels": 2, "min_size": 50, "window_ms": 200}}
    report = bt.run_backtest(cfg, progress.append)
    t = report["totals"]
    assert t["days"] == 2 and t["days_with_data"] == 2
    assert t["trades"] == 2 * bt.SyntheticSource.trades_per_day
    assert t["sweeps"] > 10
    assert t["buy"] + t["sell"] == t["sweeps"]
    assert report["tick"] == 0.25
    assert [h["label"] for h in report["horizons"]] == \
        [lb for lb, _ in bt.HORIZONS]
    h10 = report["horizons"][0]
    assert h10["all"]["n"] > 0
    assert 0.0 <= h10["all"]["hit_rate"] <= 1.0
    assert progress and progress[-1]["phase"] == "done"
    # per-sweep rows carry forward moves in ticks for the CSV
    row = report["sweeps"][0]
    assert len(row["fwd_ticks"]) == len(bt.HORIZONS)
    # deterministic: same config -> same result
    report2 = bt.run_backtest(cfg)
    assert report2["totals"] == t


def test_demo_backtest_cancellation():
    import pytest
    with pytest.raises(bt.Cancelled):
        bt.run_backtest({"mode": "demo", "days": 3,
                         "end_date": "2026-08-12"},
                        is_cancelled=lambda: True)


def test_estimate_cost_demo_is_free():
    est = bt.estimate_cost({"mode": "demo", "days": 10})
    assert est["usd"] == 0.0 and est["days"] == 10


def test_csv_export_shape():
    cfg = {"mode": "demo", "days": 1, "end_date": "2026-08-12",
           "params": {"min_levels": 2, "min_size": 50}}
    report = bt.run_backtest(cfg)
    csv = bt.report_to_csv(report)
    lines = csv.strip().split("\n")
    assert lines[0] == "sep=,"          # Excel hint for comma-decimal locales
    assert len(lines) == 2 + report["totals"]["sweeps"]
    header = lines[1].split(",")
    assert header[:3] == ["date", "time_utc", "side"]
    assert header[-1] == "move_ticks_15m"
    assert all(len(l.split(",")) == len(header) for l in lines[2:])


def test_outcome_tracker_discards_resolution_across_session_gap():
    S = 1_000_000_000
    tr = bt.OutcomeTracker([("10s", 10 * S), ("15m", 900 * S)])
    row = {"ts": 0}
    tr.add(row)
    # next trade is 17 hours later (overnight gap): both deadlines are far
    # beyond their staleness cap, so neither may record the next open
    tr.on_trade(17 * 3600 * S, 6500.0)
    assert row["fwd_px"] == [None, None]
    assert tr.expired == 2
    # but a resolution within the cap (deadline + <=2x horizon) counts
    row2 = {"ts": 20 * 3600 * S}
    tr.add(row2)
    tr.on_trade(20 * 3600 * S + 25 * S, 6501.0)     # 10s deadline + 15s
    assert row2["fwd_px"][0] == 6501.0


class _TapeSource:
    """Fixed tape for wiring tests."""

    def __init__(self, tape):
        self.tape = tape                    # {date_iso: [(ts,px,sz,side)]}

    def day_trades(self, day):
        return iter(self.tape.get(day.isoformat(), []))


def test_gap_closing_trade_resolves_the_closed_sweep(monkeypatch):
    # buy chain at t0; the next buy print 15s later closes it AND is the
    # first trade at/after the 10s deadline — it must resolve that horizon
    S = 1_000_000_000
    t0 = int(dt.datetime(2026, 8, 12, 14, 0,
                         tzinfo=dt.timezone.utc).timestamp() * S)
    tape = {"2026-08-12": [
        (t0, 100.00, 60, "B"),
        (t0, 100.25, 60, "B"),
        (t0 + 15 * S, 101.00, 1, "B"),
    ]}
    monkeypatch.setattr(bt, "make_source", lambda cfg: _TapeSource(tape))
    report = bt.run_backtest({"mode": "demo", "days": 1,
                              "end_date": "2026-08-12", "tick": 0.25,
                              "params": {"min_levels": 2, "min_size": 0,
                                         "window_ms": 200}})
    rows = [r for r in report["sweeps"] if r["size"] == 120]
    assert len(rows) == 1
    # 10s horizon resolved by the closing print at 101.00, not lost
    assert rows[0]["fwd_px"][0] == 101.00


def test_totals_consistent_when_row_cap_overflows(monkeypatch):
    monkeypatch.setattr(bt, "MAX_SWEEP_ROWS", 5)
    cfg = {"mode": "demo", "days": 1, "end_date": "2026-08-12",
           "params": {"min_levels": 2, "min_size": 50}}
    report = bt.run_backtest(cfg)
    t = report["totals"]
    assert len(report["sweeps"]) == 5
    assert t["sweeps"] > 5                      # true count, not the cap
    assert t["buy"] + t["sell"] == t["sweeps"]
    daily_total = sum(d["sweeps_buy"] + d["sweeps_sell"]
                      for d in report["daily"])
    assert daily_total == t["sweeps"]           # tiles match the bar chart
    # top sweeps ranked over ALL sweeps, not just the capped rows
    assert report["top_sweeps"][0]["size"] == t["max_sweep_size"]
    assert any("primeros" in n for n in report["notes"])


def test_no_per_day_finalize_chain_spans_midnight(monkeypatch):
    # a chain printing right up to 23:59:59.99 must NOT be force-closed at
    # the file boundary; the next day's print continues/closes it normally
    S = 1_000_000_000
    t0 = int(dt.datetime(2026, 8, 11, 23, 59, 59, 900_000,
                         tzinfo=dt.timezone.utc).timestamp() * S)
    tape = {
        "2026-08-11": [(t0, 100.00, 60, "B"),
                       (t0 + int(0.05 * S), 100.25, 60, "B")],
        # 3rd print lands 50ms past midnight, within the 200ms window
        "2026-08-12": [(t0 + int(0.15 * S), 100.50, 60, "B"),
                       (t0 + 30 * S, 100.50, 1, "A")],
    }
    monkeypatch.setattr(bt, "make_source", lambda cfg: _TapeSource(tape))
    report = bt.run_backtest({"mode": "demo", "days": 2,
                              "end_date": "2026-08-12", "tick": 0.25,
                              "params": {"min_levels": 2, "min_size": 0,
                                         "window_ms": 200}})
    big = [r for r in report["sweeps"] if r["side"] == "B"]
    assert len(big) == 1                       # ONE sweep, not split in two
    assert big[0]["size"] == 180 and big[0]["levels"] == 3
    assert big[0]["date"] == "2026-08-12"      # dated by its own ts_end


def test_depth_normalization():
    from feeds import normalize_depth as n
    assert n(None) == "auto"
    assert n("auto") == "auto"
    assert n("mbp-1") == "mbp-1"
    assert n("MBP-10") == "mbp-10"
    assert n("none") == "none"
    assert n("cualquier-cosa") == "auto"     # nunca rompe


def test_live_feed_depth_is_configurable(monkeypatch):
    """El plan del usuario decide qué libro se puede pedir: MBP-10 requiere
    nivel L2, y pedirlo sin tenerlo hacía fallar todo el feed."""
    monkeypatch.setenv("DATABENTO_API_KEY", "db-test")
    from feeds import make_feed
    f = make_feed({"mode": "live", "symbol": "ES.v.0", "depth": "mbp-1"})
    assert f.depth == "mbp-1"
    f2 = make_feed({"mode": "live", "symbol": "ES.v.0"})
    assert f2.depth == "auto"          # por defecto, prueba de mayor a menor
    f3 = make_feed({"mode": "live", "symbol": "ES.v.0", "depth": "none"})
    assert f3.depth == "none"


def test_backtest_only_needs_trades():
    """La pestaña Backtest tiene que andar con un plan sin datos de libro:
    la detección de sweeps se hace con los prints, no con el libro."""
    import inspect
    import backtest as bt_mod
    fuente = inspect.getsource(bt_mod.DatabentoSource)
    assert 'schema="trades"' in fuente
    assert "mbp" not in fuente.lower()
