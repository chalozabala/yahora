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


def main() -> int:
    load_env()
    key = os.getenv("DATABENTO_API_KEY", "")
    if not key:
        print("  No hay clave guardada todavia.")
        return 1

    try:
        import databento as db
    except ImportError:
        print("  Falta el modulo de datos reales (databento).")
        return 1

    print(f"  Probando la clave ...{key[-4:]} contra Databento...")
    try:
        client = db.Historical(key=key)
        datasets = list(client.metadata.list_datasets())
    except Exception as exc:
        kind = _classify(exc)
        if kind == "clave":
            print("  LA CLAVE NO ES VALIDA (Databento la rechazo).")
            print("  Revisala en databento.com -> Settings -> API Keys.")
        elif kind == "red":
            print("  NO SE PUDO CONECTAR A DATABENTO.")
            print("  No es problema de la clave: revisa tu internet (o el")
            print("  firewall / proxy de la red) y proba de nuevo.")
        else:
            print(f"  No se pudo verificar: {exc}")
        return 1

    print("  Clave OK.")
    if DATASET not in datasets:
        print(f"  OJO: tu cuenta no lista {DATASET} (datos de CME).")
        print("  Sin eso no vas a poder usar ES/NQ. Datasets disponibles:")
        print("   ", ", ".join(sorted(datasets)[:12]) or "(ninguno)")
        return 1

    print(f"  Tenes acceso a {DATASET} (CME: ES, NQ...).")
    try:
        rng = client.metadata.get_dataset_range(dataset=DATASET)
        start = rng.get("start") or rng.get("start_date")
        end = rng.get("end") or rng.get("end_date")
        print(f"  Historico disponible: {start}  ->  {end}")
        print("  Con eso ya podes correr el Backtest de 10 dias.")
    except Exception:
        pass

    print()
    print("  Nota: el modo 'Live' (tiempo real) necesita ademas una")
    print("  suscripcion de datos en vivo de CME. Si no la tenes, el")
    print("  Backtest y el Replay con historico funcionan igual.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
