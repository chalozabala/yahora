"""Data feeds for the sweeps indicator.

Three interchangeable async feeds, all yielding the same event dicts:

  {"type": "snapshot", "ts": ns, "bids": [[px, sz] * 10], "asks": [[px, sz] * 10]}
  {"type": "trade",    "ts": ns, "px": float, "sz": int, "side": "B"|"A"|"N"}
  {"type": "status",   ...}   informational
  {"type": "error",    ...}   terminal

* DemoFeed        — synthetic order book + trades, no credentials needed.
* LiveFeed        — Databento live gateway (requires DATABENTO_API_KEY).
* ReplayFeed      — Databento historical range replayed on a clock.

Sweep detection itself happens in the caller (server.py) so all feeds share
one code path.
"""

from __future__ import annotations

import asyncio
import math
import os
import random
import re
import time
from typing import AsyncIterator, Optional

PRICE_SCALE = 1e-9          # Databento fixed-precision prices are int * 1e-9
UNDEF_PRICE = 2 ** 63 - 1   # INT64_MAX sentinel for "no price"

BOOK_DEPTH = 10


def _px(raw: int) -> Optional[float]:
    if raw is None or raw == UNDEF_PRICE:
        return None
    return raw * PRICE_SCALE


# {root}.{roll rule}.{rank} — the root is upper case, the roll rule letter
# (c/n/v) is lower case and meaningful, so they are normalized separately
_CONTINUOUS_RE = re.compile(r"^([A-Za-z0-9]+)\.([cnvCNV])\.(\d+)$")


def normalize_symbol(sym: str) -> str:
    """Accept what a person types ('nq', 'es.V.0') as a valid symbol."""
    sym = (sym or "").strip()
    m = _CONTINUOUS_RE.match(sym)
    if m:
        return f"{m.group(1).upper()}.{m.group(2).lower()}.{m.group(3)}"
    return sym.upper()


# ---------------------------------------------------------------------------
# Demo feed
# ---------------------------------------------------------------------------

class DemoFeed:
    """Synthetic ES-like market: 0.25 tick, drifting book, periodic sweeps."""

    def __init__(self, symbol: str = "DEMO.ES", tick: float = 0.25,
                 start_price: float = 6400.0):
        self.symbol = symbol
        self.tick = tick
        # integer tick index of the best ask; best bid = best_ask - 1 tick
        self.best_ask_i = round(start_price / tick)
        self.bid_sz = [self._lvl_size(i) for i in range(BOOK_DEPTH)]
        self.ask_sz = [self._lvl_size(i) for i in range(BOOK_DEPTH)]
        self._stop = asyncio.Event()

    @staticmethod
    def _lvl_size(depth: int) -> int:
        base = random.randint(15, 120)
        return base + depth * random.randint(5, 40)

    def _price(self, i: int) -> float:
        return round(i * self.tick, 10)

    def _snapshot(self, ts: int) -> dict:
        bids = [[self._price(self.best_ask_i - 1 - d), self.bid_sz[d]]
                for d in range(BOOK_DEPTH)]
        asks = [[self._price(self.best_ask_i + d), self.ask_sz[d]]
                for d in range(BOOK_DEPTH)]
        return {"type": "snapshot", "ts": ts, "bids": bids, "asks": asks}

    def stop(self) -> None:
        self._stop.set()

    async def events(self) -> AsyncIterator[dict]:
        yield {"type": "status", "state": "running", "mode": "demo",
               "symbol": self.symbol, "detail": "synthetic data"}
        next_sweep = time.monotonic() + random.uniform(3.0, 8.0)
        while not self._stop.is_set():
            ts = time.time_ns()

            # jitter resting liquidity
            for arr in (self.bid_sz, self.ask_sz):
                for d in range(BOOK_DEPTH):
                    arr[d] = max(5, arr[d] + random.randint(-15, 15))

            # occasional 1-tick drift without prints
            if random.random() < 0.06:
                self._shift(1 if random.random() < 0.5 else -1)

            yield self._snapshot(ts)

            # background single-level trades at the touch
            if random.random() < 0.55:
                for ev in self._small_trade(ts):
                    yield ev
                yield self._snapshot(ts)

            # periodic sweep burst
            if time.monotonic() >= next_sweep:
                for ev in self._sweep_burst(ts):
                    yield ev
                yield self._snapshot(ts)
                next_sweep = time.monotonic() + random.uniform(3.0, 9.0)

            await asyncio.sleep(0.1)

    # -- internals ---------------------------------------------------------

    def _shift(self, direction: int) -> None:
        """Move the whole book by one tick, refreshing the vacated edge."""
        self.best_ask_i += direction
        if direction > 0:
            self.ask_sz.pop(0)
            self.ask_sz.append(self._lvl_size(BOOK_DEPTH))
            self.bid_sz.insert(0, self._lvl_size(0))
            self.bid_sz.pop()
        else:
            self.bid_sz.pop(0)
            self.bid_sz.append(self._lvl_size(BOOK_DEPTH))
            self.ask_sz.insert(0, self._lvl_size(0))
            self.ask_sz.pop()

    def _small_trade(self, ts: int):
        side = "B" if random.random() < 0.5 else "A"
        if side == "B":
            px = self._price(self.best_ask_i)
            sz = random.randint(1, max(2, self.ask_sz[0] // 3))
            self.ask_sz[0] = max(1, self.ask_sz[0] - sz)
        else:
            px = self._price(self.best_ask_i - 1)
            sz = random.randint(1, max(2, self.bid_sz[0] // 3))
            self.bid_sz[0] = max(1, self.bid_sz[0] - sz)
        yield {"type": "trade", "ts": ts, "px": px, "sz": sz, "side": side}

    def _sweep_burst(self, ts: int):
        side = "B" if random.random() < 0.5 else "A"
        levels = random.choices([2, 3, 4, 5, 6], weights=[38, 28, 18, 10, 6])[0]
        for k in range(levels):
            if side == "B":
                px = self._price(self.best_ask_i + k)
                lvl = self.ask_sz[k]
            else:
                px = self._price(self.best_ask_i - 1 - k)
                lvl = self.bid_sz[k]
            sz = lvl if k < levels - 1 else max(1, int(lvl * random.uniform(0.3, 0.9)))
            if k == levels - 1:      # last level only partially consumed
                if side == "B":
                    self.ask_sz[k] = max(1, self.ask_sz[k] - sz)
                else:
                    self.bid_sz[k] = max(1, self.bid_sz[k] - sz)
            # fills of one order share the same matching-engine timestamp
            yield {"type": "trade", "ts": ts, "px": px, "sz": sz, "side": side}
        # the book actually moves through the consumed levels
        for _ in range(levels - 1):
            self._shift(1 if side == "B" else -1)


# ---------------------------------------------------------------------------
# Databento feeds
# ---------------------------------------------------------------------------

NO_KEY_MSG = (
    "Falta la clave de Databento. Corré datos-reales.bat (Windows) o "
    "./datos-reales.sh (Mac/Linux) y pegá tu clave cuando te la pida. "
    "El modo Demo funciona sin clave.")

NO_PACKAGE_MSG = (
    "Falta instalar el módulo de datos reales. Corré datos-reales.bat "
    "(Windows) o ./datos-reales.sh (Mac/Linux) y volvé a intentar. "
    "El modo Demo funciona sin él.")


def _get_key(explicit: Optional[str]) -> str:
    key = explicit or os.getenv("DATABENTO_API_KEY", "")
    if not key:
        raise RuntimeError(NO_KEY_MSG)
    return key


def _import_databento():
    try:
        import databento as db  # noqa: WPS433 (heavy optional dependency)
        return db
    except ImportError as exc:
        raise RuntimeError(NO_PACKAGE_MSG) from exc


def _record_events(rec, symbol_map: dict) -> list:
    """Convert a databento DBN record into our wire events."""
    import databento_dbn as dbn

    events = []
    if isinstance(rec, dbn.TradeMsg):
        px = _px(rec.price)
        if px is not None:
            # rec.side is a databento_dbn.Side enum: normalize to a plain
            # str at the boundary or json.dumps chokes downstream
            side = str(rec.side)
            side = side if side in ("B", "A") else "N"
            events.append({"type": "trade", "ts": rec.ts_event, "px": px,
                           "sz": rec.size, "side": side})
    elif isinstance(rec, (dbn.MBP10Msg, dbn.MBP1Msg)):
        # MBP-1 y MBP-10 tienen la misma forma: cambia cuantos niveles
        # trae `levels`. Asi el grafico anda con el plan que tengas.
        bids, asks = [], []
        for lvl in rec.levels:
            bpx, apx = _px(lvl.bid_px), _px(lvl.ask_px)
            if bpx is not None:
                bids.append([bpx, lvl.bid_sz])
            if apx is not None:
                asks.append([apx, lvl.ask_sz])
        events.append({"type": "snapshot", "ts": rec.ts_event,
                       "bids": bids, "asks": asks})
    elif isinstance(rec, dbn.SymbolMappingMsg):
        # continuous contracts roll by re-mapping to a new instrument_id:
        # this is how the UI learns which real contract is in force
        contract = rec.stype_out_symbol
        symbol_map[rec.instrument_id] = contract
        events.append({"type": "status", "state": "mapped",
                       "contract": contract,
                       "requested": rec.stype_in_symbol,
                       "detail": f"contrato vigente: {contract}"})
    elif isinstance(rec, dbn.SystemMsg):
        if not getattr(rec, "is_heartbeat", False):
            events.append({"type": "status", "state": "info",
                           "detail": str(rec.msg)})
    elif isinstance(rec, dbn.ErrorMsg):
        events.append({"type": "error", "message": str(rec.err)})
    return events


# Esquemas de profundidad, del mas rico al mas barato. Detectar sweeps NO
# necesita ninguno (alcanza con `trades`); la profundidad es solo para
# pintar el heatmap. MBP-10 suele requerir un plan superior, asi que hay
# que poder caer a MBP-1 o quedarse sin libro en vez de fallar entero.
DEPTH_SCHEMAS = ("mbp-10", "mbp-1")


def es_error_de_esquema(mensaje: str) -> bool:
    """¿El gateway rechazó por el esquema de libro (y no por la clave)?
    Mensaje típico: "Not authorized for mbp-10 schema"."""
    m = (mensaje or "").lower()
    if not any(k in m for k in ("not authorized", "unauthorized",
                                "not entitled", "no entitlement",
                                "not permissioned", "forbidden")):
        return False
    return any(k in m for k in DEPTH_SCHEMAS) or "schema" in m


def normalize_depth(depth: Optional[str]) -> str:
    depth = (depth or "auto").strip().lower()
    if depth in DEPTH_SCHEMAS or depth in ("auto", "none"):
        return depth
    return "auto"


class LiveFeed:
    """Databento live gateway → trades (+ libro si el plan lo permite).

    El gateway NO rechaza una suscripción no autorizada en el momento de
    pedirla: la acepta y manda el rechazo después, como un ErrorMsg dentro
    del stream ("Not authorized for mbp-10 schema"). Por eso el fallback
    no puede ser un try/except alrededor de subscribe(): hay que escuchar
    el arranque del stream y, si el rechazo es por el esquema de libro,
    reconectar pidiendo uno más chico. Los trades nunca se resignan.
    """

    PRUEBA_S = 6.0        # cuánto se espera el veredicto del gateway

    def __init__(self, dataset: str, symbol: str, stype_in: str = "continuous",
                 api_key: Optional[str] = None, depth: str = "auto"):
        self.dataset = dataset
        self.symbol = symbol
        self.stype_in = stype_in
        self.api_key = _get_key(api_key)
        self.depth = normalize_depth(depth)
        self.depth_used: Optional[str] = None
        self._client = None
        self._it = None          # referencia fuerte: ver events()

    def stop(self) -> None:
        client = self._client
        if client is not None:
            try:
                client.stop()
            except Exception:
                pass

    def _connect(self, schema: Optional[str]):
        """Crea el cliente y suscribe. Va en un hilo: la primera suscripción
        bloquea en el TCP + la autenticación CRAM (decenas de segundos)."""
        db = _import_databento()
        client = db.Live(key=self.api_key, reconnect_policy="reconnect")
        client.subscribe(dataset=self.dataset, schema="trades",
                         stype_in=self.stype_in, symbols=[self.symbol])
        if schema:
            client.subscribe(dataset=self.dataset, schema=schema,
                             stype_in=self.stype_in, symbols=[self.symbol])
        return client

    def _candidatos(self):
        """Esquemas de libro a intentar, del mejor al peor. None = sin libro
        (los sweeps se detectan igual: sólo necesitan los trades)."""
        if self.depth == "none":
            return [None]
        if self.depth in DEPTH_SCHEMAS:
            return [self.depth, None]
        return [*DEPTH_SCHEMAS, None]

    async def events(self) -> AsyncIterator[dict]:
        yield {"type": "status", "state": "loading", "mode": "live",
               "symbol": self.symbol,
               "detail": f"conectando con databento {self.dataset}…"}

        symbol_map: dict = {}
        for schema in self._candidatos():
            client = await asyncio.to_thread(self._connect, schema)
            self._client = client
            # CUIDADO: LiveIterator arranca el cliente al crearse y lo
            # TERMINA en su __del__. Si se lo deja como variable local, el
            # recolector de basura mata la conexion en cuanto sale de
            # alcance. Por eso se crea uno solo y se guarda en self.
            self._it = client.__aiter__()
            pendientes, rechazado = await self._probar(self._it, symbol_map)
            if rechazado:
                # el plan no incluye este libro: probamos el siguiente
                self._it = None
                self._terminar(client)
                continue

            self.depth_used = schema
            libro = schema or "sin libro"
            yield {"type": "status", "state": "running", "mode": "live",
                   "symbol": self.symbol, "depth": schema,
                   "detail": f"databento live {self.dataset} · {libro}"}
            if schema is None and self.depth != "none":
                yield {"type": "status", "state": "aviso",
                       "detail": "Tu plan no incluye datos de libro: se ven "
                                 "los trades y los sweeps, pero sin mapa de "
                                 "calor."}
            elif schema == "mbp-1":
                yield {"type": "status", "state": "aviso",
                       "detail": "Tu plan no incluye MBP-10, así que se usa "
                                 "MBP-1: el mapa de calor muestra la mejor "
                                 "oferta y demanda. Los sweeps se detectan "
                                 "igual."}
            for ev in pendientes:
                yield ev
            try:
                # se sigue con EL MISMO iterador: crear otro llamaria a
                # start() sobre un cliente ya en marcha y explotaria
                while True:
                    try:
                        rec = await self._it.__anext__()
                    except StopAsyncIteration:
                        break
                    for ev in _record_events(rec, symbol_map):
                        yield ev
            finally:
                self.stop()
            # el gateway cerró la sesión: avisar en vez de dejar la pantalla
            # congelada con el estado "running"
            yield {"type": "error",
                   "message": "Databento cerró la conexión en vivo."}
            return

        yield {"type": "error",
               "message": "Databento rechazó todas las suscripciones de "
                          "libro y también los trades. Revisá que tu plan "
                          "incluya datos en vivo de este mercado."}

    @staticmethod
    def _terminar(client) -> None:
        for metodo in ("terminate", "stop"):
            try:
                getattr(client, metodo)()
                return
            except Exception:
                continue

    async def _probar(self, it, symbol_map: dict):
        """Escucha el arranque del stream. Devuelve (eventos, rechazado):
        'rechazado' es True sólo si el gateway rechazó el esquema de libro,
        que es el caso recuperable pidiendo uno más chico."""
        pendientes: list = []
        loop = asyncio.get_running_loop()
        limite = loop.time() + self.PRUEBA_S
        while True:
            restante = limite - loop.time()
            if restante <= 0:
                return pendientes, False        # sin veredicto: seguimos
            try:
                rec = await asyncio.wait_for(it.__anext__(), timeout=restante)
            except (asyncio.TimeoutError, StopAsyncIteration):
                return pendientes, False
            for ev in _record_events(rec, symbol_map):
                if ev["type"] == "error" and es_error_de_esquema(ev["message"]):
                    return [], True
                pendientes.append(ev)
                if ev["type"] in ("trade", "snapshot"):
                    return pendientes, False    # ya llegan datos: listo


class ReplayFeed:
    """Databento historical range, replayed against a wall clock."""

    def __init__(self, dataset: str, symbol: str, start: str, end: str,
                 stype_in: str = "continuous", speed: float = 1.0,
                 api_key: Optional[str] = None, limit: int = 2_000_000,
                 depth: str = "auto"):
        self.dataset = dataset
        self.symbol = symbol
        self.start = start
        self.end = end
        self.stype_in = stype_in
        self.speed = max(0.1, min(1000.0, speed))
        self.api_key = _get_key(api_key)
        self.limit = limit
        self.depth = normalize_depth(depth)
        self.depth_used: Optional[str] = None
        self._stop = asyncio.Event()

    def stop(self) -> None:
        self._stop.set()

    def _fetch(self):
        db = _import_databento()
        client = db.Historical(key=self.api_key)
        def traer(schema):
            store = client.timeseries.get_range(
                dataset=self.dataset, schema=schema,
                symbols=[self.symbol], stype_in=self.stype_in,
                start=self.start, end=self.end, limit=self.limit)
            return [r for r in store if hasattr(r, "ts_event")]

        # los trades son imprescindibles; el libro es un extra que depende
        # del plan contratado, asi que si no esta seguimos sin el
        per_schema = {"trades": traer("trades")}
        candidatos = (DEPTH_SCHEMAS if self.depth == "auto"
                      else () if self.depth == "none" else (self.depth,))
        for schema in candidatos:
            try:
                per_schema[schema] = traer(schema)
                self.depth_used = schema
                break
            except Exception:
                continue
        # a schema that hit the record limit stops mid-range; trim the
        # others to the same instant so the replay stays synchronized
        truncated = [s for s, recs in per_schema.items()
                     if len(recs) >= self.limit]
        cutoff = None
        if truncated:
            cutoff = min(per_schema[s][-1].ts_event for s in truncated)
            per_schema = {s: [r for r in recs if r.ts_event <= cutoff]
                          for s, recs in per_schema.items()}
        recs = [r for recs in per_schema.values() for r in recs]
        recs.sort(key=lambda r: r.ts_event)
        return recs, truncated, cutoff

    async def events(self) -> AsyncIterator[dict]:
        yield {"type": "status", "state": "loading", "mode": "replay",
               "symbol": self.symbol,
               "detail": f"downloading {self.start} → {self.end}"}
        recs, truncated, cutoff = await asyncio.to_thread(self._fetch)
        if not recs:
            yield {"type": "error", "message": "No records in range "
                   f"{self.start} → {self.end} for {self.symbol}"}
            return
        if truncated:
            yield {"type": "status", "state": "info",
                   "detail": f"record limit hit for {', '.join(truncated)}; "
                             f"replay trimmed to {cutoff} ns — "
                             "request a shorter range for full coverage"}
        libro = self.depth_used or "sin libro"
        yield {"type": "status", "state": "running", "mode": "replay",
               "symbol": self.symbol, "depth": self.depth_used,
               "detail": f"reproduciendo {len(recs)} registros a "
                         f"{self.speed}x · {libro}"}
        if self.depth_used is None and self.depth != "none":
            yield {"type": "status", "state": "aviso",
                   "detail": "Tu plan no incluye datos de libro: se ven los "
                             "trades y los sweeps, pero sin mapa de calor."}
        symbol_map: dict = {}
        t0_data = recs[0].ts_event
        t0_wall = time.monotonic()
        for rec in recs:
            if self._stop.is_set():
                break
            due = t0_wall + (rec.ts_event - t0_data) / 1e9 / self.speed
            delay = due - time.monotonic()
            if delay > 0:
                # cap so a quiet stretch never freezes the stream for minutes
                await asyncio.sleep(min(delay, 2.0))
            for ev in _record_events(rec, symbol_map):
                yield ev
        yield {"type": "status", "state": "finished", "mode": "replay",
               "symbol": self.symbol, "detail": "replay complete"}


def make_feed(cfg: dict):
    """Build a feed from the client's start message."""
    mode = cfg.get("mode", "demo")
    symbol = normalize_symbol(cfg.get("symbol") or "ES.v.0")
    dataset = (cfg.get("dataset") or "GLBX.MDP3").strip().upper()
    stype_in = (cfg.get("stype_in") or "continuous").strip()
    if mode == "demo":
        return DemoFeed(symbol="DEMO.ES")
    depth = cfg.get("depth", "auto")
    if mode == "live":
        return LiveFeed(dataset=dataset, symbol=symbol, stype_in=stype_in,
                        api_key=cfg.get("api_key") or None, depth=depth)
    if mode == "replay":
        start = cfg.get("start")
        end = cfg.get("end")
        if not start or not end:
            raise RuntimeError("Replay mode needs 'start' and 'end' (ISO 8601).")
        return ReplayFeed(dataset=dataset, symbol=symbol, start=start, end=end,
                          stype_in=stype_in, speed=float(cfg.get("speed", 1.0)),
                          api_key=cfg.get("api_key") or None, depth=depth)
    raise RuntimeError(f"Unknown mode '{mode}'")
