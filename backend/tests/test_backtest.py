import datetime as dt
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import backtest as bt  # noqa: E402


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
    assert len(lines) == 1 + report["totals"]["sweeps"]
    header = lines[0].split(",")
    assert header[:3] == ["date", "time_utc", "side"]
    assert header[-1] == "move_ticks_15m"
    assert all(len(l.split(",")) == len(header) for l in lines[1:])
