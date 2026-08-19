#!/usr/bin/env bash
# Descarga la última versión de Sweeps y la instala encima de esta carpeta,
# sin tocar tu clave (.env) ni lo ya descargado (.venv).
set -e

# bash lee el script mientras lo ejecuta: si la actualización sobrescribe
# este archivo, el resto se interpreta mal. Nos copiamos al temporal y
# seguimos desde ahí, pasando la carpeta destino como argumento.
if [ "${SWEEPS_UPDATER_CHILD:-}" != "1" ]; then
  DEST="$(cd "$(dirname "$0")" && pwd)"
  SELF_COPY="$(mktemp)"
  trap 'rm -f "$SELF_COPY"' EXIT
  cp "$0" "$SELF_COPY"
  # "bash script" sólo necesita permiso de lectura: /tmp puede estar noexec
  SWEEPS_UPDATER_CHILD=1 SWEEPS_UPDATER_SELF="$SELF_COPY" \
    exec bash "$SELF_COPY" "$DEST"
fi

TMP=""
cleanup() {
  rm -f "${SWEEPS_UPDATER_SELF:-}" 2>/dev/null || true
  [ -n "$TMP" ] && rm -rf "$TMP" 2>/dev/null || true
}
trap cleanup EXIT

DEST="$1"
cd "$DEST"
TMP="$(mktemp -d)"
PORT="${PORT:-8080}"

echo ""
echo "  ===================================================="
echo "    Sweeps - actualizar a la última versión"
echo "  ===================================================="
echo ""

# Actualizar con el servidor abierto deja corriendo la versión vieja sobre
# archivos nuevos: la app seguiría mostrando lo anterior.
if command -v python3 >/dev/null 2>&1 && python3 -c "
import socket, sys
s = socket.socket(); s.settimeout(0.5)
sys.exit(0 if s.connect_ex(('127.0.0.1', int('$PORT'))) == 0 else 1)" 2>/dev/null; then
  echo "  Sweeps está corriendo ahora mismo."
  echo "  Frenalo (Ctrl+C en su ventana) y volvé a correr ./actualizar.sh"
  echo ""
  exit 1
fi

echo "  Versión actual: $([ -f VERSION ] && cat VERSION || echo '(ninguna)')"
echo "  Descargando..."

BASE="https://github.com/chalozabala/yahora/archive/refs/heads"
# La rama de trabajo primero; main como respaldo por si algún día se mergea
# y la rama deja de existir.
URLS="$BASE/claude/sweeps-web-indicator-ouzwur.zip $BASE/main.zip"

bajar() {
  if command -v curl >/dev/null 2>&1; then
    curl -fsSL "$1" -o "$TMP/sweeps.zip"
  elif command -v wget >/dev/null 2>&1; then
    wget -q "$1" -O "$TMP/sweeps.zip"
  else
    echo "  Falta curl o wget en esta máquina."; exit 1
  fi
}

BAJADO=0
for u in $URLS; do
  if bajar "$u"; then BAJADO=1; break; fi
done
if [ "$BAJADO" != "1" ]; then
  echo "  No se pudo descargar. Revisá tu internet y probá de nuevo."
  exit 1
fi

rm -rf "$TMP/x"
if command -v unzip >/dev/null 2>&1; then
  unzip -q "$TMP/sweeps.zip" -d "$TMP/x"
else
  python3 -c "import zipfile,sys; zipfile.ZipFile(sys.argv[1]).extractall(sys.argv[2])" \
    "$TMP/sweeps.zip" "$TMP/x"
fi

SRC="$(find "$TMP/x" -mindepth 1 -maxdepth 1 -type d | head -1)"
# Nunca instalar a ciegas: si lo bajado no trae el programa entero, dejar
# la instalación actual intacta es mucho mejor que romperla.
if [ -z "$SRC" ] || [ ! -f "$SRC/backend/main.py" ] || [ ! -f "$SRC/run.sh" ]; then
  echo "  Lo que se descargó no es una copia completa de Sweeps."
  echo "  No se toco nada. Avisá para que lo revisemos."
  exit 1
fi

# backend/ y frontend/ se reemplazan enteras: copiando sólo por encima, un
# archivo borrado en la versión nueva sobreviviría para siempre. Todo lo
# tuyo (.env, .venv, data_cache, backtest_results) vive en la raíz.
for d in backend frontend; do
  [ -d "$SRC/$d" ] && rm -rf "${DEST:?}/$d"
done
cp -Rf "$SRC/." "$DEST/"
chmod +x "$DEST"/run.sh "$DEST"/actualizar.sh "$DEST"/datos-reales.sh 2>/dev/null || true

echo ""
echo "  Versión nueva:  $([ -f VERSION ] && cat VERSION || echo '(ninguna)')"
echo ""
echo "  Listo. Corré ./run.sh"
echo ""
