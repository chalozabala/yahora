"""Command-line runner for the sweep backtest.

    python backend/backtest_cli.py --days 10                  # demo data
    python backend/backtest_cli.py --mode databento \
        --symbol ES.v.0 --days 10                             # real data

Writes the full report JSON + CSV next to the repo (backtest_results/)
and prints a summary. Same engine as the web UI's Backtest tab.
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid

import backtest as bt


def main() -> int:
    ap = argparse.ArgumentParser(description="Sweep backtest")
    ap.add_argument("--mode", choices=["demo", "databento"], default="demo")
    ap.add_argument("--dataset", default="GLBX.MDP3")
    ap.add_argument("--symbol", default="ES.v.0")
    ap.add_argument("--stype", default="continuous")
    ap.add_argument("--days", type=int, default=10)
    ap.add_argument("--end-date", default=None,
                    help="last day, YYYY-MM-DD (default: yesterday)")
    ap.add_argument("--min-size", type=int, default=40)
    ap.add_argument("--min-levels", type=int, default=3)
    ap.add_argument("--window-ms", type=float, default=200)
    ap.add_argument("--yes", action="store_true",
                    help="skip the data-cost confirmation")
    args = ap.parse_args()

    cfg = {
        "mode": args.mode, "dataset": args.dataset, "symbol": args.symbol,
        "stype_in": args.stype, "days": args.days, "end_date": args.end_date,
        "params": {"min_size": args.min_size, "min_levels": args.min_levels,
                   "window_ms": args.window_ms},
    }

    if args.mode == "databento" and not args.yes:
        est = bt.estimate_cost(cfg)
        cost = "unknown" if est.get("unknown") else f"${est['usd']:.2f}"
        print(f"Estimated data cost: {cost} "
              f"({est['cached_days']}/{est['days']} days cached)")
        if input("Continue? [y/N] ").strip().lower() != "y":
            return 1

    def progress(p: dict) -> None:
        print(f"\r  {p.get('pct', 0):5.1f}%  {p.get('detail', '')[:70]:<70}",
              end="", flush=True)

    report = bt.run_backtest(cfg, progress)
    print()

    job_id = "cli-" + uuid.uuid4().hex[:8]
    bt.RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    json_path = bt.RESULTS_DIR / f"{job_id}.json"
    csv_path = bt.RESULTS_DIR / f"{job_id}.csv"
    json_path.write_text(json.dumps(report))
    csv_path.write_text(bt.report_to_csv(report))

    t = report["totals"]
    print(f"\nSweeps: {t['sweeps']}  (buy {t['buy']} / sell {t['sell']})  "
          f"over {t['days_with_data']} days, {t['trades']:,} trades, "
          f"tick {report['tick']}")
    print(f"{'horizon':>8} {'n':>7} {'median':>8} {'mean':>8} {'hit%':>7}")
    for h in report["horizons"]:
        a = h["all"]
        if not a["n"]:
            continue
        print(f"{h['label']:>8} {a['n']:>7} {a['median']:>8.2f} "
              f"{a['mean']:>8.2f} {a['hit_rate'] * 100:>6.1f}%")
    for note in report["notes"]:
        print(f"note: {note}")
    print(f"\nReport: {json_path}\nCSV:    {csv_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
