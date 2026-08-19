@echo off
REM Sweeps - lanzador para Windows. Hace doble clic y listo.
setlocal
cd /d "%~dp0"
if "%PORT%"=="" set PORT=8080

REM --- buscar Python -------------------------------------------------
set "PY="
py -3 --version >nul 2>&1
if not errorlevel 1 set "PY=py -3"
if not defined PY (
  python --version >nul 2>&1
  if not errorlevel 1 set "PY=python"
)
if not defined PY (
  echo.
  echo   No se encontro Python en esta PC.
  echo.
  echo   1^) Instalalo desde https://www.python.org/downloads/
  echo   2^) IMPORTANTE: marca la casilla "Add python.exe to PATH"
  echo   3^) Volve a hacer doble clic en run.bat
  echo.
  pause
  exit /b 1
)

REM --- entorno propio (solo la primera vez) ---------------------------
if not exist ".venv" (
  echo.
  echo   Preparando el entorno por primera vez...
  echo.
  %PY% -m venv .venv
  if errorlevel 1 (
    echo   No se pudo crear el entorno. Sacale una foto a esta ventana y mandala.
    pause
    exit /b 1
  )
)
call ".venv\Scripts\activate.bat"

echo   Instalando lo necesario (unos 20 segundos la primera vez)...
echo.
python -m pip install --no-cache-dir --disable-pip-version-check -r backend\requirements.txt
if errorlevel 1 (
  echo.
  echo   Fallo la instalacion. Sacale una foto a esta ventana y mandala.
  pause
  exit /b 1
)

REM --- puerto libre? --------------------------------------------------
python -c "import socket,sys; s=socket.socket(); s.settimeout(0.5); sys.exit(0 if s.connect_ex(('127.0.0.1',%PORT%))==0 else 1)"
if not errorlevel 1 (
  echo.
  echo   El puerto %PORT% ya esta en uso.
  echo   Cerra la otra ventana de Sweeps y proba de nuevo.
  echo.
  pause
  exit /b 1
)

REM --- abrir el navegador cuando el server ya este arriba -------------
start "" /min cmd /c "timeout /t 5 /nobreak >nul & start http://localhost:%PORT%"

echo.
echo   Sweeps corriendo en:  http://localhost:%PORT%
echo   El navegador se abre solo en unos segundos.
echo   Para frenarlo: cerra esta ventana.
echo.
cd backend
python -m uvicorn main:app --host 0.0.0.0 --port %PORT%
echo.
echo   El servidor se detuvo.
pause
