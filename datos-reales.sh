#!/usr/bin/env bash
# Instala el módulo de datos reales y guarda la clave de Databento.
# Se corre UNA sola vez. Después usás ./run.sh normalmente.
set -e
cd "$(dirname "$0")"

echo ""
echo "  ===================================================="
echo "    Sweeps - activar datos reales (Databento)"
echo "  ===================================================="
echo ""

if ! command -v python3 >/dev/null 2>&1; then
  echo "  Falta python3. Instalalo y volvé a correr ./datos-reales.sh"
  exit 1
fi

if [ ! -d .venv ]; then
  echo "  Preparando el entorno..."
  python3 -m venv .venv || {
    echo "  No se pudo crear el entorno. En Debian/Ubuntu:"
    echo "      sudo apt install python3-venv"
    exit 1
  }
fi
. .venv/bin/activate
python -m pip install -q --disable-pip-version-check -r backend/requirements.txt

echo ""
echo "  Paso 1 de 2: descargando el módulo de datos reales."
echo "  Son unos 250 MB, puede tardar varios minutos. Dejalo trabajar."
echo ""
python -m pip install --no-cache-dir --disable-pip-version-check \
  -r backend/requirements-databento.txt

echo ""
echo "  Paso 2 de 2: tu clave de Databento."
echo "  La sacás de databento.com -> Settings -> API Keys (empieza con 'db-')."
echo "  Si ya la guardaste antes, apretá Enter para dejar la que está."
echo ""
printf "  Pegá la clave y apretá Enter: "
read -r KEY

if [ -n "$KEY" ]; then
  printf 'DATABENTO_API_KEY=%s\n' "$KEY" > .env
  chmod 600 .env 2>/dev/null || true
  echo ""
  echo "  Clave guardada en el archivo .env (no se sube a GitHub)."
fi

echo ""
python backend/check_key.py || true
echo ""
echo "  ===================================================="
echo "    Listo. Ahora corré ./run.sh y elegí:"
echo "      - 'Databento · Replay' para ver un rato del pasado"
echo "      - pestaña Backtest para los 10 días"
echo "      - 'Databento · Live' si tenés datos en tiempo real"
echo "  ===================================================="
echo ""
