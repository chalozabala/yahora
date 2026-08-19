@echo off
REM Instala el modulo de datos reales y guarda la clave de Databento.
REM Se corre UNA sola vez. Despues usas run.bat normalmente.
setlocal
cd /d "%~dp0"

echo.
echo   ====================================================
echo     Sweeps - activar datos reales (Databento)
echo   ====================================================
echo.

REM --- buscar Python -------------------------------------------------
set "PY="
py -3 --version >nul 2>&1
if not errorlevel 1 set "PY=py -3"
if not defined PY (
  python --version >nul 2>&1
  if not errorlevel 1 set "PY=python"
)
if not defined PY (
  echo   No se encontro Python. Instalalo desde
  echo   https://www.python.org/downloads/  ^(marca "Add python.exe to PATH"^)
  pause
  exit /b 1
)

if not exist ".venv" (
  echo   Preparando el entorno...
  %PY% -m venv .venv
  if errorlevel 1 ( echo   No se pudo crear el entorno. & pause & exit /b 1 )
)
call ".venv\Scripts\activate.bat"
python -m pip install -q --disable-pip-version-check -r backend\requirements.txt

echo.
echo   Paso 1 de 2: descargando el modulo de datos reales.
echo   Son unos 250 MB, puede tardar varios minutos. Dejalo trabajar.
echo.
python -m pip install --no-cache-dir --disable-pip-version-check -r backend\requirements-databento.txt
if errorlevel 1 (
  echo.
  echo   Fallo la descarga. Sacale una foto a esta ventana y mandala.
  pause
  exit /b 1
)

echo.
echo   Paso 2 de 2: tu clave de Databento.
echo   La sacas de databento.com -^> Settings -^> API Keys (empieza con "db-").
echo   Si ya la guardaste antes, apreta Enter para dejar la que esta.
echo.
set "KEY="
set /p KEY="  Pega la clave y apreta Enter: "

if not "%KEY%"=="" (
  ^> .env echo DATABENTO_API_KEY=%KEY%
  echo.
  echo   Clave guardada en el archivo .env ^(no se sube a GitHub^).
)

echo.
python backend\check_key.py
echo.
echo   ====================================================
echo     Listo. Ahora abri run.bat y elegi:
echo       - "Databento - Replay" para ver un rato del pasado
echo       - pestana Backtest para los 10 dias
echo       - "Databento - Live" si tenes datos en tiempo real
echo   ====================================================
echo.
pause
