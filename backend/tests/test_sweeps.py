import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from sweeps import Sweep, SweepDetector, Trade  # noqa: E402

MS = 1_000_000  # ns per ms


def det(**kw):
    defaults = dict(window_ns=200 * MS, min_levels=2, min_size=0,
                    auto_size=False)
    defaults.update(kw)
    return SweepDetector(**defaults)


def feed(d, trades):
    out = []
    for t in trades:
        out.extend(d.on_trade(Trade(*t)))
    return out


def test_simple_buy_sweep_on_flush():
    d = det(min_levels=3, min_size=50)
    ts = 1_000 * MS
    feed(d, [(ts, 100.00, 30, "B"), (ts, 100.25, 20, "B"),
             (ts, 100.50, 10, "B")])
    sweeps = d.flush(ts + 300 * MS)
    assert len(sweeps) == 1
    sw = sweeps[0]
    assert sw.side == "B"
    assert sw.total_size == 60
    assert sw.levels == 3
    assert sw.price_min == 100.00 and sw.price_max == 100.50
    assert sw.trade_count == 3
    assert len(sw.prints) == 3


def test_single_level_never_qualifies():
    d = det(min_levels=2)
    ts = 1_000 * MS
    feed(d, [(ts, 100.0, 500, "B"), (ts + MS, 100.0, 400, "B")])
    assert d.flush(ts + 500 * MS) == []


def test_size_threshold_filters():
    d = det(min_levels=2, min_size=100)
    ts = 1_000 * MS
    feed(d, [(ts, 100.00, 30, "A"), (ts, 99.75, 30, "A")])
    assert d.flush(ts + 500 * MS) == []          # 60 < 100
    feed(d, [(ts + 10_000 * MS, 100.00, 60, "A"),
             (ts + 10_000 * MS, 99.75, 60, "A")])
    assert len(d.flush(ts + 11_000 * MS)) == 1   # 120 >= 100


def test_gap_splits_chain():
    d = det(min_levels=2)
    ts = 1_000 * MS
    # second print arrives 201ms later: first chain closes (1 level, no mark)
    out = feed(d, [(ts, 100.00, 10, "B"), (ts + 201 * MS, 100.25, 10, "B"),
                   (ts + 202 * MS, 100.50, 10, "B")])
    assert out == []
    sweeps = d.flush(ts + 1_000 * MS)
    assert len(sweeps) == 1
    assert sweeps[0].total_size == 20            # only the second chain
    assert sweeps[0].levels == 2


def test_sides_are_independent_chains():
    d = det(min_levels=2)
    ts = 1_000 * MS
    # sell prints interleaved with a buy chain must not break it
    out = feed(d, [(ts, 100.00, 10, "B"),
                   (ts + 1 * MS, 99.75, 5, "A"),
                   (ts + 2 * MS, 100.25, 10, "B"),
                   (ts + 3 * MS, 99.50, 5, "A")])
    assert out == []
    sweeps = d.flush(ts + 500 * MS)
    assert len(sweeps) == 2
    by_side = {s.side: s for s in sweeps}
    assert by_side["B"].total_size == 20 and by_side["B"].levels == 2
    assert by_side["A"].total_size == 10 and by_side["A"].levels == 2


def test_no_aggressor_prints_ignored():
    d = det(min_levels=2)
    ts = 1_000 * MS
    feed(d, [(ts, 100.00, 10, "B"), (ts + MS, 100.10, 999, "N"),
             (ts + 2 * MS, 100.25, 10, "B")])
    sweeps = d.flush(ts + 500 * MS)
    assert len(sweeps) == 1
    assert sweeps[0].total_size == 20            # the N print not counted


def test_next_trade_closes_stale_chain():
    d = det(min_levels=2)
    ts = 1_000 * MS
    feed(d, [(ts, 100.00, 10, "B"), (ts + MS, 100.25, 10, "B")])
    out = feed(d, [(ts + 500 * MS, 101.00, 1, "B")])
    assert len(out) == 1                          # old chain emitted
    assert out[0].total_size == 20


def test_vwap():
    d = det(min_levels=2)
    ts = 1_000 * MS
    feed(d, [(ts, 100.0, 10, "B"), (ts, 101.0, 30, "B")])
    sw = d.flush(ts + 500 * MS)[0]
    assert abs(sw.vwap - (100.0 * 10 + 101.0 * 30) / 40) < 1e-9


def test_automatic_threshold_uses_sd():
    d = det(min_levels=2, min_size=0, auto_size=True,
            sd_interval_ns=60_000_000_000, sd_multiplier=3.0)
    ts = 1_000 * MS
    # seed history: sizes alternating 1 and 3 -> sd = 1
    for i in range(20):
        d.on_trade(Trade(ts + i * 300 * MS, 100.0, 1 if i % 2 else 3, "B"))
    d.flush(ts + 7_000 * MS)
    base = ts + 8_000 * MS
    # small 2-level chain: total 4 > sd*3 = 3 -> qualifies
    feed(d, [(base, 100.00, 2, "B"), (base, 100.25, 2, "B")])
    small = d.flush(base + 500 * MS)
    assert len(small) == 1
    assert small[0].threshold > 0
    # tighten multiplier so the same chain is filtered
    d.update_params(sd_multiplier=50.0)
    b2 = base + 2_000 * MS
    feed(d, [(b2, 100.00, 2, "B"), (b2, 100.25, 2, "B")])
    assert d.flush(b2 + 500 * MS) == []


def test_automatic_falls_back_to_fixed_with_few_samples():
    d = det(min_levels=2, min_size=5, auto_size=True)
    ts = 1_000 * MS
    feed(d, [(ts, 100.00, 2, "B"), (ts, 100.25, 2, "B")])
    assert d.flush(ts + 500 * MS) == []          # 4 < fixed fallback 5
    t2 = ts + 5_000 * MS
    feed(d, [(t2, 100.00, 4, "B"), (t2, 100.25, 4, "B")])
    assert len(d.flush(t2 + 500 * MS)) == 1      # 8 >= 5


def test_update_params_applies_to_open_chain():
    d = det(min_levels=2, min_size=1000)
    ts = 1_000 * MS
    feed(d, [(ts, 100.00, 10, "B"), (ts, 100.25, 10, "B")])
    d.update_params(min_size=10)
    assert len(d.flush(ts + 500 * MS)) == 1


def test_sizes_history_bounded_even_with_auto_off():
    d = det(min_levels=2, auto_size=False,
            sd_interval_ns=60_000_000_000)          # 60s window
    ts = 1_000 * MS
    for i in range(5_000):
        d.on_trade(Trade(ts + i * 100 * MS, 100.0, 1, "B"))
    # 60s window / 100ms cadence -> ~600 entries, not 5000
    assert len(d._sizes) <= 610


def test_min_levels_clamped_on_construction():
    d = det(min_levels=1)                           # floor is 2
    ts = 1_000 * MS
    feed(d, [(ts, 100.0, 500, "B")])
    assert d.flush(ts + 500 * MS) == []             # 1 level never a sweep


def test_closing_outlier_does_not_raise_chain_threshold():
    d = det(min_levels=2, min_size=0, auto_size=True,
            sd_interval_ns=600_000_000_000, sd_multiplier=3.0)
    ts = 1_000 * MS
    for i in range(10):                             # quiet tape, sd = 0
        d.on_trade(Trade(ts + i * 300 * MS, 100.0, 2, "B"))
    d.flush(ts + 5_000 * MS)
    base = ts + 6_000 * MS
    feed(d, [(base, 100.00, 3, "B"), (base, 100.25, 3, "B")])
    # a 500-lot outlier arrives later and closes the chain: it must not
    # retroactively inflate the threshold the chain is judged against
    out = feed(d, [(base + 400 * MS, 100.50, 500, "B")])
    assert len(out) == 1
    assert out[0].total_size == 6


def test_finalize_closes_open_chains():
    d = det(min_levels=2)
    ts = 1_000 * MS
    feed(d, [(ts, 100.00, 10, "B"), (ts, 100.25, 10, "B")])
    sweeps = d.finalize()                           # e.g. end of replay
    assert len(sweeps) == 1 and sweeps[0].total_size == 20
    assert d.finalize() == []                       # idempotent


def test_to_dict_wire_format():
    d = det(min_levels=2)
    ts = 1_000 * MS
    feed(d, [(ts, 100.00, 10, "B"), (ts + MS, 100.25, 10, "B")])
    sw = d.flush(ts + 500 * MS)[0]
    w = sw.to_dict()
    assert w["type"] == "sweep"
    assert w["size"] == 20 and w["levels"] == 2
    assert w["px_min"] == 100.00 and w["px_max"] == 100.25
    assert w["prints"] == [[ts, 100.00, 10], [ts + MS, 100.25, 10]]
