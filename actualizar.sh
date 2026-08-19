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
  cp "$0" "$SELF_COPY"
  chmod +x "$SELF_COPY"
  SWEEPS_UPDATER_CHILD=1 SWEEPS_UPDATER_SELF="$SELF_COPY" \
    exec "$SELF_COPY" "$DEST"
fi

cleanup() { rm -f "${SWEEPS_UPDATER_SELF:-}" "${TMP:-}/sweeps.zip" 2>/dev/null || true
            [ -n "${TMP:-}" ] && rm -rf "$TMP" 2>/dev/null || true; }
trap cleanup EXIT

DEST="$1"
cd "$DEST"
URL="https://github.com/chalozabala/yahora/archive/refs/heads/claude/sweeps-web-indicator-ouzwur.zip"
TMP="$(mktemp -d)"

echo ""
echo "  ===================================================="
echo "    Sweeps - actualizar a la última versión"
echo "  ===================================================="
echo ""
echo "  Versión actual: $([ -f VERSION ] && cat VERSION || echo '(ninguna)')"
echo "  Descargando..."

if command -v curl >/dev/null 2>&1; then
  curl -fsSL "$URL" -o "$TMP/sweeps.zip"
elif command -v wget >/dev/null 2>&1; then
  wget -q "$URL" -O "$TMP/sweeps.zip"
else
  echo "  Falta curl o wget."; exit 1
fi

if command -v unzip >/dev/null 2>&1; then
  unzip -q "$TMP/sweeps.zip" -d "$TMP/x"
else
  python3 -c "import zipfile,sys; zipfile.ZipFile(sys.argv[1]).extractall(sys.argv[2])" \
    "$TMP/sweeps.zip" "$TMP/x"
fi

SRC="$(find "$TMP/x" -mindepth 1 -maxdepth 1 -type d | head -1)"
[ -n "$SRC" ] || { echo "  El archivo descargado no tiene el formato esperado."; exit 1; }
cp -Rf "$SRC/." "$DEST/"
chmod +x "$DEST"/run.sh "$DEST"/actualizar.sh "$DEST"/datos-reales.sh 2>/dev/null || true

echo ""
echo "  Versión nueva:  $([ -f VERSION ] && cat VERSION || echo '(ninguna)')"
echo ""
echo "  Listo. Corré ./run.sh"
echo "  IMPORTANTE: en el navegador apretá Ctrl+F5 (Cmd+Shift+R en Mac)."
echo ""
