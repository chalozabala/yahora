"""Sweeps web indicator — FastAPI server.

Serves the static frontend and a websocket that streams order-book
snapshots, trades and detected sweep events to the browser. Data comes
from one of three feeds (demo / Databento live / Databento historical
replay); sweep detection runs server-side so every mode shares the same
code path.

Wire protocol (JSON over /ws):

  client → server
    {"type": "start", "mode": "demo"|"live"|"replay", "symbol": "...",
     "dataset": "...", "stype_in": "...", "start": iso, "end": iso,
     "speed": 1.0, "params": {"min_size": 40, "min_levels": 2,
     "window_ms": 2}}
    {"type": "params", "params": {...}}          # live re-tune
    {"type": "stop"}

  server → client: JSON array of event objects, flushed every 50 ms
    {"type": "snapshot", ...} {"type": "trade", ...} {"type": "sweep", ...}
    {"type": "status", ...}   {"type": "error", ...}
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path
from typing import List, Optional

import httpx
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles

from feeds import make_feed
from sweeps import SweepDetector, Trade

app = FastAPI(title="Sweeps Web Indicator")

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"

FLUSH_INTERVAL = 0.05          # seconds between websocket batches
MAX_TRADES_PER_BATCH = 400     # merge beyond this to protect the browser


def _detector_kwargs(params: dict, partial: bool = False) -> dict:
    """Map wire params → SweepDetector kwargs (None = leave unchanged)."""
    def has(k):
        return k in params

    kw = {}
    if not partial or has("window_ms"):
        kw["window_ns"] = int(float(params.get("window_ms", 200)) * 1e6)
    if not partial or has("min_levels"):
        kw["min_levels"] = int(params.get("min_levels", 3))
    if not partial or has("min_size"):
        kw["min_size"] = int(params.get("min_size", 40))
    if not partial or has("auto_size"):
        kw["auto_size"] = bool(params.get("auto_size", False))
    if not partial or has("sd_interval_min"):
        kw["sd_interval_ns"] = int(
            float(params.get("sd_interval_min", 5)) * 60 * 1e9)
    if not partial or has("sd_multiplier"):
        kw["sd_multiplier"] = float(params.get("sd_multiplier", 6.0))
    return kw


class Session:
    """One websocket client: a feed task + a batching sender task."""

    def __init__(self, ws: WebSocket):
        self.ws = ws
        self.detector = SweepDetector()
        self.feed = None
        self.feed_task: Optional[asyncio.Task] = None
        self.buffer: List[dict] = []
        self.lock = asyncio.Lock()
        self.sender_task = asyncio.create_task(self._sender())

    # -- control ----------------------------------------------------------

    async def handle(self, msg: dict) -> None:
        mtype = msg.get("type")
        if mtype == "start":
            await self.stop_feed()
            params = msg.get("params") or {}
            self.detector = SweepDetector(**_detector_kwargs(params))
            try:
                self.feed = make_feed(msg)
            except RuntimeError as exc:
                await self.push({"type": "error", "message": str(exc)})
                return
            self.feed_task = asyncio.create_task(self._run_feed(self.feed))
        elif mtype == "params":
            p = msg.get("params") or {}
            self.detector.update_params(**_detector_kwargs(p, partial=True))
            await self.push({"type": "status", "state": "params",
                             "detail": "detector parameters updated"})
        elif mtype == "stop":
            await self.stop_feed()
            await self.push({"type": "status", "state": "stopped",
                             "detail": "feed stopped"})

    async def stop_feed(self) -> None:
        if self.feed is not None:
            try:
                self.feed.stop()
            except Exception:
                pass
            self.feed = None
        if self.feed_task is not None:
            self.feed_task.cancel()
            try:
                await self.feed_task
            except (asyncio.CancelledError, Exception):
                pass
            self.feed_task = None

    async def shutdown(self) -> None:
        await self.stop_feed()
        self.sender_task.cancel()
        try:
            await self.sender_task
        except asyncio.CancelledError:
            pass

    # -- data path --------------------------------------------------------

    async def _run_feed(self, feed) -> None:
        try:
            async for ev in feed.events():
                etype = ev.get("type")
                if etype == "trade":
                    trade = Trade(ts=ev["ts"], price=ev["px"],
                                  size=ev["sz"], side=ev["side"])
                    for sweep in self.detector.on_trade(trade):
                        await self.push(sweep.to_dict())
                    await self.push(ev)
                else:
                    # any later event closes a sweep that has gone quiet
                    ts = ev.get("ts")
                    if ts:
                        for sweep in self.detector.flush(ts):
                            await self.push(sweep.to_dict())
                    await self.push(ev)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # surface feed crashes to the UI
            await self.push({"type": "error",
                             "message": f"feed error: {exc!r}"})

    async def push(self, ev: dict) -> None:
        async with self.lock:
            self.buffer.append(ev)

    async def _sender(self) -> None:
        while True:
            await asyncio.sleep(FLUSH_INTERVAL)
            async with self.lock:
                batch, self.buffer = self.buffer, []
            if not batch:
                continue
            batch = self._compact(batch)
            try:
                await self.ws.send_text(json.dumps(batch))
            except Exception:
                return  # socket gone; receive loop will clean up

    @staticmethod
    def _compact(batch: List[dict]) -> List[dict]:
        """Keep only the newest snapshot; cap trade count per batch."""
        snapshots = [e for e in batch if e["type"] == "snapshot"]
        others = [e for e in batch if e["type"] != "snapshot"]
        trades = [e for e in others if e["type"] == "trade"]
        if len(trades) > MAX_TRADES_PER_BATCH:
            merged: dict = {}
            rest = [e for e in others if e["type"] != "trade"]
            for t in trades:
                key = (t["px"], t["side"])
                if key in merged:
                    merged[key]["sz"] += t["sz"]
                    merged[key]["ts"] = t["ts"]
                else:
                    merged[key] = dict(t)
            others = rest + list(merged.values())
        out = others
        if snapshots:
            out = out + [snapshots[-1]]
        return out


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket) -> None:
    await ws.accept()
    session = Session(ws)
    await session.push({"type": "status", "state": "connected",
                        "detail": "send a start message to begin"})
    try:
        while True:
            raw = await ws.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                await session.push({"type": "error",
                                    "message": "invalid JSON"})
                continue
            await session.handle(msg)
    except WebSocketDisconnect:
        pass
    finally:
        await session.shutdown()


@app.get("/health")
async def health() -> dict:
    return {"ok": True, "ts": time.time(),
            "databento_key_configured": bool(os.getenv("DATABENTO_API_KEY"))}


# ---------------------------------------------------------------------------
# Legacy GEX demo endpoint (kept from the original dashboard)
# ---------------------------------------------------------------------------

GEX_API_KEY = os.getenv("GEXBOT_API_KEY", "demo")
GEX_URL = "https://api.gexbot.com/v1/levels?symbol=NQ"
_gex_cache = {"ts": 0.0, "data": {}}


@app.get("/gexlive")
async def gexlive():
    if time.time() - _gex_cache["ts"] < 5:
        return _gex_cache["data"]
    headers = {"Authorization": f"Bearer {GEX_API_KEY}"}
    async with httpx.AsyncClient() as c:
        r = await c.get(GEX_URL, headers=headers, timeout=5)
        data = r.json()
    _gex_cache["ts"] = time.time()
    _gex_cache["data"] = data
    return data


app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True),
          name="frontend")
