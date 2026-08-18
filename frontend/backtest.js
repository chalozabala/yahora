/* Backtest tab: one-button flow for non-technical users.
 * Uses globals from app.js: $, detectorParams, buyColor, sellColor.
 */

"use strict";

// ---------------------------------------------------------------------------
// View switching
// ---------------------------------------------------------------------------

function showView(which) {
  const chart = which === "chart";
  $("chartwrap").style.display = chart ? "" : "none";
  $("sidebar").style.display = chart ? "" : "none";
  $("btview").hidden = chart;
  $("tab-chart").classList.toggle("active", chart);
  $("tab-backtest").classList.toggle("active", !chart);
  // the settings dialog lives inside the (hidden) chart view
  $("gear").style.display = chart ? "" : "none";
  if (!chart) $("settings").hidden = true;
}
$("tab-chart").addEventListener("click", () => showView("chart"));
$("tab-backtest").addEventListener("click", () => showView("backtest"));

$("btsymbol").addEventListener("change", () => {
  $("btsymbolcustom").hidden = $("btsymbol").value !== "custom";
});

// ---------------------------------------------------------------------------
// Run flow: estimate -> confirm -> job -> poll -> report
// ---------------------------------------------------------------------------

let btJobId = null;
let btPollTimer = null;

function btConfig() {
  const sym = $("btsymbol").value === "custom"
    ? ($("btsymbolcustom").value.trim() || "ES.v.0")
    : $("btsymbol").value;
  return {
    mode: $("btmode").value === "demo" ? "demo" : "databento",
    dataset: $("btdataset").value.trim(),
    symbol: sym,
    stype_in: $("btstype").value,
    days: +$("btdays").value || 10,
    params: detectorParams(),
  };
}

function btShowError(msg) {
  const el = $("bterror");
  el.hidden = !msg;
  el.textContent = msg || "";
}

async function btPost(url, body) {
  const r = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!r.ok) {
    let detail = "";
    try { detail = (await r.json()).detail || ""; } catch { /* no body */ }
    throw new Error(detail || `HTTP ${r.status}`);
  }
  return r.json();
}

$("btrun").addEventListener("click", async () => {
  btShowError("");
  $("btconfirm").hidden = true;
  const cfg = btConfig();
  $("btrun").disabled = true;
  let launched = false;      // once launched, btPoll/btLaunch own the button
  try {
    if (cfg.mode === "demo") {
      launched = true;
      await btLaunch(cfg);
      return;
    }
    const est = await btPost("/backtest/estimate", cfg);
    if (est.error) { btShowError(est.error); return; }
    const box = $("btconfirm");
    const cost = est.unknown ? "no se pudo estimar"
      : `≈ $${(est.usd || 0).toFixed(2)} USD`;
    box.innerHTML =
      `Costo de datos estimado: <b>${cost}</b> · ` +
      `${est.cached_days} de ${est.days} días ya están en caché ` +
      `(los días en caché no se vuelven a pagar). ¿Continuar?` +
      `<button id="btgo" class="primary">Sí, correr</button>` +
      `<button id="btno">Cancelar</button>`;
    box.hidden = false;
    $("btgo").onclick = () => { box.hidden = true; btLaunch(cfg); };
    $("btno").onclick = () => { box.hidden = true; $("btrun").disabled = false; };
  } catch (e) {
    btShowError(`No se pudo iniciar: ${e.message}`);
  } finally {
    if (!launched && $("btconfirm").hidden) $("btrun").disabled = false;
  }
});

async function btLaunch(cfg) {
  if (btPollTimer) { clearInterval(btPollTimer); btPollTimer = null; }
  $("btrun").disabled = true;
  try {
    $("btreport").hidden = true;
    $("btprogress").hidden = false;
    $("btprogtext").textContent = "Iniciando…";
    $("btbarfill").style.width = "0%";
    $("btprogdetail").textContent = "";
    $("btcancel").disabled = false;
    const { job_id } = await btPost("/backtest", cfg);
    btJobId = job_id;
    btPollTimer = setInterval(btPoll, 700);
  } catch (e) {
    $("btprogress").hidden = true;
    btShowError(`No se pudo iniciar: ${e.message}`);
    $("btrun").disabled = false;
  }
}

$("btcancel").addEventListener("click", async () => {
  if (!btJobId) return;
  $("btcancel").disabled = true;
  $("btprogtext").textContent = "Cancelando…";
  await fetch(`/backtest/${btJobId}`, { method: "DELETE" });
});

async function btPoll() {
  if (!btJobId) return;
  let st;
  try {
    st = await (await fetch(`/backtest/${btJobId}`)).json();
  } catch { return; }
  const p = st.progress || {};
  if (st.state === "running") {
    $("btprogtext").textContent =
      p.days ? `Procesando día ${(p.day_i || 0) + 1} de ${p.days}` :
        "Procesando…";
    $("btbarfill").style.width = `${p.pct || 0}%`;
    $("btprogdetail").textContent = p.detail || "";
    return;
  }
  clearInterval(btPollTimer);
  btPollTimer = null;
  $("btprogress").hidden = true;
  $("btrun").disabled = false;
  const finishedJob = btJobId;
  btJobId = null;
  if (st.state === "done" && st.report) {
    renderReport(st.report, finishedJob);
  } else if (st.state === "cancelled") {
    btShowError("Backtest cancelado.");
  } else {
    btShowError(`El backtest falló: ${st.error || st.state}`);
  }
}

// ---------------------------------------------------------------------------
// Report rendering
// ---------------------------------------------------------------------------

function esc(s) {
  return String(s).replace(/[&<>"]/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
}

function pct(x) { return x === null || x === undefined ? "–"
  : `${(x * 100).toFixed(1)}%`; }
function num(x) { return x === null || x === undefined ? "–"
  : (+x).toLocaleString(); }
function fx(x, d = 2) { return x === null || x === undefined ? "–"
  : (+x).toFixed(d); }

function sideMark(side) {
  return side === "B"
    ? '<span class="mark mark-B">▲</span>'
    : '<span class="mark mark-A">▼</span>';
}

function plainSummary(rep) {
  const t = rep.totals;
  if (!t.sweeps) {
    return "No se detectó ningún sweep con el filtro actual. Probá bajar " +
      "el tamaño mínimo o los niveles en ⚙ Settings y volvé a correrlo.";
  }
  const h1m = rep.horizons.find((h) => h.label === "1m") || rep.horizons[0];
  const a = h1m.all;
  let s = `En ${t.days_with_data} días con datos se detectaron ` +
    `<b>${num(t.sweeps)} sweeps</b> ` +
    `(<span class="mark-B">▲ ${num(t.buy)} de compra</span>, ` +
    `<span class="mark-A">▼ ${num(t.sell)} de venta</span>), ` +
    `con un tamaño promedio de ${num(t.avg_sweep_size)} contratos. `;
  if (a && a.n) {
    const dir = a.median > 0 ? "a favor" : a.median < 0 ? "en contra" : "neutro";
    s += `Un minuto después de cada sweep, el precio se movió una mediana ` +
      `de <b>${fx(Math.abs(a.median))} ticks ${dir}</b> de la dirección ` +
      `del sweep, y quedó a favor el <b>${pct(a.hit_rate)}</b> de las veces ` +
      `(${num(a.n)} casos medidos).`;
  }
  return s;
}

function renderReport(rep, jobId) {
  const t = rep.totals;
  const el = $("btreport");
  const cfgLine = rep.config.mode === "demo"
    ? "datos sintéticos de demostración"
    : `${esc(rep.config.symbol)} · ${esc(rep.config.dataset)}`;

  let html = `
  <div class="bt-card">
    <h2>Resultado</h2>
    <p class="bt-note">${cfgLine} · ${t.days} días pedidos ·
      tick ${esc(rep.tick)} · generado ${esc((rep.generated_at || "").slice(0, 16))}Z</p>
    <p class="bt-summary">${plainSummary(rep)}</p>
    <div class="bt-tiles">
      <div class="bt-tile"><div class="v">${num(t.sweeps)}</div>
        <div class="k">sweeps detectados</div></div>
      <div class="bt-tile"><div class="v">${sideMark("B")}${num(t.buy)}</div>
        <div class="k">de compra</div></div>
      <div class="bt-tile"><div class="v">${sideMark("A")}${num(t.sell)}</div>
        <div class="k">de venta</div></div>
      <div class="bt-tile"><div class="v">${num(t.trades)}</div>
        <div class="k">trades procesados</div></div>
    </div>
  </div>`;

  html += `
  <div class="bt-card">
    <h2>Sweeps por día</h2>
    <div class="bt-legend">
      <span><span class="mark-B">▲</span> Compras</span>
      <span><span class="mark-A">▼</span> Ventas</span>
    </div>
    <canvas id="btdaily"></canvas>
  </div>`;

  html += `
  <div class="bt-card">
    <h2>Qué hizo el precio después de cada sweep</h2>
    <p class="bt-note">Movimiento en ticks en la dirección del sweep
      (positivo = el precio siguió al sweep). "% a favor" = casos con
      movimiento positivo.</p>
    <div class="bt-scroll"><table class="bt-table">
      <tr><th>Horizonte</th><th>casos</th>
        <th>mediana</th><th>promedio</th><th>% a favor</th>
        <th>${sideMark("B")} mediana</th><th>${sideMark("B")} % a favor</th>
        <th>${sideMark("A")} mediana</th><th>${sideMark("A")} % a favor</th></tr>`;
  const withN = (rate, n) =>
    rate === null || rate === undefined ? "–"
      : `${pct(rate)} <small>(${num(n)})</small>`;
  for (const h of rep.horizons) {
    html += `<tr><td>${esc(h.label)} después</td>
      <td>${num(h.all.n)}</td><td>${fx(h.all.median)}</td>
      <td>${fx(h.all.mean)}</td><td>${pct(h.all.hit_rate)}</td>
      <td>${fx(h.buy.median)}</td><td>${withN(h.buy.hit_rate, h.buy.n)}</td>
      <td>${fx(h.sell.median)}</td><td>${withN(h.sell.hit_rate, h.sell.n)}</td></tr>`;
  }
  html += `</table></div></div>`;

  if (rep.top_sweeps && rep.top_sweeps.length) {
    html += `
    <div class="bt-card">
      <h2>Los ${rep.top_sweeps.length} sweeps más grandes</h2>
      <div class="bt-scroll"><table class="bt-table">
        <tr><th>Fecha</th><th>Lado</th><th>Contratos</th><th>Niveles</th>
          <th>Precio (vwap)</th><th>+1m (ticks)</th></tr>`;
    const i1m = rep.horizon_labels.indexOf("1m");
    for (const s of rep.top_sweeps) {
      const fwd = (s.fwd_ticks || [])[i1m];
      html += `<tr><td>${esc(s.date)}</td>
        <td>${sideMark(s.side)}${s.side === "B" ? "Compra" : "Venta"}</td>
        <td>${num(s.size)}</td><td>${num(s.levels)}</td>
        <td>${fx(s.vwap, 2)}</td><td>${fwd === null || fwd === undefined ? "–" : fx(fwd)}</td></tr>`;
    }
    html += `</table></div></div>`;
  }

  html += `
  <div class="bt-card">
    <div class="bt-row">
      <a href="/backtest/${esc(jobId)}/csv" download>
        <button>⬇ Descargar todos los sweeps (CSV)</button></a>
      <span class="bt-note">Una fila por sweep, con el movimiento posterior
        a cada horizonte. Se abre en Excel o Google Sheets.</span>
    </div>
    ${(rep.notes || []).map((n) => `<p class="bt-note">⚠ ${esc(n)}</p>`).join("")}
  </div>`;

  el.innerHTML = html;
  el.hidden = false;
  btLastReport = rep;
  drawDailyChart(rep);
  // innerHTML replaced the canvas: re-observe the new one so window
  // resizes redraw instead of CSS-stretching a stale bitmap
  if (btResizeObs) btResizeObs.disconnect();
  btResizeObs = new ResizeObserver(() => {
    if (btLastReport) drawDailyChart(btLastReport);
  });
  btResizeObs.observe($("btdaily"));
  el.scrollIntoView({ behavior: "smooth", block: "start" });
}

let btLastReport = null;
let btResizeObs = null;

// ---------------------------------------------------------------------------
// Daily grouped bar chart (canvas, hover tooltip)
// ---------------------------------------------------------------------------

function drawDailyChart(rep) {
  const cv = $("btdaily");
  if (!cv) return;
  const days = rep.daily.filter((d) => d.trades > 0);
  const dpr = window.devicePixelRatio || 1;
  const w = cv.clientWidth || 800, h = 200;
  cv.width = w * dpr; cv.height = h * dpr;
  const c = cv.getContext("2d");
  c.setTransform(dpr, 0, 0, dpr, 0, 0);
  c.clearRect(0, 0, w, h);
  if (!days.length) return;

  const padL = 34, padB = 20, padT = 8;
  const plotW = w - padL - 8, plotH = h - padT - padB;
  const maxV = Math.max(1, ...days.map(
    (d) => Math.max(d.sweeps_buy, d.sweeps_sell)));
  const group = plotW / days.length;
  const barW = Math.max(3, Math.min(26, group / 2 - 3));

  // recessive gridlines + y labels (round integers)
  const step = [1, 2, 5, 10, 20, 25, 50, 100, 200, 500, 1000]
    .find((s) => s >= maxV / 4) || 2000;
  c.font = "10px sans-serif";
  c.textAlign = "right";
  c.textBaseline = "middle";
  for (let v = 0; v <= maxV; v += step) {
    const y = padT + plotH - (v / maxV) * plotH;
    c.strokeStyle = "rgba(35,42,61,.6)";
    c.beginPath(); c.moveTo(padL, y); c.lineTo(w - 8, y); c.stroke();
    c.fillStyle = "#7683a0";
    c.fillText(String(v), padL - 6, y);
  }

  const bars = [];
  days.forEach((d, i) => {
    const x0 = padL + i * group + group / 2;
    for (const [key, off, color, name] of [
      ["sweeps_buy", -barW - 1, buyColor(), "Compras"],
      ["sweeps_sell", 1, sellColor(), "Ventas"]]) {
      const v = d[key];
      const bh = (v / maxV) * plotH;
      const x = x0 + off, y = padT + plotH - bh;
      c.fillStyle = color;
      if (bh > 0) {
        c.beginPath();
        c.roundRect(x, y, barW, bh, [3, 3, 0, 0]);
        c.fill();
      }
      bars.push({ x, y, w: barW, h: bh, date: d.date, name, v });
    }
    if (i % Math.ceil(days.length / 10) === 0) {
      c.fillStyle = "#7683a0";
      c.textAlign = "center";
      c.textBaseline = "top";
      c.fillText(d.date.slice(5), x0, padT + plotH + 5);
      c.textBaseline = "middle";
      c.textAlign = "right";
    }
  });

  let tip = document.querySelector(".bt-charttip");
  if (!tip) {
    tip = document.createElement("div");
    tip.className = "bt-charttip";
    tip.hidden = true;
    document.body.appendChild(tip);
  }
  cv.onmousemove = (e) => {
    const r = cv.getBoundingClientRect();
    const mx = e.clientX - r.left, my = e.clientY - r.top;
    const b = bars.find((b) => mx >= b.x && mx <= b.x + b.w &&
                               my >= b.y - 4 && my <= b.y + b.h);
    if (!b) { tip.hidden = true; return; }
    tip.hidden = false;
    tip.textContent = `${b.date} · ${b.name}: ${b.v}`;
    tip.style.left = `${e.clientX + 12}px`;
    tip.style.top = `${e.clientY - 24}px`;
  };
  cv.onmouseleave = () => { tip.hidden = true; };
}
