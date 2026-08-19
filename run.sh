#!/usr/bin/env bash
# Arranque en un comando:  ./run.sh   →   http://localhost:8080
set -e
cd "$(dirname "$0")"
PORT="${PORT:-8080}"

if ! command -v python3 >/dev/null 2>&1; then
  echo "Falta python3. Instalalo y volvé a correr ./run.sh"
  exit 1
fi

# venv propio: evita el error 'externally-managed-environment' (PEP 668)
# y no toca el Python del sistema
if [ ! -d .venv ]; then
  python3 -m venv .venv || {
    echo "No se pudo crear el entorno. En Debian/Ubuntu instalá:"
    echo "    sudo apt install python3-venv"
    echo "y volvé a correr ./run.sh"
    exit 1
  }
fi
. .venv/bin/activate
python -m pip install -q --upgrade pip
python -m pip install -q -r backend/requirements.txt

if python - "$PORT" <<'EOF'
import socket, sys
s = socket.socket()
s.settimeout(0.5)
sys.exit(0 if s.connect_ex(("127.0.0.1", int(sys.argv[1]))) == 0 else 1)
EOF
then
  echo "El puerto $PORT ya está en uso."
  echo "Cerrá la otra ventana de Sweeps, o corré:  PORT=8081 ./run.sh"
  exit 1
fi

echo ""
echo "  Sweeps corriendo en:  http://localhost:$PORT"
echo "  El navegador se abre solo en unos segundos. (Ctrl+C para frenar)"
echo ""

# abrir el navegador una vez que el server esté arriba
( sleep 5
  if command -v xdg-open >/dev/null 2>&1; then xdg-open "http://localhost:$PORT"
  elif command -v open >/dev/null 2>&1; then open "http://localhost:$PORT"
  fi ) >/dev/null 2>&1 &

cd backend
exec python -m uvicorn main:app --host 0.0.0.0 --port "$PORT"
