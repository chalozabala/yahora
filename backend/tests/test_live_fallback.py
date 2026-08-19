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


class _IteradorFalso:
    """Imita a LiveIterator: arranca el cliente al crearse y lo TERMINA en
    __del__. Esa segunda parte es la que rompió en producción."""

    def __init__(self, cliente, guion):
        cliente.start()
        self._cliente = cliente
        self._guion = list(guion)

    def __del__(self):
        try:
            self._cliente.terminate()
        except Exception:
            pass

    async def __anext__(self):
        if self._guion:
            return self._guion.pop(0)
        while True:                      # vivo, esperando datos
            await asyncio.sleep(0.05)


class _ClienteFalso:
    """Imita al Live de databento con su contrato real: acepta cualquier
    subscribe, manda el rechazo por el stream, y NO se puede volver a
    arrancar despues de terminate()."""

    def __init__(self, sin_autorizar=("mbp-10",), guion=None):
        self.sin_autorizar = sin_autorizar
        self.suscripciones = []
        self.conectado = True
        self.arrancado = False
        self.terminado = False
        self._guion = guion

    def subscribe(self, dataset, schema, stype_in, symbols):
        self.suscripciones.append(schema)

    def start(self):
        if not self.conectado:
            raise ValueError("must call subscribe() before starting live client")
        if self.arrancado:
            raise ValueError("client is already started")
        self.arrancado = True

    def stop(self):
        self.arrancado = False

    def terminate(self):
        self.terminado = True
        self.conectado = False
        self.arrancado = False

    def __aiter__(self):
        if self._guion is not None:
            guion = self._guion
        else:
            malos = [s for s in self.suscripciones if s in self.sin_autorizar]
            guion = ([("error", f"Not authorized for {malos[0]} schema")]
                     if malos else [("trade", None)])
        return _IteradorFalso(self, guion)


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
        c = _ClienteFalso(guion=[("error", "Invalid API key")])
        c.subscribe(None, "trades", None, None)
        return c

    monkeypatch.setattr(feeds.LiveFeed, "_connect", _conectar)
    feed = feeds.LiveFeed(dataset="GLBX.MDP3", symbol="ES.v.0")
    evs = asyncio.run(_correr(feed))
    errores = [e for e in evs if e["type"] == "error"]
    assert errores and "Invalid API key" in errores[0]["message"]


def test_no_se_mata_la_conexion_al_terminar_la_prueba(monkeypatch):
    """Regresión del error real: 'must call subscribe() before starting
    live client'. El iterador de prueba se recolectaba, su __del__ llamaba
    a terminate(), y al seguir con el stream se intentaba arrancar un
    cliente ya muerto. El iterador tiene que ser uno solo y sobrevivir."""
    import gc

    clientes = []

    def _conectar(self, schema):
        c = _ClienteFalso(sin_autorizar=())     # todo autorizado
        c.subscribe(None, "trades", None, None)
        clientes.append(c)
        return c

    monkeypatch.setattr(feeds.LiveFeed, "_connect", _conectar)
    feed = feeds.LiveFeed(dataset="GLBX.MDP3", symbol="ES.v.0")

    async def _con_basura():
        salida = []

        async def _juntar():
            async for ev in feed.events():
                salida.append(ev)
                gc.collect()      # fuerza el escenario que rompio
        t = asyncio.ensure_future(_juntar())
        try:
            await asyncio.wait_for(asyncio.shield(t), timeout=4)
        except asyncio.TimeoutError:
            t.cancel()
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass
        return salida

    evs = asyncio.run(_con_basura())
    assert not clientes[0].terminado, "se mato la conexion que servia"
    errores = [e for e in evs if e["type"] == "error"]
    assert not errores, f"no deberia haber error: {errores}"
    assert [e for e in evs if e["type"] == "trade"]
