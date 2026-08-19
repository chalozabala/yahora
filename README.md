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

## Backtest (pestaña "Backtest")

Pensado para que lo corra cualquiera, sin saber nada técnico:

1. Abrí la app y tocá la pestaña **Backtest**.
2. Elegí el instrumento (S&P 500 / Nasdaq) y la cantidad de días
   (10 por defecto). Con **Demo** podés probarlo sin clave de Databento.
3. Tocá **▶ Correr backtest**. Con datos reales, antes de gastar te
   muestra el costo estimado de los datos y cuántos días ya están en
   caché (los días descargados no se vuelven a pagar).
4. Mirá el progreso día por día y, al final, el reporte: cuántos sweeps
   hubo, qué hizo el precio 10s/1m/5m/15m después de cada uno (en ticks,
   a favor o en contra de la dirección del sweep), el gráfico por día y
   los sweeps más grandes. Todo se puede bajar como CSV para Excel.

El filtro de detección (tamaño mínimo, niveles, tiempo) es el mismo del
panel ⚙ Settings, así el backtest mide exactamente lo que ves en vivo.
Los resultados quedan guardados en `backtest_results/` y los datos crudos
en `data_cache/`. También hay CLI: `python3 backend/backtest_cli.py --help`.

## Correrlo en tu PC (sin saber nada técnico)

1. **Bajá el ZIP**:
   [descargar](https://github.com/chalozabala/yahora/archive/refs/heads/claude/sweeps-web-indicator-ouzwur.zip)
   (o en GitHub: botón verde **Code → Download ZIP**, con la rama
   `claude/sweeps-web-indicator-ouzwur` seleccionada).
2. **Descomprimilo** (clic derecho → Extraer todo).
3. **Doble clic** en:
   * **Windows** → `run.bat`
   * **Mac / Linux** → `run.sh` (o en una terminal: `./run.sh`)
4. Se abre una ventana negra que prepara todo la primera vez (1-2 minutos)
   y después **el navegador se abre solo** en `http://localhost:8080`.

Requisito único: tener **Python 3** instalado
([python.org/downloads](https://www.python.org/downloads/) — en Windows
marcá la casilla *"Add python.exe to PATH"* al instalar). Si falta,
`run.bat` te lo dice y te deja el link.

Para frenarlo: cerrá la ventana negra.

## Run locally (manual)

La forma corta (instala dependencias y levanta todo):

```bash
./run.sh
# abrir http://localhost:8080
```

Manual:

```bash
python3 -m pip install -r backend/requirements.txt
export DATABENTO_API_KEY=db-...        # optional; demo mode works without
cd backend && python3 -m uvicorn main:app --port 8080
# open http://localhost:8080
```

`databento` is only imported when live/replay is requested — demo mode
runs with FastAPI alone.

## Deploy en Railway (3 clics, sin CLI)

1. Entrá a [railway.com](https://railway.com) → **Login with GitHub**
   (la misma cuenta dueña de este repo).
2. **New Project → Deploy from GitHub repo** → elegí `chalozabala/yahora`
   y la rama `claude/sweeps-web-indicator-ouzwur`. Railway detecta el
   `Dockerfile` y el `railway.json` solos y construye la app.
3. Cuando termine el build: **Settings → Networking → Generate Domain**.
   Esa URL pública es la que le compartís a cualquiera — abre y ya ve el
   indicador corriendo en demo, con la pestaña Backtest incluida.

Opcional: en **Variables** agregá `DATABENTO_API_KEY` para habilitar los
modos Live y Replay con datos reales. Nota: el plan trial de Railway da un
crédito único; Render ([render.com](https://render.com), plan free) es la
alternativa gratis permanente — **New + → Blueprint** sobre este repo usa
el `render.yaml` incluido (el servicio free se duerme tras inactividad y
tarda ~1 min en despertar). En ambos, el disco es efímero: la caché de
datos pagados de Databento no sobrevive redeploys — para backtests
grandes conviene correr local o Fly con volumen.

## Deploy (Fly.io)

```bash
fly volumes create sweeps_data   # persists the data cache + saved reports
fly launch                       # uses the Dockerfile + fly.toml
fly secrets set DATABENTO_API_KEY=db-...
```

El volumen es importante: sin él, la caché de datos de Databento y los
reportes guardados se borran cada vez que la máquina se apaga por
inactividad — y volverías a pagar por los mismos datos.

## Tests

```bash
python3 -m pip install pytest
python3 -m pytest backend/tests -q
```

## Endpoints

* `/` — the app
* `/ws` — websocket stream (JSON event batches)
* `/health` — liveness + whether a Databento key is configured
* `/gexlive` — legacy GEX demo endpoint kept from the original repo
