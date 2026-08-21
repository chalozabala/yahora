# Investigación profunda en GitHub: estrategias de trading para NQ y ES con datos de futuros y de opciones (gamma, delta, GEX)

**Fecha:** 21 de agosto de 2026
**Objetivo:** operar futuros NQ (E-mini Nasdaq-100) y ES (E-mini S&P 500) usando (a) datos de futuros y (b) datos de opciones — gamma, delta, GEX, posicionamiento de dealers — partiendo del dashboard GEX mínimo existente en este repo (FastAPI + API de GEXBot para NQ).

**Metodología:** investigación orquestada con 22 agentes en paralelo: 8 buscadores por dimensión (GEX/dealers, vanna/charm/0DTE, estrategias intradía de futuros, order flow/volume profile, frameworks de backtesting, pipelines de datos de opciones, ML cuantitativo, volatilidad/régimen), verificación repo a repo contra su página de GitHub, selección de los 10 mejores para análisis profundo **leyendo su código fuente real** (fórmulas exactas, reglas de entrada/salida, calidad, licencias), y una pasada de crítica que detectó y cubrió 2 huecos (gestión de riesgo para futuros apalancados y mapeo NDX/QQQ→NQ). **Total: 93 repositorios únicos verificados.** Se excluyeron repos muertos (404), spam y repos generados con IA sin código real.

---

## 1. Resumen ejecutivo

1. **Existe un ecosistema pequeño pero real de GEX específico para NQ/ES**, y es muy reciente (2026). Los dos hallazgos principales son [zrack/gex-terminal](https://github.com/zrack/gex-terminal) (GEX intradía sobre opciones de futuros de CME para ES/NQ, Black-76, adaptadores Databento/Tradovate/IBKR, 46 archivos de test) y [Hewkaw02/Futures-Options-SD-Dashboard](https://github.com/Hewkaw02/Futures-Options-SD-Dashboard) (GEX Black-76 sobre cadenas nativas /ES y /NQ vía Tastytrade DXLink, gratis con cuenta). Ya no es necesario depender solo de proxies SPX/QQQ ni de servicios de pago tipo GEXBot.

2. **La única señal GEX "superviviente" a una validación seria que encontramos es operable hoy con tu dashboard**: régimen de gamma negativo + precio a <15% del rango sobre el put wall → rebote alcista; stop bajo el put wall, target en el punto medio de los muros ([0dte-strategy](https://github.com/YichengYang-Ethan/0dte-strategy), validada sobre 574 días). Y su lección más valiosa: **ejecutar la señal en el futuro, no en opciones** — el wrapper de opciones consumió entre el 41% y el 95% del edge en sus pruebas; el subyacente en directo dio profit factor 1.67.

3. **El "régimen de gamma" como filtro es la síntesis más accionable de toda la investigación**: GEX total positivo → dealers estabilizan → favorece reversión a la media y rangos; GEX total negativo → dealers amplifican → favorece momentum/breakouts. Ninguna estrategia intradía de futuros de las encontradas usa este filtro de serie: combinarlo (tu dashboard GEX + un ORB o bandas de ruido) es el terreno menos explotado y donde tienes ventaja de infraestructura.

4. **Advertencia empírica dura**: dos repos independientes con metodología seria documentan **compresión del edge intradía desde 2025** (la réplica de "Beat the Market" en SPY y ES, y el proyecto 0DTE). Además, evidencia académica citada (SSRN 4881008) indica que el gamma neto de dealers ≈ 0 en 2024–26, lo que debilita la convención "naive" (calls +, puts −) que usa casi todo el ecosistema. Los niveles GEX funcionan mejor como **mapa de niveles y régimen** que como señal direccional autónoma.

5. **La ruta de datos tiene tres escalones claros**: gratis (CBOE delayed para _SPX/_NDX/QQQ/SPY + Tastytrade DXLink para cadenas /ES /NQ nativas + ficheros de settlement de CME para OI oficial), medio (ThetaData ~$40/mes para históricos OPRA 1-min; Polygon), e institucional (Databento GLBX.MDP3: futuros + opciones sobre futuros + libro L3 + OI oficial vía esquema `statistics`). Para backtesting/ejecución los estándares son NautilusTrader (26.7k★, adaptador Databento estable, ejemplo oficial con ES) y QuantConnect Lean; para ejecución retail: ib_async, async_rithmic, project-x-py (TopstepX) y la API oficial de Tradovate.

---

## 2. Conceptos operativos (lo que implementan los repos)

### 2.1 La fórmula GEX y sus convenciones de signo

Prácticamente todo el ecosistema calcula la exposición gamma por strike así:

```
GEX($ por movimiento del 1%) = Γ × OI × mult × S² × 0.01
```

- `Γ`: gamma por contrato (Black-Scholes para índice/ETF; **Black-76 para opciones sobre futuros**, con drift 0 sobre el precio del futuro F).
- `OI`: open interest (algunos repos usan volumen de sesión como proxy intradía — ver gex-terminal).
- `mult`: **100** para opciones de equity/índice, **$50/punto** para opciones de ES, **$20/punto** para opciones de NQ (micros: MES $5, MNQ $2).
- Convenciones de signo encontradas:
  - **Naive (la dominante)**: calls positivas, puts negativas — asume dealers largos de calls y cortos de puts (jensolson, gex-tracker, Darthreign, 0dte-strategy).
  - **Uniforme dealer-short**: todo negativo (Hewkaw02) — hace que el GEX total sea casi siempre negativo; revisar antes de fiarse de su régimen.
  - **Direccional por lado agresor**: usa el aggressor side de cada trade (Databento MDP3) para inferir qué lado quedó el dealer (gex-terminal, modo direccional) — la variante más moderna y la única que aprovecha datos que el cálculo clásico por OI ignora.

### 2.2 Niveles estructurales

- **Gamma flip / zero gamma**: precio donde el GEX neto acumulado cruza cero. La implementación de referencia (método Perfiliev; Darthreign/gex-dashboard) reconstruye el perfil sobre una rejilla de spot ±8% con 161 pasos e **interpola linealmente** el cruce — mucho mejor que tomar "el primer strike donde cambia el signo" (granularidad de 25 puntos en NQ).
- **Call wall**: strike con máximo GEX/OI de calls por encima del spot (resistencia); **put wall**: máximo |GEX negativo|/OI de puts por debajo (soporte). Variantes intradía: top-3 por OI y por volumen.
- **Régimen**: GEX total > 0 → mercado "amortiguado" (los dealers venden rallies y compran caídas al cubrir delta) → rangos, reversión, pinning; GEX total < 0 → "explosivo" (cubren en la dirección del movimiento) → tendencias, expansión de volatilidad.
- **Bandas de expected move**: `S × IV_ATM × √(DTE/365)` (±1σ/2σ/3σ); con VIX1D: `(VIX1D/√252) × 0.5 × SPX` para el rango de media sesión.

### 2.3 Vanna y charm (flujos de segunda derivada)

- **Vanna** (∂Δ/∂σ): con IV cayendo en un rally, los dealers recompran delta → "vanna rally". VEX ≈ Σ vanna × OI × 100 × S × 0.01, con signo + calls / − puts. Repos: [gflows](https://github.com/aaguiar10/gflows) (SPX/NDX/RUT por strike y vencimiento, datos CBOE gratis), gammagrid, 0dte-strategy (vanna bucketizada por vencimiento {0DTE, 1–7d, 8–30d, 31+d}).
- **Charm** (∂Δ/∂t): el decaimiento de delta fuerza recobertura hacia el cierre y alrededor de OPEX. Implementaciones: Hewkaw02 (fórmula Black-76 completa), gflows, FlashAlpha-lab/0dte-options-analytics.
- El ecosistema open source de vanna/charm es pequeño; la mayor parte vive en productos comerciales. gflows es la referencia de arquitectura.

### 2.4 La advertencia que casi nadie imprime en su README

El proyecto 0dte-strategy cita evidencia académica (Adams/Fontaine/Ornthanalai, SSRN 4881008) de que **el carry de gamma neto de los dealers fue ≈ 0 en 2024–26**: la hipótesis naive del signo está debilitada en el régimen actual. Y gex-terminal declara explícitamente que "no se ha medido validez predictiva, edge, calibración ni P&L en vivo". Traducción práctica: usa el GEX como **mapa** (dónde están los muros, dónde cambia el régimen) y exige validación propia antes de tratarlo como **señal**.

---

## 3. Los 10 repositorios analizados a fondo (código leído)

### 3.1 [zrack/gex-terminal](https://github.com/zrack/gex-terminal) — GEX intradía nativo para ES/NQ · Python · MIT · ~13★ · muy activo (08-2026)

**Qué es.** TUI asíncrona que estima GEX intradía sobre **opciones de futuros de CME para ES y NQ** (y SPX/QQQ vía BS). Calcula gamma wall (argmax |net GEX|), zero-gamma por interpolación lineal, banda de concentración (rango mínimo con ≥70% del |gamma|), y exporta overlays a TradingView. Adaptadores: Databento GLBX.MDP3 (trades de `ES.OPT`/`NQ.OPT` + midpoint del continuo `ES.v.0`/`NQ.v.0`, con lado agresor e inversión de IV Black-76 por bisección), Tradovate, IBKR, yfinance; modo demo/replay JSONL sin cuenta.

**Detalle clave.** No usa OI: usa **volumen acumulado de sesión** como proxy (call_gex = vol_call × Γ × S² × 0.01 × mult, puts en negativo), y tiene un **modo direccional** que firma por lado agresor. Config correcta ya incluida: ES mult 50, NQ 20, MES 5, MNQ 2; Black-76 automático para roots de futuros.

**Para ti.** Lo valioso no es la TUI sino extraer `IntradayGexEngine` + `StatefulGexConsumer` + adaptador Databento como librería: el engine es NumPy puro, devuelve un dict (strikes, call/put/net gex, gamma wall, zero gamma, banda) que puedes servir por JSON/WebSocket a tu frontend actual. Calidad inusual: 46 archivos de test, docs de supuestos del modelo. Contras: proyecto de ~3 meses, volumen ≠ inventario de dealers (los autores lo advierten), IV frágil fuera de Databento, no distingue RTH/ETH (decide tú cuándo resetear estado).

### 3.2 [Hewkaw02/Futures-Options-SD-Dashboard](https://github.com/Hewkaw02/Futures-Options-SD-Dashboard) — GEX Black-76 sobre /ES /NQ con datos gratis · Python · MIT · ~7★ · ~659 commits, muy activo

**Qué es.** Analítica de opciones sobre futuros CME (/ES, /NQ, /GC) vía **API de Tastytrade + DXLink Streamer (gratis con cuenta de brokerage)**: griegas Black-76 completas (incluye vanna y charm), GEX/DEX por strike, bandas SD desde IV, muros de OI, PCR, skew, max pain, y un "Master Trading Bias" multi-factor. Pipeline batch (CSV/PNG por hora) → JSON → dashboard web estático.

**Fórmulas.** `gex = position_sign × OI × Γ × S² × 0.01 × mult` con mult {ES:50, NQ:20}; bandas `sd1 = precio × IV × √(DTE/365)` con suelo de 1 DTE para 0DTE; IV ATM por interpolación lineal entre strikes; skew = IV_put(98%) − IV_call(102%); filtro de calidad de datos (spread ≤50% del mid, bid>0, vol ≤ 2×OI) antes de agregar — **crítico en NQ, cuyo libro de opciones es fino**.

**Para ti.** `analytics/exposure.py` y `analytics/volatility.py` son módulos puros (solo `math`) trasplantables tal cual a tu backend FastAPI; su formato JSON (`gex_profile`, `sd_bands`, `oi_walls`, `max_pain`, `iv_smile`) sirve como contrato de datos para tu frontend. Contras: **firma del GEX uniforme dealer-short** (corrígela a naive/direccional configurable antes de usar su régimen), gamma flip sin interpolación, el README promete 5 "estados de cobertura" que el código no tiene, sin tests, bus factor 1.

### 3.3 [Darthreign/gex-dashboard](https://github.com/Darthreign/gex-dashboard) — el dashboard GEX gratuito más completo · Python · MIT · ~10★ · 31 tests, activo

**Qué es.** Dashboard Dash sobre las cadenas **delayed gratuitas de CBOE** (`https://cdn.cboe.com/api/global/delayed_quotes/options/_SPX.json`, también `_NDX`, `SPY`, `QQQ`): GEX/DEX por strike (naive: `sign × Γ × OI × 100 × S² × 0.01`), griegas recalculadas localmente (BS vectorizado + IV Newton-Raphson), **zero gamma con rejilla ±8%/161 pasos e interpolación** (la mejor implementación del flip encontrada), muros GEX1–5, HVL ponderado por volumen, proxy de delta-flow 1-min (Δvolumen × delta), histórico Parquet, bot de Discord y **servidor MCP** para consultarlo con Claude.

**Para ti.** Trae los dos mapeos que necesitas: `config.py` asocia SPX→ES y NDX→NQ (trasladar niveles por **basis aditiva**, no ratio), y `gex/futopt.py` calcula GEX **nativo sobre opciones de futuros NQ/ES vía dxFeed** (multiplicadores 20/50, semanales EW, cadenas desde el referencial de Tastyworks). También captura ticks NQ/ES 24/5 a Parquet. Piezas a copiar: `enrich()`, `zero_gamma()`, el regex OCC y el endpoint CDN de CBOE. Contras: delayed ~15 min (vale para niveles, no para flujo), OI se actualiza una vez al día, comentarios parcialmente en francés, un autor.

### 3.4 [YichengYang-Ethan/0dte-strategy](https://github.com/YichengYang-Ethan/0dte-strategy) — la única señal GEX validada (y las hipótesis falsadas) · Python · MIT · ~11★

**Qué es.** Investigación 0DTE sobre SPX con disciplina poco común: parámetros pre-registrados, "kill gates" numéricos, test de fugas de información ("future-poison test"), fills conservadores, Deflated Sharpe. Bot paper/live en IB + reconstrucción "R0" de un sistema de precio-objetivo de pinning en curso. Datos: Theta Data OPRA 1-min (~$40/mes), 952 días.

**La señal superviviente (574 días validados, 2024-01→2026-04):**
```
SI GEX_total < 0  Y  (spot − put_wall)/(call_wall − put_wall) < 0.15
→ LONG (rebote convexo: dealers cortos de gamma compran la caída al cubrir)
   stop  = put_wall × 0.998
   target = (put_wall + call_wall)/2
```
GEX naive (`Γ × OI × 100 × S² × 0.01`), muros por máximo GEX a cada lado, flip por interpolación del acumulado, vanna con S¹ y buckets {0, 1–7, 8–30, 31+} días.

**El hallazgo de oro para un trader de futuros:** su análisis de atrición demostró que la misma señal daba **PF 1.67 operando el subyacente en directo**, mientras el wrapper de call consumía ~41% del edge y el de spread ~95%. **Calcula GEX sobre opciones; ejecuta MNQ/MES o NQ/ES.** También documenta hipótesis falsadas (long-call 1DTE: PF 0.78; el skew de GEX ATM no predecía compresión de varianza tras costes) — lectura obligada antes de repetir esos caminos. Contras: edge marginal y frágil, R0 aún sin veredicto, datos no incluidos.

### 3.5 [giovannibrusco/zarattini-2024-momentum-spy](https://github.com/giovannibrusco/zarattini-2024-momentum-spy) — réplica rigurosa de "Beat the Market" sobre SPY **y ES** · Python · MIT · 48 tests

**Qué es.** Réplica del paper de Zarattini/Aziz/Barbon (2024) de momentum intradía con "Noise Area", validada de forma cruzada sobre **futuros ES reales de IB** (9 contratos cosidos con roll por cruce de volumen y back-adjustment aditivo).

**Reglas exactas:** σ_t = media 14 días de |P_t/O − 1| por minuto de sesión (excluyendo el día actual); bandas `Upper = max(open, prev_close) × (1+σ_t)`, `Lower = min(open, prev_close) × (1−σ_t)`; chequeos cada 30 min (10:00–15:30 ET); long si close > Upper, short si close < Lower (con flips); salida trailing por max(VWAP, banda) [variante "final"] o banda opuesta [variante "base"]; cierre forzoso 15:59; sizing por volatilidad objetivo (2%/día, cap 4×, floor por nocional del contrato).

**Resultados honestos:** SPY 2020–24: Sharpe 1.11, alpha +16.7% anual (t=2.85), perfil long-volatility (+25.8% en 2022). ES may-2024→jul-2026: variante "final" Sharpe −0.07 (whipsaw), **"base" Sharpe 0.52**; correlación ES-SPY 0.97. **Desde 2025: Sharpe ≈ 0 en ambos** — y sus experimentos demuestran que optimizar parámetros o reseleccionar variantes walk-forward **empeora** frente a la config fija del paper. Para NQ: cambiar símbolo a NQ/MNQ en el descargador IB, recalcular σ_t desde cero (no reutilizar las de ES), multiplicador 20/2. **Idea de extensión natural (no incluida en el repo): condicionar las entradas al régimen de gamma de tu dashboard** — breakouts solo en gamma negativo — usando su protocolo congelado ex-ante y su `stats.py` (alpha, t-stat, DSR) como plantilla de validación.

### 3.6 [dws-data/nas-orb-backtester](https://github.com/dws-data/nas-orb-backtester) — ORB con retroceso a Volume Profile, nativo de NQ · Python · **sin licencia** · resultados no reproducibles

**Qué es.** ORB de 15 min (09:30–09:45 ET) sobre NQ 1-min de Databento (`NQ.v.0`, 2016–2026): Volume Profile de 24 buckets **sobre el propio rango de apertura** (POC/VAH/VAL, value area 70%, réplica del Fixed Range VP de TradingView); breakout confirmado por umbral (10–30% del tamaño del ORB, 5 modos de confirmación); **entrada en el retroceso** a VAH/POC/VAL; stop en ORB opuesto/extremo VP/POC; target = 1× el rango; métricas MFE/MAE en R; contexto ADR(30)/NR7/gaps/estructura multi-timeframe.

**Publicado:** 278 trades 2021–26, ~42–43% win, +21.2R/año a 1% de riesgo. **Pero**: la variante ganadora y los filtros aplicados no se revelan, faltan el runner y el pipeline de datos (gitignored), no hay tests ni licencia (legalmente no puedes copiar el código — úsalo como **referencia de diseño y reimplementa**). El diseño sí es excelente: todo normalizado por orb_size, portar a ES es trivial, y `entry_level` acepta cualquier nivel — **puedes inyectar tus niveles GEX (flip, muros) como niveles de entrada o filtro direccional** reutilizando la mecánica de confirmación/retroceso.

### 3.7 [srlcarlg/srl-python-indicators](https://github.com/srlcarlg/srl-python-indicators) — order flow, volume profile, TPO y VWAP en un solo paquete · Python · Apache-2.0 · ~48★

**Qué es.** Port Python de una suite madura de cTrader: footprint por ticks (OrderFlowTicks), order flow agregado, Volume Profile (8 métodos de distribución sin ticks), TPO/Market Profile, multi-VWAP anclado con bandas, sistema Weis & Wyckoff. Separa datos de gráficos (Plotly) — puedes exponer los arrays por JSON a tu frontend.

**El parche crítico para CME:** su clasificación buy/sell es uptick/downtick por **cuenta de ticks** (herencia forex/CFD; un tick a precio igual suma a ambos lados). Con Databento `trades` tienes `side` (agresor) y `size`: parchear el bucle a `buy_profile[idx] += size if side=='B'` (~10 líneas) convierte la librería en footprint/delta **real en contratos**. Ojo: no calcula POC/VAH/VAL explícitos (solo HVN/LVN; el POC emerge como argmax; implementar VA 70% son ~15 líneas), es batch (no incremental), sin tests. Uso con tu dashboard: **HVN/LVN + POC como confluencia con tus muros de gamma; delta/absorción como confirmación en el nivel** (p. ej., delta vendedor que no logra romper un put wall = absorción compradora).

### 3.8 [nautechsystems/nautilus_trader](https://github.com/nautechsystems/nautilus_trader) — el chasis de backtest/ejecución · Rust+Python · LGPL-3.0 · 26.7k★

**Qué es.** Plataforma event-driven de grado producción: el mismo código de estrategia sirve para backtest (resolución de nanosegundos, libro L1/L2/L3, fill models con latencia y comisiones, cuentas de margen) y ejecución en vivo. **Adaptador oficial estable de Databento** que decodifica CME MDP 3.0 (GLBX.MDP3) incluida la reconstrucción L3 del libro desde MBO, y adaptador estable de Interactive Brokers para ejecutar futuros CME.

**Lo directamente aprovechable:** `examples/backtest/databento_cme_quoter.py` es un backtest completo de **ES** en XCME (plantilla para NQ); la estrategia de ejemplo `OrderBookImbalance` (ratio = min(bid,ask)/max(bid,ask) < 0.20 → orden FOK al lado grueso) sirve como confirmación de microestructura en tus niveles GEX; `GreeksCalculator` da gamma por contrato (el GEX de dealers, `Γ×OI×…`, lo agregas tú con el OI del esquema `statistics` de Databento); simbología parent (`ES.OPT`/`NQ.OPT`) y continuos (`ES.v.0`). Patrón recomendado: tu backend GEX publica niveles como custom data en el message bus; la estrategia se suscribe a niveles + libro y opera contra ellos. Contras: curva de aprendizaje alta, transición v1→v2 en curso (fijar versión), datos Databento de pago, los rolls de continuos los aportas tú.

### 3.9 [databento/databento-python](https://github.com/databento/databento-python) — la fuente canónica de datos CME · Python · Apache-2.0 · ~293★ · SDK oficial

**Qué es.** Cliente oficial de Databento: histórico (`timeseries.get_range`) y live (TCP con replay intradía, snapshot de libro para MBO, política de reconexión con relleno de huecos) sobre GLBX.MDP3 (CME) y OPRA.PILLAR. Esquemas: `mbo` (L3), `mbp-1/10`, `trades` (con agresor), `tbbo`, `ohlcv-1s/1m/1h/1d`, `definition` (strikes, vencimientos, multiplicadores), **`statistics` (open interest y settlement — la pieza que casi ningún dashboard GEX usa)**, `status` (sesiones/halts). Simbología: `parent` (`ES.OPT` = todas las opciones sobre futuros de ES en una petición), `continuous` (`NQ.v.0` roll por volumen).

**Para ti.** La vía nativa completa para GEX de NQ/ES sin proxies: `definition` + `statistics` (OI oficial diario) + `trades`/`mbp-1` (precios para IV/gamma con Black-76 — Databento **no** da griegas) con mult 50/20; los strikes mapean 1:1 al precio del futuro que operas, sin conversión de basis. Precios en punto fijo 1e-9 (usa los conversores de `DBNStore`), OI es EOD (el GEX intradía refresca spot y precios, no OI), versión 0.x con breaking changes (fijar versión). De pago por uso; MBO es caro — MBP-10/MBP-1 abaratan mucho.

### 3.10 [CJuanvip/CMEOptions](https://github.com/CJuanvip/CMEOptions) — OI oficial de opciones sobre futuros, gratis · Python · GPL-3.0 · abandonado (2019) pero patrón vigente

**Qué es.** Pipeline que parsea los **ficheros públicos de settlement diarios de CME** (precio de settle, strike, **open interest oficial** por serie), resuelve IV por strike (Brent) y calcula griegas; agrega Σ(OI × griega), strike medio ponderado por OI ("stress points"), escaleras ITM, max pain, y escenarios "¿cuánta cobertura absorbe el mercado si el precio va a X?" con ajuste cuadrático del perfil griega-vs-precio (la raíz de esa cuadrática es un gamma flip).

**Estado real.** El FTP original murió (~2022) — migrar al espejo `cmegroup.com/ftp/pub/settle/` o a los endpoints JSON de settlements de CME. Bugs verificados leyendo el código: vanna con signo invertido, BS en vez de Black-76, put delta +1 ITM a expiración, componentes por lado del polinomio mal evaluadas — **no uses sus números sin corregirlos; el valor es el patrón** (config JSON por producto → parser → solver IV → agregador por OI), directamente extensible a las opciones de ES/NQ (estándar + semanales EW/E1A–E5A). GPL-3.0: para uso interno sin problema; para redistribuir, reimplementa. Es la forma **gratuita** de tener el OI oficial de opciones sobre futuros que le falta a casi todos los dashboards GEX.

---

## 4. Estrategias accionables sintetizadas

Ordenadas por relación evidencia/esfuerzo, todas ejecutando **el futuro** (lección del §3.4):

### E1. Rebote en put wall con gamma negativa (validada por 0dte-strategy)
- **Setup:** GEX total < 0 (tu dashboard) y precio a <15% del rango [put wall, call wall] sobre el put wall.
- **Ejecución:** long MNQ/MES (micros primero); stop = put wall × 0.998 (en NQ, traducir el nivel de NDX/QQQ con basis — §6.3); target = punto medio de muros. Máx. 1 posición, cierre 16:00 ET.
- **Por qué funciona:** dealers cortos de gamma recompran delta en la caída → rebote convexo. Es la única regla del corpus que sobrevivió 574 días de validación con kill gates.

### E2. Régimen gamma como interruptor de estrategia (síntesis, terreno virgen)
- **GEX > 0 (día "amortiguado"):** reversión a la media — fade de extremos hacia VWAP/POC, respetar muros como rango; evitar breakouts (whipsaw).
- **GEX < 0 (día "explosivo"):** momentum — ORB (E3) o bandas de ruido (E4); evitar fades.
- Ningún repo de estrategias intradía lo usa de serie; tú ya tienes la mitad de la infraestructura. Valídalo con el protocolo del §5.

### E3. ORB 15-min con retroceso a Volume Profile en NQ (diseño de nas-orb-backtester, reimplementado)
- Rango 09:30–09:45 ET → VP del rango (24 buckets, VA 70%) → breakout confirmado (close a ≥10–30% del rango más allá del extremo) → **entrar en el retroceso** a VAH/POC/VAL → stop en ORB opuesto o extremo del VP → target 1× el rango → EOD forzoso.
- Mejora GEX: opera solo en la dirección del lado "libre" de muros (p. ej., largos solo si el call wall queda lejos por arriba) y usa el flip como filtro direccional. Reimplementa (sin licencia) y haz tu propio grid con validación out-of-sample: los +21.2R/año publicados no son verificables.

### E4. Bandas de ruido intradía (Zarattini) con filtro de régimen
- Reglas del §3.5, variante "base" (la que aguantó en ES 2024–26). Solo RTH; sizing por volatilidad (2%/día, cap 4×).
- **Advertencia:** edge ≈ 0 desde 2025 sin filtro. La hipótesis a testear (coherente con sus propios resultados): las bandas funcionan en gamma negativa y producen whipsaw en gamma positiva → condicionar entradas a GEX < 0.

### E5. Confirmación por order flow en niveles GEX
- En un muro: absorción (delta agresor en contra sin progreso de precio, srl-python-indicators parcheado con agresor real de Databento) o imbalance de libro (ratio min/max < 0.20, patrón OrderBookImbalance de Nautilus) como gatillo de entrada; OFI/MLOFI (LOB-feature-analysis) como feature continua.

### E6. Filtro macro de volatilidad (capa superior)
- Contango/backwardation de futuros VIX (vix_utils; regla clásica de QuantConnect Tutorials #198: roll diario > +0.10 = calma, < −0.10 = estrés), ratio VIX/VIX3M, y VRP = IV − RV (Yang-Zhang de jasonstrimpel/volatility-trading). Backwardation/VRP comprimido → reducir tamaño o apagar estrategias direccionales; VVIX/VIX alto con GEX negativo = fragilidad.

### E7. Capa ML solo después de E1–E4: meta-labeling
- No predecir el mercado: entrenar un modelo secundario que aprenda **cuándo tus señales GEX funcionan** (régimen, hora, IV, distancia al flip) y cuánto arriesgar (hudson-and-thames/meta-labeling, mlfinlab: triple barrier + purged CV). El hallazgo de gex-llm-patterns apunta a usar el **perfil por strike** (concentración, asimetría, distancia a muros) como features, no el GEX escalar. Expectativas honestas: R² 0.001–0.01 (Options-Flow-Predictor); el valor está en filtrar y dimensionar, no en predecir.

---

## 5. Validación: el protocolo que separa a los repos serios

Los tres mejores repos de investigación coinciden en la misma disciplina — cópiala:

1. **Congela parámetros y variantes ex-ante** (commit antes de mirar resultados) — zarattini-2024.
2. **Kill gates numéricos pre-registrados** ("si falla, STOP sin racionalizar") — 0dte-strategy: batir baselines tontas, expectancy > 0 con fills conservadores (entrar al ask, salir al bid), walk-forward anidado, Sharpe > 1 y Deflated Sharpe > 0, parámetros lejos de los bordes del grid.
3. **Tests anti-fuga**: z-scores con shift(1), cutoff estricto de sesión, y el "future-poison test" (aleatoriza los datos posteriores al cutoff y exige resultado idéntico) — trivial de portar y vale oro.
4. **Costes reales**: comisión (~$0.85–2.5/contrato/lado) + fees + slippage en ticks (0.25–0.5; NQ tiene libro más fino que ES), aplicados en entrada y salida.
5. **Publica también los resultados negativos** en tu cuaderno: las dos hipótesis falsadas de 0dte-strategy y la compresión post-2025 de zarattini son la información más valiosa de sus repos.

---

## 6. Datos: rutas por coste

### 6.1 Gratis (suficiente para niveles y régimen)
| Fuente | Qué da | Repos que la usan |
|---|---|---|
| CBOE delayed (`cdn.cboe.com/api/global/delayed_quotes/options/_SPX.json`, `_NDX`, `SPY`, `QQQ`) | Cadena completa con IV, OI, volumen, griegas de CBOE; ~15 min de retraso, se regenera ~60 s | Darthreign, gex-tracker, gflows, gex_data, Gex-Multi |
| **Tastytrade + DXLink** (cuenta de brokerage gratuita) | Cadenas **nativas de opciones sobre futuros /ES /NQ** en vivo: griegas, OI, trades | Hewkaw02, Darthreign (dxFeed) |
| Ficheros de settlement de CME (espejo web) | **OI oficial y settle de opciones sobre futuros** ES/NQ, EOD | CMEOptions (patrón) |
| yfinance | Cadenas SPY/QQQ, velas de ES=F/NQ=F | gammagrid, gamma-seed, MNQ-GEX |
| [global-stock-data](https://github.com/simonlin1212/global-stock-data) | CBOE con griegas + flujo 0DTE + **COT de la CFTC** (posicionamiento en futuros NQ/ES) sin API key | — |
| Databento GLBX demo/replay | Sesiones JSONL empaquetadas para desarrollo offline | gex-terminal |

### 6.2 Coste medio
- **ThetaData** (~$40/mes tier Value): OPRA 1-min con quotes y griegas de SPX/SPY/QQQ/NDX — el estándar de facto para históricos de backtesting (0dte-strategy: 952 días, ~16 GB). Cliente oficial deprecado; usar la API REST (wrapper `pythonfortraders/thetadata-api-python`).
- **Polygon.io/Massive**: snapshot de cadena con griegas y OI incluidos; plan de índices para SPX/NDX (prefijo `I:`).
- **Tradier** (token de brokerage): cadena con OI en tiempo casi real (Gamma-Vanna-Options-Exposure, 0DTE-dealer-gamma).
- **GEXBot** (lo que ya usas): niveles listos, incluido el feed NDX ya convertido a coordenadas de NQ (patrón del indicador Quantower_GexBot_Gamma_Point).

### 6.3 Mapeo de niveles al futuro (imprescindible hagas lo que hagas)
- **Regla de oro: convierte solo los precios de los niveles; nunca escales la gamma.**
- ES: `nivel_ES = nivel_SPX + basis`, con basis = (ES − SPX) en vivo o del cierre; SPX y ES comparten escala de puntos.
- NQ: `nivel_NQ = nivel_NDX + basis`; desde QQQ: `nivel_NQ ≈ strike_QQQ × (NQ/QQQ)` (ratio ≈ 41×, **recalcularlo cada mañana**).
- Repos que lo implementan: [Gex-Multi](https://github.com/itsfabtrading/Gex-Multi) (basis aditiva NDX→NQ, preconfigurado SPX/NDX/SPY/QQQ, usa VIX/VXN para vol), [MNQ-GEX](https://github.com/solaris0675/MNQ-GEX-gamma-exposure-levels-Free-) (QQQ→NQ/MNQ vía yfinance, 0–30 DTE), [Dax-giant/GEX](https://github.com/Dax-giant/GEX) (cadena de NDX directamente — el OI de NDX concentra el posicionamiento institucional), [gex_data](https://github.com/GMestreM/gex_data) (motor CBOE limpio y modular, MIT — la base que Gex-Multi extiende).
- **La alternativa que elimina el mapeo:** GEX nativo sobre opciones de futuros de CME (Tastytrade gratis o Databento `ES.OPT`/`NQ.OPT`) — strikes en la misma escala que el futuro, cubre posicionamiento ETH, y capta el pit propio de futuros. Es la dirección a la que apuntan los dos repos más activos (gex-terminal, Hewkaw02).

### 6.4 Institucional
- **Databento GLBX.MDP3**: futuros + opciones sobre futuros + L3 (MBO con agresor) + `statistics` (OI/settlement) + `definition`; simbología parent/continuous. De pago por uso; el live de CME real-time añade cuota de exchange. La combinación con NautilusTrader es la pila de referencia.
- **OPRA.PILLAR** (Databento) para la pata de opciones cash (SPX/NDX/SPY/QQQ) si mantienes la vía proxy.

---

## 7. Frameworks y ejecución

### Backtesting
| Framework | ★ | Punto fuerte para NQ/ES | Limitación |
|---|---|---|---|
| [nautilus_trader](https://github.com/nautechsystems/nautilus_trader) | 26.7k | Adaptador Databento estable (L3 CME), ejemplo oficial con ES, mismo código backtest→live, fill models serios | Curva alta; v1→v2 en curso; rolls manuales |
| [Lean](https://github.com/QuantConnect/Lean) (QuantConnect) | 21.3k | Contratos continuos/rolls/márgenes/sesiones RTH-ETH resueltos de serie; datos ES/NQ incluidos en su nube | Ecosistema propio (CLI/Docker); C# en el núcleo |
| [vectorbt](https://github.com/polakowo/vectorbt) | 8.7k | Barridos masivos de variantes en minutos (fase de investigación) | No modela márgenes/futuros ni live; mult a mano |
| [hftbacktest](https://github.com/nkaz001/hftbacktest) | 4.4k | El único con colas y latencias realistas sobre MBO de CME — clave si ejecutas límites en el libro | Live solo cripto; formato de datos propio |
| [pysystemtrade](https://github.com/robcarver17/pysystemtrade) | 3.4k | Referencia en rolls/continuos y producción real de futuros con IB | Enfoque swing/tendencia, no intradía |
| [backtrader](https://github.com/mementum/backtrader) | 22.9k | Margen+multiplicador de futuros correctos en Python puro; mucha documentación | Congelado desde ~2023; IB vía IbPy obsoleta |
| [zipline-reloaded](https://github.com/stefan-jansen/zipline-reloaded) | 1.9k | ContinuousFuture con rolls heredado de Quantopian, mantenido | Sin live; requiere bundle propio |

### Ejecución en vivo
- **[ib_async](https://github.com/ib-api-reloaded/ib_async)** (1.7k★): el estándar retail para CME vía Interactive Brokers (sucesor mantenido de ib_insync); `ContFuture` para ES/NQ, brackets.
- **[async_rithmic](https://github.com/rundef/async_rithmic)** (113★, activo): Rithmic (la infraestructura de AMP/Apex y la mayoría de prop firms) con ticks, DOM L2 y routing, reconexión automática.
- **[project-x-py](https://github.com/TexasCoding/project-x-py)** (33★, 481 commits, 1.300+ tests): SDK async de ProjectX/**TopstepX** — datos, orderbook L2, indicadores, riesgo; el más completo para cuentas de fondeo.
- **[Tradovate example-api-trading-strategy](https://github.com/tradovate/example-api-trading-strategy)** (oficial) y [dearvn/tradovate-trading-bot](https://github.com/dearvn/tradovate-trading-bot) (58★; CVD + volume profile + clasificación de régimen apuntando a ES/NQ/MES/MNQ — sustituye sus señales de TradingView por las tuyas de gamma). Ojo: el acceso API individual a Tradovate exige licencia CME de pago desde 2023.
- **NinjaTrader 8**: [MicroTrendsLtd/NinjaTrader8](https://github.com/MicroTrendsLtd/NinjaTrader8) (111★, base de estrategias unmanaged con brackets) y [CSharp-NT8-OrderFlowKit](https://github.com/gbzenobi/CSharp-NT8-OrderFlowKit) (347★, bookmap/footprint/CVD gratis sobre el feed de CME).

---

## 8. Gestión de riesgo y position sizing (el hueco que la crítica detectó)

Con NQ a $20/punto un solo contrato mueve cientos de dólares por minuto en apertura; esta capa no es opcional:

- **[quantstats](https://github.com/ranaroussi/quantstats)** (7.6k★): tearsheets, VaR/CVaR, drawdowns y Monte Carlo sobre tu curva de equity — el auditor estándar de cualquier backtest antes de apalancar.
- **[riskguard](https://github.com/SilentFleetKK/riskguard)** (64★, reciente): middleware sin dependencias con límites por activo, **circuit breaker por drawdown** y límite de pérdida diaria que sobreviven reinicios (SQLite + auditoría HMAC) — "disciplina escrita en código" entre tu señal y el broker.
- **[mc_sim_fin](https://github.com/gaugau3000/mc_sim_fin)**: remuestrea tus trades y responde la pregunta central: *¿probabilidad de ruina con esta cuenta y este sizing?*
- **[risk_normalization](https://github.com/howardbandy/risk_normalization)** (Howard Bandy): `safe-f` (fracción máxima segura por trade dada tu tolerancia a drawdown) y CAR25; reduce contratos automáticamente cuando el sistema se degrada.
- **[KellyPortfolio](https://github.com/thk3421-models/KellyPortfolio)** (96★): Kelly con covarianzas y el argumento clave del Kelly fraccional: 1/4 de Kelly ≈ −20% retorno, −80% varianza — la defensa contra el error de estimación del edge.
- **[systematictradingexamples](https://github.com/robcarver17/systematictradingexamples)** (Carver): volatility targeting pedagógico — de "quiero X% de vol anual" a número de contratos.
- **[prop-firm-simulator](https://github.com/gabrielee5/prop-firm-simulator)**: modela límites diarios/trailing drawdown de cuentas de fondeo (Topstep/Apex) y barre riesgo por trade × R:R para maximizar la probabilidad de pasar y sobrevivir.
- **[dynamic-position-sizer-atr-calculator](https://github.com/leionion/dynamic-position-sizer-atr-calculator)**: el flujo práctico intradía — riesgo fijo (1%), stop a N×ATR, contratos = floor(riesgo/(dist_stop × valor_por_punto)).
- Reglas mínimas del corpus: riesgo 0.5–1% por trade; micros (MNQ $2, MES $5) hasta que el sizing por volatilidad dé ≥1 contrato estándar; límite de pérdida diaria codificado (el bot de 0dte-strategy usa $500/día y máx. 1 posición).

---

## 9. Hoja de ruta concreta para `yahora`

Tu estado actual: FastAPI que proxya `api.gexbot.com/v1/levels?symbol=NQ` con caché de 5 s + frontend Plotly. Fases incrementales, cada una útil por sí sola:

**Fase 1 — Pipeline GEX propio y gratuito (independencia de GEXBot).**
Ingesta CBOE delayed de `_NDX` y `_SPX` (patrón `gex_data`/Darthreign: endpoint CDN + regex OCC), griegas con `py_vollib_vectorized`, GEX naive por strike, **flip por rejilla ±8%/161 pasos con interpolación** (Darthreign `zero_gamma()`), muros GEX1–5, y conversión por basis a NQ/ES (patrón Gex-Multi). Mantén GEXBot como contraste. Servir un JSON estable tipo `{gex_profile, zero_gamma, call_wall, put_wall, sd_bands, régimen}` (contrato de datos de Hewkaw02 `update_dashboard.py`) y pintar niveles en el frontend. Añade el filtro de calidad de datos (spread ≤50% mid, bid>0, vol ≤2×OI) antes de agregar.

**Fase 2 — Vencimientos, vanna/charm y régimen.**
Buckets {0DTE, 1–7, 8–30, 31+} (0dte-strategy) para separar el pin intradía del posicionamiento mensual; VEX/CHEX por strike (gflows como referencia); bandas de expected move (±1σ/2σ con suelo 1 DTE); régimen gamma (>0/<0) publicado como campo del JSON; histórico en Parquet para poder backtestear tus propios niveles después.

**Fase 3 — Vía nativa CME.**
Cuenta Tastytrade gratuita + DXLink para cadenas /ES /NQ (trasplanta `analytics/exposure.py` de Hewkaw02 corrigiendo la firma a naive/direccional configurable), o Databento `ES.OPT`/`NQ.OPT` (`definition` + `statistics` + `trades`, Black-76, mult 50/20). OI oficial gratuito vía settlements de CME (patrón CMEOptions, corregido). Valora extraer `IntradayGexEngine` de gex-terminal como librería para el GEX intradía por volumen/agresor.

**Fase 4 — Señales y backtest.**
Implementa E1 (regla del put wall) y E2 (régimen como filtro de un ORB/bandas de ruido); backtestea con datos 1-min (Databento `NQ.v.0`/`ES.v.0` es barato en OHLCV) usando vectorbt para el barrido y el protocolo del §5 para la validación (plantilla: `stats.py` y reports de zarattini-2024). Después: confirmación por order flow (E5) y, solo con señal validada, meta-labeling (E7).

**Fase 5 — Riesgo y ejecución.**
riskguard (límite diario + circuit breaker) + sizing por volatilidad/safe-f; ejecución paper con ib_async o Tradovate demo; micros primero. Si prop firm: project-x-py (TopstepX) y prop-firm-simulator para calibrar el riesgo por trade.

---

## 10. Advertencias transversales

1. **Convención naive debilitada** (gamma neto de dealers ≈ 0 en 2024–26, SSRN 4881008): trata el signo como hipótesis configurable (naive / uniforme / direccional por agresor) y compara.
2. **El OI es EOD** en todas las fuentes (CBOE, settlements CME, Databento statistics): tus muros intradía se apoyan en el posicionamiento de ayer; solo el spot/precios/volumen refrescan intradía.
3. **Delayed ~15 min vale para niveles, no para flujo**: los muros y el flip cambian despacio (dependen de OI); el delta-flow 1-min exige feed en vivo (DXLink/Databento).
4. **Compresión de edge post-2025** documentada de forma independiente por dos repos serios: exige validación en datos 2025–26 antes de arriesgar nada.
5. **Licencias**: MIT/Apache en casi todo lo recomendado; excepciones: nautilus_trader LGPL-3.0 (ok para uso propio), CMEOptions GPL-3.0 (reimplementa el patrón), 0DTE-dealer-gamma PolyForm Noncommercial, **nas-orb-backtester sin licencia** (solo referencia de diseño).
6. **Bus factor 1** en los repos de nicho (gex-terminal, Hewkaw02, Darthreign): extrae los módulos que necesites a tu repo en vez de depender de ellos como dependencia viva.
7. **Sesiones**: casi todo el corpus es RTH 09:30–16:00 ET; ninguna σ_t/banda/perfil se traslada a ETH sin recalcular. Decide explícitamente el reset de estado (apertura RTH vs reapertura Globex 18:00 ET).
8. Varios repos prometedores están **muertos** (OrderRejected/gamma-vanna-charm — zonas GEX/VEX/CEX sobre ES/NQ en NinjaTrader — borrado; hedge0/VwapProject, kesanir/GEX-Charts: 404): no persigas esas URLs.

---

## 11. Índice completo de los 93 repositorios verificados

### GEX y posicionamiento de dealers
| Repo | ★ | Qué aporta |
|---|---|---|
| [zrack/gex-terminal](https://github.com/zrack/gex-terminal) | 13 | GEX intradía nativo ES/NQ (Black-76, Databento/Tradovate/IBKR, modo direccional) — §3.1 |
| [Hewkaw02/Futures-Options-SD-Dashboard](https://github.com/Hewkaw02/Futures-Options-SD-Dashboard) | 7 | GEX Black-76 sobre /ES /NQ vía Tastytrade DXLink, vanna/charm, bandas SD — §3.2 |
| [Darthreign/gex-dashboard](https://github.com/Darthreign/gex-dashboard) | 10 | GEX/DEX CBOE gratis, mejor zero-gamma, futopt dxFeed, MCP server — §3.3 |
| [jensolson/SPX-Gamma-Exposure](https://github.com/jensolson/SPX-Gamma-Exposure) | 164 | El clásico de referencia; análisis de sensibilidad al supuesto de dealers |
| [Matteo-Ferrara/gex-tracker](https://github.com/Matteo-Ferrara/gex-tracker) | 209 | GEX de CBOE sin API keys; el más estrellado; base sencilla y auditable |
| [gammagrid/gammagrid](https://github.com/gammagrid/gammagrid) | 52 | Dashboard Streamlit+Postgres: walls, flip, max pain, vanna/charm, replay histórico |
| [phammings/SPX500-Gamma-Exposure-Calculator](https://github.com/phammings/SPX500-Gamma-Exposure-Calculator) | 40 | Implementación limpia de la metodología Perfiliev (validar tu flip) |
| [puneet-chandna/0DTE-dealer-gamma](https://github.com/puneet-chandna/0DTE-dealer-gamma) | 4 | 0DTE SPX en tiempo real (FastAPI+Next.js, WebSocket 5 s); PolyForm NC |
| [Timonkru/gamma-seed](https://github.com/Timonkru/gamma-seed) | 2 | Niveles diarios → indicador Pine Script en TradingView (QQQ proxy de NQ) |
| [warrofua/gamma_studies](https://github.com/warrofua/gamma_studies) | 8 | Monitoreo de CAMBIOS de gamma intradía (API Schwab) |
| [FlashAlpha-lab/gex-explained](https://github.com/FlashAlpha-lab/gex-explained) | 8 | Teoría GEX con código ejecutable (parte marketing de su API) |
| [itsfabtrading/Gex-Multi](https://github.com/itsfabtrading/Gex-Multi) | 2 | Conversión por basis NDX/QQQ→NQ y SPX→ES — la pieza del mapeo |
| [solaris0675/MNQ-GEX](https://github.com/solaris0675/MNQ-GEX-gamma-exposure-levels-Free-) | 3 | GEX de QQQ ya convertido a precios NQ/MNQ, gratis |
| [Dax-giant/GEX](https://github.com/Dax-giant/GEX) | 1 | Notebooks NDX-GEX y SPX-GEX sobre la cadena del índice (no el ETF) |
| [GMestreM/gex_data](https://github.com/GMestreM/gex_data) | 7 | Motor CBOE modular MIT (base de Gex-Multi) |
| [The-R2D2-code/Quantower_GexBot_Gamma_Point](https://github.com/The-R2D2-code/Quantower_GexBot_Gamma_Point) | 12 | Niveles GexBot (NDX→NQ) pintados en Quantower; DLL sin fuente |

### Vanna, charm y 0DTE
| Repo | ★ | Qué aporta |
|---|---|---|
| [aaguiar10/gflows](https://github.com/aaguiar10/gflows) | 109 | Delta/gamma/vanna/charm por strike y vencimiento (SPX/NDX/RUT, CBOE gratis) |
| [YichengYang-Ethan/0dte-strategy](https://github.com/YichengYang-Ethan/0dte-strategy) | 11 | La señal validada + kill gates + hipótesis falsadas — §3.4 |
| [FlashAlpha-lab/0dte-options-analytics](https://github.com/FlashAlpha-lab/0dte-options-analytics) | 10 | Pin risk, régimen, expected move, flujos charm/theta (depende de su API) |
| [thunderscarf/SPX_0DTE_Options_Selling_Public](https://github.com/thunderscarf/SPX_0DTE_Options_Selling_Public) | 1 | Expected move con VIX1D + backtest real (78% win, 537 días) |
| [Grefer/DeltaLab](https://github.com/Grefer/DeltaLab) | 14 | Laboratorio de delta hedging dinámico (simular flujos de dealers) |
| [G12-maker/0DTE-lab](https://github.com/G12-maker/0DTE-lab) | 4 | Backtesting 0DTE específico de QQQ (el complejo que pina NQ) |
| [vilkovgr/0dte-strategies](https://github.com/vilkovgr/0dte-strategies) | 50 | Replicación académica 0DTE SPXW 2016–2026 **con paneles de datos incluidos** (Git LFS) |
| [FlashAlpha-lab/awesome-options-analytics](https://github.com/FlashAlpha-lab/awesome-options-analytics) | 36 | Índice curado del nicho (GEX/DEX/VEX/CHEX, datos, papers) |

### Estrategias intradía de futuros
| Repo | ★ | Qué aporta |
|---|---|---|
| [giovannibrusco/zarattini-2024-momentum-spy](https://github.com/giovannibrusco/zarattini-2024-momentum-spy) | 0 | "Beat the Market" replicado en SPY **y ES**, con la verdad del edge post-2025 — §3.5 |
| [Branly76/Intraday-strategy-Beat-the-market-for-SPY-](https://github.com/Branly76/Intraday-strategy-Beat-the-market-for-SPY-) | 5 | Versión simple con 3 años de datos SPY incluidos (para entender la mecánica) |
| [giovannibrusco/zarattini-2023-orb-qqq](https://github.com/giovannibrusco/zarattini-2023-orb-qqq) | 0 | ORB 5-min del paper "Can Day Trading Really Be Profitable?" sobre QQQ, con bootstrap |
| [dws-data/nas-orb-backtester](https://github.com/dws-data/nas-orb-backtester) | 3 | ORB 15-min + retroceso a VP, nativo de NQ — §3.6 |
| [quantrocket-codeload/trend-day](https://github.com/quantrocket-codeload/trend-day) | 28 | "Trend day" de Ernie Chan (momentum de fin de sesión) |
| [quantrocket-codeload/calspread](https://github.com/quantrocket-codeload/calspread) | — | Mean reversion intradía en futuros con barras bid/ask de IB |
| [thomas-quant/mss-research](https://github.com/thomas-quant/mss-research) | 0 | Event-study Polars de conceptos ICT/SMC sobre ES/NQ 1-min, bucketing por sesión |
| [eslazarev/vwap-backtrader](https://github.com/eslazarev/vwap-backtrader) | 22 | VWAP con reset de sesión + rolling para Backtrader (testeado, CI) |
| [dearvn/tradovate-trading-bot](https://github.com/dearvn/tradovate-trading-bot) | 58 | Bot Tradovate ES/NQ con CVD, volume profile y clasificación de régimen |
| [tradovate/example-api-trading-strategy](https://github.com/tradovate/example-api-trading-strategy) | 31 | Plantilla oficial del broker (websockets, brackets, isAutomated) |
| [TexasCoding/project-x-py](https://github.com/TexasCoding/project-x-py) | 33 | SDK async TopstepX/ProjectX: datos, L2, indicadores, riesgo (1.300+ tests) |
| [MicroTrendsLtd/NinjaTrader8](https://github.com/MicroTrendsLtd/NinjaTrader8) | 111 | Base de estrategias unmanaged NT8 (brackets, scale-out) |

### Order flow, volume profile y microestructura
| Repo | ★ | Qué aporta |
|---|---|---|
| [srlcarlg/srl-python-indicators](https://github.com/srlcarlg/srl-python-indicators) | 48 | Footprint/delta/VP/TPO/VWAP en un paquete (parchear con agresor real) — §3.7 |
| [murtazayusuf/OrderflowChart](https://github.com/murtazayusuf/OrderflowChart) | 250 | Footprints interactivos en Plotly (archivado pero funcional) |
| [tysonwu/stack-orderflow](https://github.com/tysonwu/stack-orderflow) | 138 | GUI de footprint (PyQt6/finplot) tipo Exocharts |
| [bfolkens/py-market-profile](https://github.com/bfolkens/py-market-profile) | 403 | POC/VAH/VAL/IB desde pandas — la vía más rápida a value areas |
| [beinghorizontal/tpo_project](https://github.com/beinghorizontal/tpo_project) | 133 | TPO en vivo con Dash (tu mismo stack de dashboard) |
| [gbzenobi/CSharp-NT8-OrderFlowKit](https://github.com/gbzenobi/CSharp-NT8-OrderFlowKit) | 347 | Bookmap/footprint/CVD gratis en NinjaTrader 8 |
| [jialuechen/flowpylib](https://github.com/jialuechen/flowpylib) | 139 | Inferencia de metaórdenes, cambios de régimen del flujo, TCA |
| [nicolezattarin/LOB-feature-analysis](https://github.com/nicolezattarin/LOB-feature-analysis) | 277 | OFI/MLOFI, PIN — features académicas del libro para MBP-10/MBO |
| [DJ824/orderbook-reconstruction](https://github.com/DJ824/orderbook-reconstruction) | 29 | Reconstrucción del libro desde MBO de Databento en C++ |

### Frameworks de backtesting y ejecución
| Repo | ★ | Qué aporta |
|---|---|---|
| [nautechsystems/nautilus_trader](https://github.com/nautechsystems/nautilus_trader) | 26.7k | La pila de referencia CME (Databento L3 + IB live) — §3.8 |
| [QuantConnect/Lean](https://github.com/QuantConnect/Lean) | 21.3k | Continuos/rolls/márgenes/sesiones resueltos; datos en su nube |
| [mementum/backtrader](https://github.com/mementum/backtrader) | 22.9k | Backtest con margen/mult correcto; congelado |
| [polakowo/vectorbt](https://github.com/polakowo/vectorbt) | 8.7k | Barridos masivos en la fase de investigación |
| [nkaz001/hftbacktest](https://github.com/nkaz001/hftbacktest) | 4.4k | Colas/latencias sobre MBO (backtest de ejecución realista) |
| [robcarver17/pysystemtrade](https://github.com/robcarver17/pysystemtrade) | 3.4k | Rolls y producción de futuros con IB (Carver) |
| [stefan-jansen/zipline-reloaded](https://github.com/stefan-jansen/zipline-reloaded) | 1.9k | ContinuousFuture mantenido; investigación |
| [barter-rs/barter-rs](https://github.com/barter-rs/barter-rs) | 2.2k | Motor Rust (sin conectores CME; solo como arquitectura) |
| [ib-api-reloaded/ib_async](https://github.com/ib-api-reloaded/ib_async) | 1.7k | Conector IB estándar (sucesor de ib_insync) |
| [rundef/async_rithmic](https://github.com/rundef/async_rithmic) | 113 | Rithmic async (AMP/prop firms): ticks, DOM, órdenes |

### Datos de opciones y griegas
| Repo | ★ | Qué aporta |
|---|---|---|
| [databento/databento-python](https://github.com/databento/databento-python) | 293 | GLBX.MDP3 + OPRA: la fuente canónica — §3.9 |
| [vollib/py_vollib](https://github.com/vollib/py_vollib) | — | IV "Let's Be Rational" + griegas Black/BS/BSM (la referencia) |
| [marcdemers/py_vollib_vectorized](https://github.com/marcdemers/py_vollib_vectorized) | 160 | Griegas de cadenas completas en milisegundos (incluye Black-76) |
| [polygon-io/client-python](https://github.com/polygon-io/client-python) | 1.5k | Snapshot de cadena con griegas y OI (plan de índices para SPX/NDX) |
| [pythonfortraders/thetadata-api-python](https://github.com/pythonfortraders/thetadata-api-python) | 8 | Wrapper REST vigente de ThetaData (el oficial está deprecado) |
| [Proshotv2/Gamma-Vanna-Options-Exposure](https://github.com/Proshotv2/Gamma-Vanna-Options-Exposure) | 19 | GEX/VEX vía API de Tradier (patrón broker-API) |
| [VandersonTorres/gamma-exposure-indicator](https://github.com/VandersonTorres/gamma-exposure-indicator) | 2 | CBOE vía Playwright incluyendo **opciones del futuro ES**; export Pine Script |
| [mcdallas/wallstreet](https://github.com/mcdallas/wallstreet) | 1.7k | Cadenas con griegas en pocas líneas (prototipos/contraste) |
| [CJuanvip/CMEOptions](https://github.com/CJuanvip/CMEOptions) | 6 | OI oficial de opciones sobre futuros desde settlements de CME — §3.10 |
| [theoddden/Option-Positioning-Ratios](https://github.com/theoddden/Option-Positioning-Ratios) | 7 | PCR filtrado por liquidez, OI nocional, skew shifts, concentración de gamma |
| [simonlin1212/global-stock-data](https://github.com/simonlin1212/global-stock-data) | 1.5k | 11 fuentes oficiales sin API key: CBOE con griegas, 0DTE flow, COT CFTC |

### ML y quant research
| Repo | ★ | Qué aporta |
|---|---|---|
| [stefan-jansen/machine-learning-for-trading](https://github.com/stefan-jansen/machine-learning-for-trading) | 20.6k | Casos de futuros CME, NASDAQ-100 intradía y opciones S&P; walk-forward |
| [hudson-and-thames/mlfinlab](https://github.com/hudson-and-thames/mlfinlab) | 4.9k | Triple barrier, meta-labeling, purged CV (rama pública) |
| [BlackArbsCEO/Adv_Fin_ML_Exercises](https://github.com/BlackArbsCEO/Adv_Fin_ML_Exercises) | 2k | AFML paso a paso, 100% abierto |
| [hudson-and-thames/meta-labeling](https://github.com/hudson-and-thames/meta-labeling) | 103 | 4 papers JFDS: el puente señal-GEX → filtro ML + sizing |
| [AI4Finance-Foundation/FinRL](https://github.com/AI4Finance-Foundation/FinRL) | 11k | RL financiero estándar (inyectar features GEX/IV en el estado) |
| [onesamblack/futures-trading-gym](https://github.com/onesamblack/futures-trading-gym) | 34 | Gym con economía real de ES (tick 0.25, $12.50/tick) |
| [Invest-In-a-Tech/Machine-Learning-ES-Emini-Futures](https://github.com/Invest-In-a-Tech/Machine-Learning-ES-Emini-Futures) | 22 | ML/RL sobre ES con footprint de Sierra Chart (absorción) |
| [NavnoorBawa/Options-Flow-Predictor](https://github.com/NavnoorBawa/Options-Flow-Predictor) | 24 | GEX/PCR/flujo como features de RF+XGBoost (diseño de features) |
| [iAmGiG/gex-llm-patterns](https://github.com/iAmGiG/gex-llm-patterns) | 26 | Hallazgo: el perfil por strike > GEX escalar (81.8M contratos, papers en revisión) |
| [taylorjmellon/market-regime-detection](https://github.com/taylorjmellon/market-regime-detection) | 2 | HMM + K-Means como plantilla de detección de regímenes |

### Volatilidad y régimen
| Repo | ★ | Qué aporta |
|---|---|---|
| [jasonstrimpel/volatility-trading](https://github.com/jasonstrimpel/volatility-trading) | 1.9k | Estimadores RV (Yang-Zhang, etc.) + conos → VRP e IV-rank propio |
| [dougransom/vix_utils](https://github.com/dougransom/vix_utils) | 63 | Estructura temporal completa de futuros VIX desde CBOE (gratis) |
| [QuantConnect/Tutorials](https://github.com/QuantConnect/Tutorials) | 738 | Estrategia #198: término del VIX (roll ±0.10) con cobertura en E-mini |
| [anthonymakarewicz/volatility-trading](https://github.com/anthonymakarewicz/volatility-trading) | 37 | VRP accionable + forecasting HAR-RV-VIX (~30% R² OOS) |
| [lambdaclass/options_portfolio_backtester](https://github.com/lambdaclass/options_portfolio_backtester) | 263 | Backtester de opciones con timing por señales y tail hedges |
| [je-suis-tm/quant-trading](https://github.com/je-suis-tm/quant-trading) | 10.6k | VIX Calculator (construir un "VIX de NDX" propio) + colección general |
| [peterchettiar/trading-volatility](https://github.com/peterchettiar/trading-volatility) | 10 | Contango/backwardation → reglas concretas (CSVs incluidos) |
| [kurupjayesh/Dispersion-Trading-using-Options](https://github.com/kurupjayesh/Dispersion-Trading-using-Options) | 33 | Correlación implícita como señal de régimen |
| [meixler/vix](https://github.com/meixler/vix) | 26 | Réplica exacta del white paper del VIX (validación de IV propia) |

### Riesgo y position sizing
| Repo | ★ | Qué aporta |
|---|---|---|
| [ranaroussi/quantstats](https://github.com/ranaroussi/quantstats) | 7.6k | Tearsheets, CVaR, Monte Carlo de ruina |
| [SilentFleetKK/riskguard](https://github.com/SilentFleetKK/riskguard) | 64 | Circuit breakers y límites diarios persistentes |
| [thk3421-models/KellyPortfolio](https://github.com/thk3421-models/KellyPortfolio) | 96 | Kelly fraccional con covarianzas |
| [gaugau3000/mc_sim_fin](https://github.com/gaugau3000/mc_sim_fin) | 51 | Probabilidad de ruina desde tus trades |
| [howardbandy/risk_normalization](https://github.com/howardbandy/risk_normalization) | — | safe-f y CAR25 (sizing dinámico) |
| [robcarver17/systematictradingexamples](https://github.com/robcarver17/systematictradingexamples) | 489 | Volatility targeting para futuros (pedagógico) |
| [gabrielee5/prop-firm-simulator](https://github.com/gabrielee5/prop-firm-simulator) | 0 | Optimizar riesgo bajo reglas de prop firm |
| [leionion/dynamic-position-sizer-atr-calculator](https://github.com/leionion/dynamic-position-sizer-atr-calculator) | 13 | Sizing por ATR consciente del valor por tick |

---

*Informe generado a partir de una investigación multi-agente (22 agentes, 427 consultas web) el 2026-08-21. Las estrellas y fechas de actividad son aproximadas a esa fecha. Nada de esto es consejo financiero; todos los edges citados requieren validación propia con datos actuales antes de arriesgar capital.*
