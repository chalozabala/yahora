"""El gateway rechaza el esquema DENTRO del stream, no en subscribe().

Reproduce el caso real: un plan con Trades y MBP-1 pero sin MBP-10. El
feed tiene que bajar solo a MBP-1 y seguir andando, en vez de fallar.
"""

import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import feeds  # noqa: E402


class _ClienteFalso:
    """Imita al Live de databento: acepta cualquier subscribe y despues
    manda el rechazo por el stream, como hace el gateway de verdad."""

    def __init__(self, sin_autorizar=("mbp-10",)):
        self.sin_autorizar = sin_autorizar
        self.suscripciones = []
        self.detenido = False

    def subscribe(self, dataset, schema, stype_in, symbols):
        self.suscripciones.append(schema)   # nunca levanta excepcion

    def stop(self):
        self.detenido = True

    def __aiter__(self):
        return self._gen()

    async def _gen(self):
        malos = [s for s in self.suscripciones if s in self.sin_autorizar]
        if malos:
            yield ("error", f"Not authorized for {malos[0]} schema")
            return
        yield ("trade", None)
        while True:                          # se queda vivo como el real
            await asyncio.sleep(0.05)


def _eventos_falsos(rec, symbol_map):
    tipo, dato = rec
    if tipo == "error":
        return [{"type": "error", "message": dato}]
    return [{"type": "trade", "ts": 1, "px": 100.0, "sz": 1, "side": "B"}]


@pytest.fixture(autouse=True)
def _parchar(monkeypatch):
    monkeypatch.setattr(feeds, "_record_events", _eventos_falsos)
    monkeypatch.setenv("DATABENTO_API_KEY", "db-test")


def test_detecta_el_rechazo_por_esquema():
    assert feeds.es_error_de_esquema("Not authorized for mbp-10 schema")
    assert feeds.es_error_de_esquema("not entitled to mbp-1 schema")
    # una clave invalida NO es un problema de esquema: bajar el libro no
    # lo arreglaria, y disfrazarlo mandaria al usuario a buscar mal
    assert not feeds.es_error_de_esquema("Invalid API key")
    assert not feeds.es_error_de_esquema("CRAM authentication failed")


async def _correr(feed, segundos=8.0):
    """Junta los eventos del arranque. El feed real (y el falso) se quedan
    vivos esperando datos, asi que se corta por tiempo."""
    salida = []

    async def _juntar():
        async for ev in feed.events():
            salida.append(ev)

    tarea = asyncio.ensure_future(_juntar())
    try:
        await asyncio.wait_for(asyncio.shield(tarea), timeout=segundos)
    except asyncio.TimeoutError:
        tarea.cancel()
        try:
            await tarea
        except (asyncio.CancelledError, Exception):
            pass
    return salida


def test_baja_a_mbp1_cuando_el_plan_no_tiene_mbp10(monkeypatch):
    creados = []

    def _conectar(self, schema):
        c = _ClienteFalso(sin_autorizar=("mbp-10",))
        creados.append((schema, c))
        c.subscribe(None, "trades", None, None)
        if schema:
            c.subscribe(None, schema, None, None)
        return c

    monkeypatch.setattr(feeds.LiveFeed, "_connect", _conectar)
    feed = feeds.LiveFeed(dataset="GLBX.MDP3", symbol="ES.v.0")
    evs = asyncio.run(_correr(feed))

    intentos = [s for s, _ in creados]
    assert intentos[0] == "mbp-10"       # primero lo mejor
    assert intentos[1] == "mbp-1"        # y al rechazo, baja un escalon
    assert feed.depth_used == "mbp-1"

    # el usuario NO ve un error: ve que arranco, con un aviso
    assert not [e for e in evs if e["type"] == "error"]
    corriendo = [e for e in evs if e.get("state") == "running"]
    assert corriendo and corriendo[0]["depth"] == "mbp-1"
    avisos = [e for e in evs if e.get("state") == "aviso"]
    assert avisos and "MBP-10" in avisos[0]["detail"]
    # y los datos fluyen
    assert [e for e in evs if e["type"] == "trade"]


def test_sigue_solo_con_trades_si_no_hay_ningun_libro(monkeypatch):
    def _conectar(self, schema):
        c = _ClienteFalso(sin_autorizar=("mbp-10", "mbp-1"))
        c.subscribe(None, "trades", None, None)
        if schema:
            c.subscribe(None, schema, None, None)
        return c

    monkeypatch.setattr(feeds.LiveFeed, "_connect", _conectar)
    feed = feeds.LiveFeed(dataset="GLBX.MDP3", symbol="ES.v.0")
    evs = asyncio.run(_correr(feed))

    assert feed.depth_used is None            # sin libro, pero andando
    assert not [e for e in evs if e["type"] == "error"]
    assert [e for e in evs if e["type"] == "trade"]
    avisos = [e for e in evs if e.get("state") == "aviso"]
    assert avisos and "sin mapa de calor" in avisos[0]["detail"]


def test_un_error_de_clave_no_se_disfraza_de_problema_de_libro(monkeypatch):
    def _conectar(self, schema):
        c = _ClienteFalso(sin_autorizar=())
        c.subscribe(None, "trades", None, None)
        return c

    async def _gen_malo(self):
        yield ("error", "Invalid API key")

    monkeypatch.setattr(feeds.LiveFeed, "_connect", _conectar)
    monkeypatch.setattr(_ClienteFalso, "_gen", _gen_malo)
    feed = feeds.LiveFeed(dataset="GLBX.MDP3", symbol="ES.v.0")
    evs = asyncio.run(_correr(feed))
    errores = [e for e in evs if e["type"] == "error"]
    assert errores and "Invalid API key" in errores[0]["message"]
