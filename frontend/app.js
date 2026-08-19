/* Sweeps — web indicator
 *
 * Liquidity-heatmap chart (time →, price ↑) with a clean-room web
 * implementation of a sweep-orders indicator: bursts of aggressive trades
 * that consume several price levels are marked with (1) trade dots at the
 * swept prints and (2) an offset icon connected by a dotted line, labeled
 * with total volume. Nearby same-side icons cluster into one aggregated
 * icon. Detection runs server-side; data arrives over a websocket from the
 * demo generator, Databento live, or Databento historical replay.
 */

"use strict";

// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------

const S = {
  ws: null,
  wantRun: false,          // user pressed Start (drives auto-reconnect)
  cfg: null,               // last start message sent
  run: 0,                  // feed generation id (stale-event guard)
  speed: 1,                // replay speed, for clock extrapolation

  cols: [],                // [{t, bids:[[px,sz]], asks:[[px,sz]], bb, ba}]
  trades: [],              // [{t, px, sz, side}]
  sweeps: [],              // detected sweeps (see ingestSweep)
  nextSweepId: 1,

  dataNow: 0,              // latest event time (ms, data clock)
  wallAtDataNow: 0,        // performance.now() when dataNow was set

  tick: 0,                 // detected price grid step
  center: 0,               // EMA-followed mid price
  priceSpan: 0,            // visible price range (price units)
  timeSpan: 60_000,        // visible time range (ms)

  maxDepthSz: 50,          // rolling normalizer for heatmap intensity
  lastPx: 0, lastSide: "N",
  feedLabel: "", contract: null,   // "live · NQ.v.0" → "NQZ6"
  nBuy: 0, nSell: 0, vBuy: 0, vSell: 0,
  highlight: null,         // {id, until}
  clusters: [],            // recomputed every frame (for hit-testing)
  dragIcon: null,          // {startY, startOffset} while dragging icons
};

const $ = (id) => document.getElementById(id);
const canvas = $("chart");
const ctx = canvas.getContext("2d");

// ---------------------------------------------------------------------------
// Instrumentos. El sufijo ".v.0" es la symbology continua de Databento para
// "el contrato con más volumen del día anterior", es decir el que realmente
// se está operando — el vigente. Al rolar, Databento avisa con un mensaje de
// mapeo y la app muestra el contrato nuevo sin que haya que tocar nada.
// ---------------------------------------------------------------------------

const INSTRUMENTS = [
  { sym: "ES.v.0", name: "S&P 500 — ES" },
  { sym: "NQ.v.0", name: "Nasdaq 100 — NQ" },
  { sym: "MES.v.0", name: "Micro S&P — MES" },
  { sym: "MNQ.v.0", name: "Micro Nasdaq — MNQ" },
  { sym: "YM.v.0", name: "Dow — YM" },
  { sym: "RTY.v.0", name: "Russell 2000 — RTY" },
  { sym: "CL.v.0", name: "Petróleo — CL" },
  { sym: "GC.v.0", name: "Oro — GC" },
  { sym: "NG.v.0", name: "Gas natural — NG" },
  { sym: "ZN.v.0", name: "Bono 10 años — ZN" },
  { sym: "6E.v.0", name: "Euro — 6E" },
  { sym: "BTC.v.0", name: "Bitcoin — BTC" },
];

function fillInstruments(select, selected) {
  select.innerHTML =
    INSTRUMENTS.map((i) =>
      `<option value="${i.sym}"${i.sym === selected ? " selected" : ""}>` +
      `${i.name}</option>`).join("") +
    `<option value="custom">otro…</option>`;
}

const AXIS_W = 68, AXIS_H = 22;
const MAX_COLS = 6000, MAX_TRADES = 8000, MAX_SWEEPS = 600, MAX_LOG_ROWS = 200;

const buyColor = () => $("buycolor").value;
const sellColor = () => $("sellcolor").value;
const sideColor = (side) => (side === "B" ? buyColor() : sellColor());

function hexToRgba(hex, a) {
  const n = parseInt(hex.slice(1), 16);
  return `rgba(${(n >> 16) & 255},${(n >> 8) & 255},${n & 255},${a})`;
}

function applyCssColors() {
  document.documentElement.style.setProperty("--buy", buyColor());
  document.documentElement.style.setProperty("--sell", sellColor());
}

// ---------------------------------------------------------------------------
// Heatmap color LUT (dark navy → blue → cyan → yellow → white)
// ---------------------------------------------------------------------------

const LUT = (() => {
  const stops = [
    [0.00, [11, 14, 23]],
    [0.10, [13, 27, 62]],
    [0.35, [21, 72, 152]],
    [0.60, [0, 170, 213]],
    [0.82, [250, 220, 90]],
    [1.00, [255, 255, 255]],
  ];
  const lut = [];
  for (let i = 0; i < 256; i++) {
    const x = i / 255;
    let k = 0;
    while (k < stops.length - 2 && x > stops[k + 1][0]) k++;
    const [x0, c0] = stops[k], [x1, c1] = stops[k + 1];
    const f = Math.min(1, Math.max(0, (x - x0) / (x1 - x0)));
    const c = c0.map((v, j) => Math.round(v + (c1[j] - v) * f));
    lut.push(`rgb(${c[0]},${c[1]},${c[2]})`);
  }
  return lut;
})();

// ---------------------------------------------------------------------------
// WebSocket client
// ---------------------------------------------------------------------------

function wsUrl() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  return `${proto}://${location.host}/ws`;
}

function connect() {
  if (S.ws && (S.ws.readyState === 0 || S.ws.readyState === 1)) return;
  const ws = new WebSocket(wsUrl());
  S.ws = ws;
  ws.onopen = () => {
    setConn("on", "connected");
    if (S.wantRun && S.cfg) {
      // the server session restarts from scratch: reset local state and
      // bump the generation so leftovers from the old run are dropped
      S.run++;
      S.cfg.run = S.run;
      resetData();
      ws.send(JSON.stringify(S.cfg));
    }
  };
  ws.onmessage = (e) => {
    let batch;
    try { batch = JSON.parse(e.data); } catch { return; }
    if (!Array.isArray(batch)) batch = [batch];
    for (const ev of batch) ingest(ev);
  };
  ws.onclose = () => {
    setConn("off", "disconnected");
    S.ws = null;
    if (S.wantRun) setTimeout(connect, 2000);
  };
  ws.onerror = () => { /* onclose follows */ };
}

function detectorParams() {
  return {
    window_ms: +$("windowms").value || 0,
    min_levels: +$("minlevels").value || 2,
    min_size: +$("minsize").value || 0,
    auto_size: $("autosize").checked,
    sd_interval_min: +$("sdinterval").value || 5,
    sd_multiplier: +$("sdmult").value || 6,
  };
}

function sendStart() {
  const mode = $("mode").value;
  S.run++;
  const cfg = { type: "start", mode, run: S.run, params: detectorParams() };
  if (mode !== "demo") {
    cfg.dataset = $("dataset").value.trim();
    cfg.symbol = $("symbol").value.trim();
    cfg.stype_in = $("stype").value;
  }
  if (mode === "replay") {
    cfg.start = $("rstart").value.trim();
    cfg.end = $("rend").value.trim();
    cfg.speed = +$("rspeed").value || 1;
  }
  S.speed = mode === "replay" ? (cfg.speed || 1) : 1;
  S.cfg = cfg;
  S.wantRun = true;
  resetData();
  if (S.ws && S.ws.readyState === 1) S.ws.send(JSON.stringify(cfg));
  else connect();
  $("stopbtn").disabled = false;
}

function sendStop() {
  S.wantRun = false;
  if (S.ws && S.ws.readyState === 1) {
    S.ws.send(JSON.stringify({ type: "stop" }));
  }
  $("stopbtn").disabled = true;
}

function sendParams() {
  if (!S.ws || S.ws.readyState !== 1 || !S.wantRun) return;
  S.ws.send(JSON.stringify({ type: "params", params: detectorParams() }));
}

// ---------------------------------------------------------------------------
// Ingestion
// ---------------------------------------------------------------------------

function resetData() {
  S.cols = []; S.trades = []; S.sweeps = []; S.clusters = [];
  S.dataNow = 0; S.tick = 0; S.center = 0; S.priceSpan = 0;
  S.maxDepthSz = 50;
  S.lastPx = 0; S.lastSide = "N";
  S.nBuy = 0; S.nSell = 0; S.vBuy = 0; S.vSell = 0;
  S.highlight = null;
  $("sweeplog").innerHTML = "";
  updateStats();
}

function bumpClock(tMs) {
  if (tMs > S.dataNow) {
    S.dataNow = tMs;
    S.wallAtDataNow = performance.now();
  }
}

function ingest(ev) {
  // batches from a previous feed can still be in flight after a restart;
  // their stale wall-clock timestamps would pin the forward-only clock
  if (ev.run !== undefined && ev.run !== S.run) return;
  switch (ev.type) {
    case "snapshot": ingestSnapshot(ev); break;
    case "trade": ingestTrade(ev); break;
    case "sweep": ingestSweep(ev); break;
    case "status":
      if (ev.state === "running") {
        S.feedLabel = `${ev.mode} · ${ev.symbol}`;
        S.contract = null;
        setConn("on", S.feedLabel);
        $("feedinfo").textContent = ev.detail || "";
      } else if (ev.state === "mapped") {
        // Databento resolvió el símbolo continuo al contrato real en vigencia
        S.contract = ev.contract;
        setConn("on", `${S.feedLabel || ""} → ${ev.contract}`);
      } else if (ev.state === "loading") {
        setConn("on", "loading…");
        $("feedinfo").textContent = ev.detail || "";
      } else if (ev.state === "finished" || ev.state === "stopped") {
        $("feedinfo").textContent = ev.detail || "";
      }
      break;
    case "error":
      setConn("err", "error");
      $("feedinfo").textContent = ev.message || "unknown error";
      break;
  }
}

function ingestSnapshot(ev) {
  const t = ev.ts / 1e6;
  bumpClock(t);
  const bids = ev.bids || [], asks = ev.asks || [];
  const bb = bids.length ? bids[0][0] : 0;
  const ba = asks.length ? asks[0][0] : 0;
  S.cols.push({ t, bids, asks, bb, ba });
  if (S.cols.length > MAX_COLS) S.cols.splice(0, S.cols.length - MAX_COLS);

  // learn the price grid from adjacent ask levels
  if (asks.length >= 2) {
    let d = Infinity;
    for (let i = 1; i < asks.length; i++) {
      const dd = Math.abs(asks[i][0] - asks[i - 1][0]);
      if (dd > 1e-12 && dd < d) d = dd;
    }
    if (isFinite(d)) S.tick = S.tick ? Math.min(S.tick, d) : d;
  }

  const mid = bb && ba ? (bb + ba) / 2 : (bb || ba);
  if (mid) {
    S.center = S.center ? S.center + (mid - S.center) * 0.08 : mid;
    if (!S.priceSpan) S.priceSpan = S.tick ? S.tick * 44 : mid * 0.002;
  }

  let mx = 0;
  for (const [, sz] of bids) if (sz > mx) mx = sz;
  for (const [, sz] of asks) if (sz > mx) mx = sz;
  S.maxDepthSz = Math.max(S.maxDepthSz * 0.999, mx, 10);
}

function ingestTrade(ev) {
  const t = ev.ts / 1e6;
  bumpClock(t);
  S.trades.push({ t, px: ev.px, sz: ev.sz, side: ev.side });
  if (S.trades.length > MAX_TRADES) S.trades.splice(0, S.trades.length - MAX_TRADES);
  S.lastPx = ev.px;
  S.lastSide = ev.side;
}

function ingestSweep(ev) {
  const sw = {
    id: S.nextSweepId++,
    t0: ev.ts_start / 1e6, t1: ev.ts_end / 1e6,
    side: ev.side, size: ev.size, levels: ev.levels,
    pxMin: ev.px_min, pxMax: ev.px_max, vwap: ev.vwap,
    n: ev.trades, threshold: ev.threshold,
    prints: (ev.prints || []).map((p) => ({ t: p[0] / 1e6, px: p[1], sz: p[2] })),
    born: performance.now(),
  };
  bumpClock(sw.t1);
  S.sweeps.push(sw);
  if (S.sweeps.length > MAX_SWEEPS) S.sweeps.splice(0, S.sweeps.length - MAX_SWEEPS);
  if (sw.side === "B") { S.nBuy++; S.vBuy += sw.size; }
  else { S.nSell++; S.vSell += sw.size; }
  updateStats();
  logSweep(sw);
  if ($("soundalert").checked) playAlert(sw.side);
}

// ---------------------------------------------------------------------------
// Sound alert
// ---------------------------------------------------------------------------

let audioCtx = null;
let lastAlertAt = 0;

function playAlert(side) {
  const now = performance.now();
  if (now - lastAlertAt < 250) return;   // throttle bursts
  lastAlertAt = now;
  try {
    audioCtx = audioCtx || new (window.AudioContext || window.webkitAudioContext)();
    const o = audioCtx.createOscillator();
    const g = audioCtx.createGain();
    o.type = "sine";
    o.frequency.value = side === "B" ? 880 : 440;
    g.gain.setValueAtTime(0.12, audioCtx.currentTime);
    g.gain.exponentialRampToValueAtTime(0.001, audioCtx.currentTime + 0.25);
    o.connect(g).connect(audioCtx.destination);
    o.start();
    o.stop(audioCtx.currentTime + 0.26);
  } catch { /* audio unavailable */ }
}

// ---------------------------------------------------------------------------
// Sidebar log + stats
// ---------------------------------------------------------------------------

function fmtTime(ms) {
  const d = new Date(ms);
  return d.toLocaleTimeString([], { hour12: false }) +
    "." + String(Math.floor(ms % 1000)).padStart(3, "0").slice(0, 1);
}

let _dpForTick = -1, _dp = 2;

function fmtPx(px) {
  if (!isFinite(px)) return "–";
  if (S.tick !== _dpForTick) {
    // decimals that make the tick exact: 0.25 → 2, 0.01 → 2, 1 → 0
    _dpForTick = S.tick;
    _dp = 2;
    if (S.tick > 0) {
      let dp = 0;
      let t = S.tick;
      while (dp < 9 && Math.abs(t - Math.round(t)) > 1e-9) { t *= 10; dp++; }
      _dp = dp;
    }
  }
  return px.toFixed(_dp);
}

function updateStats() {
  $("nbuy").textContent = S.nBuy;
  $("nsell").textContent = S.nSell;
  $("vbuy").textContent = S.vBuy.toLocaleString();
  $("vsell").textContent = S.vSell.toLocaleString();
}

function logSweep(sw) {
  const log = $("sweeplog");
  const row = document.createElement("div");
  row.className = `swrow ${sw.side}`;
  row.innerHTML =
    `<span class="arrow">${sw.side === "B" ? "▲" : "▼"}</span>` +
    `<span class="sz">${sw.size.toLocaleString()}</span>` +
    `<span class="rng">${sw.levels} lvl · ${fmtPx(sw.pxMin)}→${fmtPx(sw.pxMax)}</span>` +
    `<span class="tm">${fmtTime(sw.t1)}</span>`;
  row.onclick = () => { S.highlight = { id: sw.id, until: performance.now() + 2500 }; };
  log.prepend(row);
  while (log.children.length > MAX_LOG_ROWS) log.lastChild.remove();
}

function setConn(cls, text) {
  $("conn").className = `dot ${cls}`;
  $("statustext").textContent = text;
}

// ---------------------------------------------------------------------------
// Rendering
// ---------------------------------------------------------------------------

let W = 0, H = 0, DPR = 1;

function resize() {
  DPR = window.devicePixelRatio || 1;
  const r = canvas.getBoundingClientRect();
  W = Math.max(1, Math.floor(r.width));
  H = Math.max(1, Math.floor(r.height));
  canvas.width = Math.floor(W * DPR);
  canvas.height = Math.floor(H * DPR);
}
new ResizeObserver(resize).observe($("chartwrap"));
resize();

function nowMs() {
  if (!S.dataNow) return 0;
  const extra = (performance.now() - S.wallAtDataNow) * S.speed;
  return S.dataNow + Math.min(extra, 3000 * S.speed);
}

function niceStep(raw, tick) {
  if (tick > 0) {
    const mult = [1, 2, 4, 5, 10, 20, 40, 50, 100, 200, 400, 500, 1000];
    for (const m of mult) if (tick * m >= raw) return tick * m;
    return tick * 2000;
  }
  const p = Math.pow(10, Math.floor(Math.log10(raw || 1)));
  for (const m of [1, 2, 5, 10]) if (p * m >= raw) return p * m;
  return p * 10;
}

function draw() {
  requestAnimationFrame(draw);
  ctx.setTransform(DPR, 0, 0, DPR, 0, 0);
  ctx.fillStyle = "#0b0e17";
  ctx.fillRect(0, 0, W, H);

  const plotW = W - AXIS_W, plotH = H - AXIS_H;
  const tNow = nowMs();
  if (!tNow || !S.center || !S.priceSpan) {
    ctx.fillStyle = "#31405e";
    ctx.font = "14px sans-serif";
    ctx.textAlign = "center";
    ctx.fillText("waiting for data — press Start", W / 2, H / 2);
    return;
  }

  const t0 = tNow - S.timeSpan;
  const pxPerMs = plotW / S.timeSpan;
  const pTop = S.center + S.priceSpan / 2;
  const pxPerUnit = plotH / S.priceSpan;
  const xOf = (t) => (t - t0) * pxPerMs;
  const yOf = (p) => (pTop - p) * pxPerUnit;

  ctx.save();
  ctx.beginPath();
  ctx.rect(0, 0, plotW, plotH);
  ctx.clip();

  if ($("showheat").checked) drawHeatmap(xOf, yOf, tNow, t0, plotW, plotH, pxPerUnit);
  drawBBO(xOf, yOf, plotW);
  if ($("showtrades").checked) drawTrades(xOf, yOf, t0);
  drawSweepMarks(xOf, yOf, t0, plotW, plotH);
  ctx.restore();

  drawPriceAxis(yOf, pTop, plotW, plotH);
  drawTimeAxis(xOf, t0, tNow, plotW, plotH);
}

function drawHeatmap(xOf, yOf, tNow, t0, plotW, plotH, pxPerUnit) {
  const cols = S.cols;
  if (!cols.length) return;
  let i0 = 0;
  while (i0 < cols.length - 1 && cols[i0 + 1].t < t0) i0++;
  const visible = cols.length - i0;
  const stride = Math.max(1, Math.floor(visible / Math.max(200, plotW / 2)));
  const lvlH = Math.max(1, (S.tick || S.priceSpan / 40) * pxPerUnit);
  const pTop = S.center + S.priceSpan / 2;
  const pBot = S.center - S.priceSpan / 2;

  for (let i = i0; i < cols.length; i += stride) {
    const col = cols[i];
    const next = cols[Math.min(cols.length - 1, i + stride)];
    const x0 = xOf(col.t);
    const x1 = i + stride >= cols.length ? xOf(tNow) : xOf(next.t);
    const w = Math.max(1, x1 - x0 + 0.5);
    if (x1 < 0 || x0 > plotW) continue;
    for (const book of [col.bids, col.asks]) {
      for (const [px, sz] of book) {
        if (px < pBot || px > pTop) continue;
        const v = Math.min(1, sz / S.maxDepthSz);
        const idx = Math.min(255, Math.floor(Math.pow(v, 0.6) * 255));
        if (idx < 6) continue;
        ctx.fillStyle = LUT[idx];
        ctx.fillRect(x0, yOf(px) - lvlH / 2, w, lvlH);
      }
    }
  }
}

function drawBBO(xOf, yOf, plotW) {
  const cols = S.cols;
  if (cols.length < 2) return;
  for (const key of ["ba", "bb"]) {
    ctx.beginPath();
    let started = false;
    let prevY = 0;
    for (const col of cols) {
      const v = col[key];
      if (!v) continue;
      const x = xOf(col.t);
      if (x < -5) continue;
      const y = yOf(v);
      if (!started) { ctx.moveTo(x, y); started = true; }
      else { ctx.lineTo(x, prevY); ctx.lineTo(x, y); }
      prevY = y;
    }
    ctx.strokeStyle = "rgba(120,150,200,.55)";
    ctx.lineWidth = 1;
    ctx.stroke();
  }
  if (S.lastPx) {
    const y = yOf(S.lastPx);
    ctx.strokeStyle = "rgba(255,255,255,.85)";
    ctx.lineWidth = 1;
    ctx.setLineDash([4, 3]);
    ctx.beginPath();
    ctx.moveTo(0, y); ctx.lineTo(plotW, y);
    ctx.stroke();
    ctx.setLineDash([]);
  }
}

function drawTrades(xOf, yOf, t0) {
  for (const tr of S.trades) {
    if (tr.t < t0) continue;
    const x = xOf(tr.t), y = yOf(tr.px);
    const r = Math.min(5, 1.2 + Math.sqrt(tr.sz) * 0.35);
    ctx.beginPath();
    ctx.arc(x, y, r, 0, Math.PI * 2);
    ctx.fillStyle = tr.side === "B" ? hexToRgba(buyColor(), 0.45)
      : tr.side === "A" ? hexToRgba(sellColor(), 0.45) : "rgba(160,170,190,.4)";
    ctx.fill();
  }
}

// ---- sweep marks: trade dots + offset icons with dotted connectors --------

function drawSweepMarks(xOf, yOf, t0, plotW, plotH) {
  const showIcons = $("showicons").checked;
  const showVolume = $("showvolume").checked;
  const showDots = $("showdots").checked;
  const iconSize = +$("iconsize").value;
  const iconOffset = +$("iconoffset").value;
  const lineWidth = +$("linewidth").value;
  const dotSize = +$("dotsize").value;
  const dotShape = $("dotshape").value;

  const visible = [];
  for (const sw of S.sweeps) {
    const x = xOf(sw.t1);
    if (x < -60 || sw.t1 < t0 - 3000) continue;
    visible.push(sw);
    sw._x = Math.min(x, plotW);
    // anchor: the edge of the swept run the icon hangs off
    sw._anchorPx = sw.side === "B" ? sw.pxMin : sw.pxMax;
  }

  if (showDots) {
    for (const sw of visible) {
      ctx.fillStyle = sideColor(sw.side);
      ctx.strokeStyle = "rgba(0,0,0,.6)";
      ctx.lineWidth = 1;
      for (const p of sw.prints) {
        const x = xOf(p.t), y = yOf(p.px);
        if (dotShape === "rect") {
          ctx.fillRect(x - dotSize / 2, y - dotSize / 2, dotSize, dotSize);
          ctx.strokeRect(x - dotSize / 2, y - dotSize / 2, dotSize, dotSize);
        } else {
          ctx.beginPath();
          ctx.arc(x, y, dotSize / 2, 0, Math.PI * 2);
          ctx.fill();
          ctx.stroke();
        }
      }
    }
  }

  S.clusters = [];
  if (!showIcons) return;

  // cluster same-side sweeps whose icons would overlap
  const gap = iconSize + 6;
  const byTime = [...visible].sort((a, b) => a.t1 - b.t1);
  for (const sw of byTime) {
    const y = yOf(sw._anchorPx);
    let target = null;
    for (const cl of S.clusters) {
      if (cl.side === sw.side && Math.abs(sw._x - cl.x) <= gap &&
          Math.abs(y - cl.yAnchor) <= gap * 2) { target = cl; break; }
    }
    if (target) {
      target.members.push(sw);
      target.size += sw.size;
      target.x = Math.max(target.x, sw._x);
      target.yAnchor = (target.yAnchor * (target.members.length - 1) + y) /
        target.members.length;
      target.born = Math.max(target.born, sw.born);
    } else {
      S.clusters.push({ side: sw.side, x: sw._x, yAnchor: y, size: sw.size,
                        members: [sw], born: sw.born });
    }
  }

  const wallNow = performance.now();
  for (const cl of S.clusters) {
    const dir = cl.side === "B" ? 1 : -1;        // buys below, sells above
    const yIcon = cl.yAnchor + dir * iconOffset;
    cl.yIcon = yIcon;
    cl.r = iconSize / 2;
    const color = sideColor(cl.side);

    // dotted connector from icon to the swept run
    ctx.strokeStyle = hexToRgba(color, 0.8);
    ctx.lineWidth = lineWidth;
    ctx.setLineDash([2, 4]);
    ctx.beginPath();
    ctx.moveTo(cl.x, yIcon - dir * cl.r);
    ctx.lineTo(cl.x, cl.yAnchor);
    ctx.stroke();
    ctx.setLineDash([]);

    // icon: filled circle with aggressor arrow
    ctx.beginPath();
    ctx.arc(cl.x, yIcon, cl.r, 0, Math.PI * 2);
    ctx.fillStyle = hexToRgba(color, 0.9);
    ctx.fill();
    ctx.lineWidth = 1.5;
    ctx.strokeStyle = "rgba(0,0,0,.55)";
    ctx.stroke();

    const a = cl.r * 0.52;                       // arrow glyph
    ctx.beginPath();
    if (cl.side === "B") {
      ctx.moveTo(cl.x, yIcon - a);
      ctx.lineTo(cl.x - a, yIcon + a * 0.8);
      ctx.lineTo(cl.x + a, yIcon + a * 0.8);
    } else {
      ctx.moveTo(cl.x, yIcon + a);
      ctx.lineTo(cl.x - a, yIcon - a * 0.8);
      ctx.lineTo(cl.x + a, yIcon - a * 0.8);
    }
    ctx.closePath();
    ctx.fillStyle = "#0b0e17";
    ctx.fill();

    // arrival pulse
    const age = wallNow - cl.born;
    if (age < 1200) {
      const f = age / 1200;
      ctx.beginPath();
      ctx.arc(cl.x, yIcon, cl.r + f * 22, 0, Math.PI * 2);
      ctx.strokeStyle = hexToRgba(color, (1 - f) * 0.55);
      ctx.lineWidth = 2;
      ctx.stroke();
    }

    // volume label beside the icon
    if (showVolume) {
      ctx.font = `bold ${Math.max(10, Math.min(15, iconSize * 0.62))}px sans-serif`;
      ctx.textBaseline = "middle";
      const label = cl.size.toLocaleString();
      const tw = ctx.measureText(label).width;
      const rightRoom = cl.x + cl.r + 6 + tw < plotW - 4;
      ctx.textAlign = rightRoom ? "left" : "right";
      const lx = rightRoom ? cl.x + cl.r + 6 : cl.x - cl.r - 6;
      ctx.fillStyle = color;
      ctx.fillText(label, lx, yIcon);
    }

    // highlight from log click
    if (S.highlight && cl.members.some((m) => m.id === S.highlight.id)) {
      if (wallNow < S.highlight.until) {
        ctx.beginPath();
        ctx.arc(cl.x, yIcon, cl.r + 7, 0, Math.PI * 2);
        ctx.setLineDash([5, 4]);
        ctx.strokeStyle = "#ffffff";
        ctx.lineWidth = 1.5;
        ctx.stroke();
        ctx.setLineDash([]);
      } else S.highlight = null;
    }
  }
}

function drawPriceAxis(yOf, pTop, plotW, plotH) {
  ctx.fillStyle = "#0e1220";
  ctx.fillRect(plotW, 0, AXIS_W, H);
  ctx.strokeStyle = "#232a3d";
  ctx.beginPath();
  ctx.moveTo(plotW + 0.5, 0); ctx.lineTo(plotW + 0.5, plotH);
  ctx.stroke();

  const step = niceStep(S.priceSpan / 8, S.tick);
  const pBot = pTop - S.priceSpan;
  const first = Math.ceil(pBot / step) * step;
  ctx.font = "11px sans-serif";
  ctx.textAlign = "left";
  ctx.textBaseline = "middle";
  for (let p = first; p <= pTop; p += step) {
    const y = yOf(p);
    if (y < 8 || y > plotH - 4) continue;
    ctx.strokeStyle = "rgba(35,42,61,.6)";
    ctx.beginPath();
    ctx.moveTo(0, y); ctx.lineTo(plotW, y);
    ctx.stroke();
    ctx.fillStyle = "#7683a0";
    ctx.fillText(fmtPx(p), plotW + 6, y);
  }

  if (S.lastPx) {
    const y = yOf(S.lastPx);
    if (y > -10 && y < plotH + 10) {
      const c = S.lastSide === "B" ? buyColor()
        : S.lastSide === "A" ? sellColor() : "#8899bb";
      ctx.fillStyle = c;
      ctx.fillRect(plotW, y - 9, AXIS_W, 18);
      ctx.fillStyle = "#04121f";
      ctx.font = "bold 11px sans-serif";
      ctx.fillText(fmtPx(S.lastPx), plotW + 6, y);
    }
  }
}

function drawTimeAxis(xOf, t0, tNow, plotW, plotH) {
  ctx.fillStyle = "#0e1220";
  ctx.fillRect(0, plotH, W, AXIS_H);
  ctx.strokeStyle = "#232a3d";
  ctx.beginPath();
  ctx.moveTo(0, plotH + 0.5); ctx.lineTo(W, plotH + 0.5);
  ctx.stroke();

  const stepMs = [1000, 2000, 5000, 10_000, 15_000, 30_000, 60_000,
    120_000, 300_000, 600_000].find((s) => s * plotW / S.timeSpan >= 90)
    || 900_000;
  const first = Math.ceil(t0 / stepMs) * stepMs;
  ctx.font = "10px sans-serif";
  ctx.textAlign = "center";
  ctx.textBaseline = "top";
  for (let t = first; t <= tNow; t += stepMs) {
    const x = xOf(t);
    if (x < 20 || x > plotW - 10) continue;
    ctx.strokeStyle = "rgba(35,42,61,.5)";
    ctx.beginPath();
    ctx.moveTo(x, 0); ctx.lineTo(x, plotH);
    ctx.stroke();
    ctx.fillStyle = "#7683a0";
    ctx.fillText(new Date(t).toLocaleTimeString([], { hour12: false }), x, plotH + 5);
  }
}

requestAnimationFrame(draw);

// ---------------------------------------------------------------------------
// Interaction
// ---------------------------------------------------------------------------

function clusterAt(mx, my) {
  for (const cl of S.clusters) {
    if (cl.yIcon === undefined) continue;
    if (Math.hypot(mx - cl.x, my - cl.yIcon) <= cl.r + 5) return cl;
  }
  return null;
}

canvas.addEventListener("wheel", (e) => {
  e.preventDefault();
  const f = Math.pow(1.15, Math.sign(e.deltaY));
  if (e.shiftKey) {
    S.timeSpan = Math.min(1_800_000, Math.max(5000, S.timeSpan * f));
  } else if (S.priceSpan) {
    const minSpan = (S.tick || 0.01) * 8;
    const maxSpan = (S.tick || 0.01) * 2000;
    S.priceSpan = Math.min(maxSpan, Math.max(minSpan, S.priceSpan * f));
  }
}, { passive: false });

canvas.addEventListener("mousedown", (e) => {
  if (e.button !== 0) return;
  const rect = canvas.getBoundingClientRect();
  const cl = clusterAt(e.clientX - rect.left, e.clientY - rect.top);
  if (cl) {
    S.dragIcon = { startY: e.clientY, startOffset: +$("iconoffset").value,
                   dir: cl.side === "B" ? 1 : -1 };
    e.preventDefault();
  }
});

window.addEventListener("mousemove", (e) => {
  if (S.dragIcon) {
    const d = (e.clientY - S.dragIcon.startY) * S.dragIcon.dir;
    const v = Math.min(220, Math.max(20, Math.round(S.dragIcon.startOffset + d)));
    $("iconoffset").value = v;
    $("iconoffsetval").value = v;
  }
});

window.addEventListener("mouseup", () => { S.dragIcon = null; });

canvas.addEventListener("mousemove", (e) => {
  const rect = canvas.getBoundingClientRect();
  const mx = e.clientX - rect.left, my = e.clientY - rect.top;
  const tip = $("tooltip");
  const cl = S.dragIcon ? null : clusterAt(mx, my);
  if (!cl) { tip.hidden = true; return; }
  tip.hidden = false;
  const side = cl.side === "B" ? "BUY" : "SELL";
  let html = `<span class="t-side-${cl.side}">${side} SWEEP` +
    (cl.members.length > 1 ? ` ×${cl.members.length}` : "") + `</span><br>` +
    `Total volume: <b>${cl.size.toLocaleString()}</b><br>`;
  for (const m of cl.members.slice(-4)) {
    html += `· ${m.size.toLocaleString()} @ ${m.levels} lvl ` +
      `(${fmtPx(m.pxMin)}→${fmtPx(m.pxMax)}) vwap ${fmtPx(m.vwap)} ` +
      `${fmtTime(m.t1)}<br>`;
  }
  if (cl.members.length > 4) html += `… ${cl.members.length - 4} more<br>`;
  const thr = cl.members[cl.members.length - 1].threshold;
  if (thr) html += `<small>threshold in force: ${thr}</small>`;
  tip.innerHTML = html;
  tip.style.left = `${Math.min(mx + 14, W - 270)}px`;
  tip.style.top = `${Math.max(8, my - 24)}px`;
});

canvas.addEventListener("mouseleave", () => { $("tooltip").hidden = true; });

canvas.addEventListener("contextmenu", (e) => {
  e.preventDefault();
  $("settings").hidden = false;
});

// ---------------------------------------------------------------------------
// UI wiring
// ---------------------------------------------------------------------------

function syncModeFields() {
  const mode = $("mode").value;
  document.querySelector(".dbfields").hidden = mode === "demo";
  document.querySelector(".replayfields").hidden = mode !== "replay";
}
$("mode").addEventListener("change", syncModeFields);
syncModeFields();

// ---- selector de instrumento ---------------------------------------------

fillInstruments($("symbolpick"), $("symbol").value);

function applyInstrument() {
  const v = $("symbolpick").value;
  if (v === "custom") {
    $("symbol").hidden = false;          // escribir el símbolo a mano
    $("symbol").focus();
    document.querySelector(".advfields").hidden = false;
    return;
  }
  $("symbol").hidden = true;
  $("symbol").value = v;
  // los presets son contratos continuos de CME: fijamos lo que necesitan
  $("dataset").value = "GLBX.MDP3";
  $("stype").value = "continuous";
}
$("symbolpick").addEventListener("change", applyInstrument);

$("advtoggle").addEventListener("click", () => {
  const adv = document.querySelector(".advfields");
  adv.hidden = !adv.hidden;
  if (!adv.hidden) $("symbol").hidden = false;
});

$("connect").addEventListener("click", sendStart);
$("stopbtn").addEventListener("click", sendStop);
$("gear").addEventListener("click", () => {
  $("settings").hidden = !$("settings").hidden;
});
$("closeset").addEventListener("click", () => { $("settings").hidden = true; });
$("testalert").addEventListener("click", () => playAlert("B"));

for (const id of ["windowms", "minlevels", "minsize", "autosize",
                  "sdinterval", "sdmult"]) {
  $(id).addEventListener("change", sendParams);
}
$("autosize").addEventListener("change", () => {
  $("minsize").disabled = $("autosize").checked;
});
for (const [slider, out] of [["sdmult", "sdmultval"],
                             ["iconsize", "iconsizeval"],
                             ["iconoffset", "iconoffsetval"],
                             ["linewidth", "linewidthval"],
                             ["dotsize", "dotsizeval"]]) {
  $(slider).addEventListener("input", () => { $(out).value = $(slider).value; });
}
$("buycolor").addEventListener("input", applyCssColors);
$("sellcolor").addEventListener("input", applyCssColors);
applyCssColors();

// default replay range: a recent weekday RTH slice, as a convenience
(() => {
  const d = new Date(Date.now() - 86400_000 * 3);
  while (d.getUTCDay() === 0 || d.getUTCDay() === 6) d.setTime(d.getTime() - 86400_000);
  const day = d.toISOString().slice(0, 10);
  $("rstart").value = `${day}T14:30Z`;
  $("rend").value = `${day}T14:40Z`;
})();

// auto-start the demo so the page shows something immediately
connect();
setTimeout(() => { if (!S.wantRun) sendStart(); }, 400);
