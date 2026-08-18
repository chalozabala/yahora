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
import threading
import time
import uuid
from pathlib import Path
from typing import List, Optional

import httpx
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

import backtest as bt
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
        self.run = None                # client-chosen feed generation id
        self.sender_task = asyncio.create_task(self._sender())

    # -- control ----------------------------------------------------------

    async def handle(self, msg: dict) -> None:
        try:
            await self._handle(msg)
        except Exception as exc:   # malformed input must not kill the socket
            await self.push({"type": "error",
                             "message": f"bad request: {exc!r}"})

    async def _handle(self, msg: dict) -> None:
        mtype = msg.get("type")
        if mtype == "start":
            await self.stop_feed()
            # a new run: stale batches from the old feed must not reach
            # the client with the new generation id
            async with self.lock:
                self.buffer.clear()
            self.run = msg.get("run")
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
            await self._finalize_chains()   # bounded feed ended (replay)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # surface feed crashes to the UI
            await self._finalize_chains()
            await self.push({"type": "error",
                             "message": f"feed error: {exc!r}"})

    async def _finalize_chains(self) -> None:
        """Emit sweeps whose chains were still open when the feed ended."""
        for sweep in self.detector.finalize():
            await self.push(sweep.to_dict())

    async def push(self, ev: dict) -> None:
        if self.run is not None:
            ev = {**ev, "run": self.run}
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
                text = json.dumps(batch)
            except (TypeError, ValueError):
                # never let one unserializable event kill the stream
                safe = []
                for ev in batch:
                    try:
                        json.dumps(ev)
                        safe.append(ev)
                    except (TypeError, ValueError):
                        pass
                if not safe:
                    continue
                text = json.dumps(safe)
            try:
                await self.ws.send_text(text)
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


# ---------------------------------------------------------------------------
# Backtest jobs
# ---------------------------------------------------------------------------

_jobs: dict = {}          # job_id -> job state dict
JOB_TTL_S = 900           # evict finished jobs from memory (disk remains)
MAX_RUNNING_JOBS = 2


def _report_path(job_id: str) -> Path:
    return bt.RESULTS_DIR / f"{job_id}.json"


def _csv_path(job_id: str) -> Path:
    return bt.RESULTS_DIR / f"{job_id}.csv"


def _public_report(report: dict) -> dict:
    """The UI payload: everything except the bulky per-sweep rows."""
    return {k: v for k, v in report.items() if k != "sweeps"}


def _persist_report(job_id: str, report: dict) -> None:
    bt.RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    _report_path(job_id).write_text(json.dumps(report))
    _csv_path(job_id).write_text(bt.report_to_csv(report))


def _load_public(job_id: str) -> Optional[dict]:
    path = _report_path(job_id)
    if not path.exists():
        return None
    return _public_report(json.loads(path.read_text()))


async def _run_backtest_job(job_id: str, cfg: dict) -> None:
    job = _jobs[job_id]

    def on_progress(p: dict) -> None:
        job["progress"] = p

    try:
        report = await asyncio.to_thread(
            bt.run_backtest, cfg, on_progress, job["cancel"].is_set)
        await asyncio.to_thread(_persist_report, job_id, report)
        job["report"] = _public_report(report)
        job["state"] = "done"
    except bt.Cancelled:
        job["state"] = "cancelled"
    except Exception as exc:
        job["state"] = "error"
        job["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        # keep the entry around long enough for the UI to fetch the result,
        # then drop it — the JSON on disk still serves late status requests
        asyncio.get_running_loop().call_later(
            JOB_TTL_S, _jobs.pop, job_id, None)


@app.post("/backtest/estimate")
async def backtest_estimate(cfg: dict) -> dict:
    try:
        return await asyncio.to_thread(bt.estimate_cost, cfg)
    except RuntimeError as exc:
        return {"error": str(exc)}


@app.post("/backtest")
async def backtest_start(cfg: dict) -> dict:
    running = sum(1 for j in _jobs.values() if j["state"] == "running")
    if running >= MAX_RUNNING_JOBS:
        raise HTTPException(
            status_code=429,
            detail="Ya hay backtests corriendo. Esperá a que terminen "
                   "(o cancelalos) y probá de nuevo.")
    job_id = uuid.uuid4().hex[:12]
    _jobs[job_id] = {
        "id": job_id, "state": "running",
        "progress": {"pct": 0.0, "detail": "starting"},
        "error": None, "report": None,
        "cancel": threading.Event(),
        "created_at": time.time(),
        "config": {k: cfg.get(k) for k in
                   ("mode", "dataset", "symbol", "days")},
    }
    asyncio.create_task(_run_backtest_job(job_id, cfg))
    return {"job_id": job_id}


@app.get("/backtest/{job_id}")
async def backtest_status(job_id: str) -> dict:
    job = _jobs.get(job_id)
    if job is None:
        report = await asyncio.to_thread(_load_public, job_id)
        if report is not None:   # finished in an earlier server life
            return {"id": job_id, "state": "done", "progress": {"pct": 100},
                    "report": report}
        return {"id": job_id, "state": "unknown"}
    return {k: job[k] for k in
            ("id", "state", "progress", "error", "report", "config")}


@app.delete("/backtest/{job_id}")
async def backtest_cancel(job_id: str) -> dict:
    job = _jobs.get(job_id)
    if job is not None:
        job["cancel"].set()
    return {"ok": True}


@app.get("/backtest/{job_id}/csv")
async def backtest_csv(job_id: str):
    csv_path = _csv_path(job_id)
    headers = {"Content-Disposition":
               f'attachment; filename="sweeps_backtest_{job_id}.csv"'}
    if csv_path.exists():   # pre-written at job completion
        return FileResponse(str(csv_path), media_type="text/csv",
                            headers=headers)
    path = _report_path(job_id)
    if not path.exists():
        return PlainTextResponse("not found", status_code=404)
    csv = await asyncio.to_thread(
        lambda: bt.report_to_csv(json.loads(path.read_text())))
    return PlainTextResponse(csv, media_type="text/csv", headers=headers)


def _list_finished() -> list:
    items = []
    if bt.RESULTS_DIR.exists():
        for p in sorted(bt.RESULTS_DIR.glob("*.json"),
                        key=lambda p: p.stat().st_mtime, reverse=True)[:20]:
            try:
                r = json.loads(p.read_text())
                items.append({"id": p.stem,
                              "generated_at": r.get("generated_at"),
                              "config": r.get("config"),
                              "totals": r.get("totals")})
            except Exception:
                continue
    return items


@app.get("/backtests")
async def backtest_list() -> dict:
    items = await asyncio.to_thread(_list_finished)
    running = [{"id": j["id"], "state": j["state"], "config": j["config"]}
               for j in _jobs.values() if j["state"] == "running"]
    return {"running": running, "finished": items}


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
