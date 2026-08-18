# Sweeps — web indicator

A web reimplementation of a *sweep orders* indicator in the style of the
one found in Bookmap, fed by the [Databento](https://databento.com) API.
A **sweep** is a burst of aggressive trades on one side that consumes
liquidity across several price levels in a short time window — the
footprint of a large market order punching through the book.

![Deploy to Fly.io](https://fly.io/dl/open_in_fly.svg)

## What it does

* **Liquidity heatmap** chart (time →, price ↑) painted from MBP-10 depth,
  with best bid/ask lines, last-price line and per-trade dots.
* **Sweep detection**, server-side, mirroring the documented behavior of
  the original indicator:
  * trade *chains* per aggressor side (buys and sells detected
    independently), a chain grows while the gap between consecutive prints
    is ≤ the **Time** limit (ms);
  * a chain is a sweep when its **total volume** (all levels combined)
    reaches the size threshold **and** it touched at least **Price levels**
    distinct prices;
  * the size threshold is fixed, or **Automatic**: stdev of traded volume
    over the last *SD interval* minutes × *SD multiplier*;
  * marks appear once the chain closes (delay ≥ the time limit), like the
    original.
* **Faithful rendering**: dots on the swept prints (round/rect, size
  configurable) plus one icon per sweep, vertically offset and connected
  by a dotted line, volume label beside it; nearby same-side icons
  **cluster** into one icon with the aggregated volume. Drag an icon
  vertically to move all icons. Right-click the chart for the settings
  dialog. Optional **sound alert** per new mark.
* Extras: sweep log with click-to-locate, buy/sell counters, hover
  tooltips, wheel = price zoom, shift+wheel = time zoom.

## Data modes

| Mode | Source | Needs |
|------|--------|-------|
| Demo | synthetic book + trades with periodic sweeps | nothing |
| Live | Databento live gateway (`trades` + `mbp-10`) | `DATABENTO_API_KEY` |
| Replay | Databento historical `timeseries.get_range`, replayed on a clock | `DATABENTO_API_KEY` |

Default instrument: `ES.v.0` (CME E-mini S&P 500, highest-volume contract,
continuous symbology) on dataset `GLBX.MDP3`. Any Databento
dataset/symbol/symbology can be entered in the toolbar.

> Cost note: historical `mbp-10` is volume-heavy; replay pulls both
> `trades` and `mbp-10` for the range you request (capped by a record
> limit). Check `metadata.get_cost` in the Databento portal if unsure.

## Run locally

```bash
pip install -r backend/requirements.txt
export DATABENTO_API_KEY=db-...        # optional; demo mode works without
cd backend && uvicorn main:app --port 8080
# open http://localhost:8080
```

`databento` is only imported when live/replay is requested — demo mode
runs with FastAPI alone.

## Deploy (Fly.io)

```bash
fly launch            # uses the Dockerfile
fly secrets set DATABENTO_API_KEY=db-...
```

## Tests

```bash
pip install pytest
pytest backend/tests -q
```

## Endpoints

* `/` — the app
* `/ws` — websocket stream (JSON event batches)
* `/health` — liveness + whether a Databento key is configured
* `/gexlive` — legacy GEX demo endpoint kept from the original repo
