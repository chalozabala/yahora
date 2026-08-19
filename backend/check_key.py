"""Verifica que la clave de Databento funcione y qué datos alcanza.

Lo corren datos-reales.bat / datos-reales.sh después de instalar, para
avisar en el momento si la clave está mal o si no tiene permisos, en vez
de que el error aparezca recién al usar la app.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import load_env  # noqa: E402

DATASET = "GLBX.MDP3"          # CME Globex: ES, NQ, etc.

# nombres de excepciones que significan "no llegué al servidor", no
# "el servidor me rechazó" — un corte de red trae un 403 del proxy en el
# texto y no hay que confundirlo con una clave inválida
_NETWORK_ERRORS = {"ConnectionError", "ProxyError", "Timeout", "ConnectTimeout",
                   "ReadTimeout", "SSLError", "NewConnectionError"}


def _classify(exc: Exception) -> str:
    names = {c.__name__ for c in type(exc).__mro__}
    if names & _NETWORK_ERRORS:
        return "red"
    status = getattr(exc, "http_status", None) or getattr(exc, "status_code", None)
    if status in (401, 403):
        return "clave"
    return "otro"


def verificar(key: str = "") -> dict:
    """Comprueba la clave contra Databento. Devuelve un dict apto tanto
    para la consola como para la interfaz web."""
    key = key or os.getenv("DATABENTO_API_KEY", "")
    if not key:
        return {"ok": False, "caso": "sin_clave",
                "mensaje": "No hay clave guardada todavia."}
    try:
        import databento as db
    except ImportError:
        return {"ok": False, "caso": "sin_modulo",
                "mensaje": "Falta el modulo de datos reales (databento)."}

    try:
        client = db.Historical(key=key)
        datasets = list(client.metadata.list_datasets())
    except Exception as exc:
        caso = _classify(exc)
        if caso == "clave":
            msg = ("Databento rechazo la clave. Revisala en databento.com "
                   "-> Settings -> API Keys.")
        elif caso == "red":
            msg = ("No se pudo conectar a Databento. No es la clave: "
                   "revisa tu internet o el firewall de la red.")
        else:
            msg = f"No se pudo verificar: {exc}"
        return {"ok": False, "caso": caso, "mensaje": msg}

    res = {"ok": True, "caso": "ok", "clave_termina_en": key[-4:],
           "cme": DATASET in datasets}
    if not res["cme"]:
        res["ok"] = False
        res["caso"] = "sin_cme"
        res["mensaje"] = (f"La clave funciona pero tu cuenta no incluye "
                          f"{DATASET} (datos de CME), asi que ES/NQ no van.")
        res["datasets"] = sorted(datasets)[:12]
        return res

    res["mensaje"] = f"Clave OK y con acceso a {DATASET} (CME: ES, NQ...)."
    try:
        rng = client.metadata.get_dataset_range(dataset=DATASET)
        res["historico_desde"] = rng.get("start") or rng.get("start_date")
        res["historico_hasta"] = rng.get("end") or rng.get("end_date")
    except Exception:
        pass
    return res


def main() -> int:
    load_env()
    r = verificar()
    print(f"  {r['mensaje']}")
    if r.get("datasets"):
        print("  Datasets disponibles:", ", ".join(r["datasets"]))
    if r.get("historico_desde"):
        print(f"  Historico disponible: {r['historico_desde']} -> "
              f"{r['historico_hasta']}")
        print("  Con eso ya podes correr el Backtest de 10 dias.")
    if r["ok"]:
        print()
        print("  Nota: el modo 'Live' (tiempo real) necesita ademas una")
        print("  suscripcion de datos en vivo de CME. Si no la tenes, el")
        print("  Backtest y el Replay con historico funcionan igual.")
    return 0 if r["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
