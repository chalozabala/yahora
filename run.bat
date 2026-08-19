@echo off
REM Sweeps - lanzador para Windows. Hace doble clic y listo.
setlocal
cd /d "%~dp0"
if "%PORT%"=="" set PORT=8080

REM rama que espera al servidor y abre el navegador (se relanza sola)
if /i "%~1"=="--abrir" goto abrir

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

REM --- entorno propio -------------------------------------------------
REM Se comprueba activate.bat, no la carpeta: si una instalacion anterior
REM quedo a medias, .venv existe pero no sirve, y sin esto seguiriamos
REM usandola (o instalando en el Python del sistema) para siempre.
if not exist ".venv\Scripts\activate.bat" (
  if exist ".venv" (
    echo   Habia un entorno incompleto: se rehace.
    rd /s /q ".venv"
  )
  echo.
  echo   Preparando el entorno por primera vez...
  echo.
  %PY% -m venv .venv
)
if not exist ".venv\Scripts\activate.bat" (
  echo.
  echo   No se pudo crear el entorno de Python.
  echo   Sacale una foto a esta ventana y mandala.
  echo.
  pause
  exit /b 1
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
call :puerto
if not errorlevel 1 (
  echo.
  echo   El puerto %PORT% ya esta en uso.
  echo   Cerra la otra ventana de Sweeps y proba de nuevo.
  echo.
  pause
  exit /b 1
)

start "" /min cmd /c ""%~f0" --abrir"

echo.
echo   Sweeps corriendo en:  http://localhost:%PORT%
echo   El navegador se abre solo cuando este listo.
echo   Para frenarlo: cerra esta ventana.
echo.
cd backend
REM 127.0.0.1: solo esta PC. Con 0.0.0.0 Windows pide permiso de firewall
REM y ademas quedaria accesible para cualquiera en la misma red.
python -m uvicorn main:app --host 127.0.0.1 --port %PORT%
echo.
echo   El servidor se detuvo.
pause
exit /b

REM --- subrutina: errorlevel 0 si el puerto responde ------------------
:puerto
powershell -NoProfile -Command "$c=New-Object Net.Sockets.TcpClient; try{$c.Connect('127.0.0.1',%PORT%);$c.Close();exit 0}catch{exit 1}"
REM devolver explicitamente el codigo: "exit /b" pelado no es fiable
exit /b %errorlevel%

REM --- rama que abre el navegador cuando el servidor responde ---------
:abrir
for /l %%i in (1,1,60) do (
  call :puerto
  if not errorlevel 1 goto abrirya
  ping -n 2 127.0.0.1 >nul
)
:abrirya
REM La URL lleva la version: asi el navegador no puede resolverla desde su
REM cache (nunca vio esa direccion), que es como se colaba la pagina vieja.
set "VER="
if exist VERSION set /p VER=<VERSION
if defined VER (
  start "" "http://localhost:%PORT%/?v=%VER%"
) else (
  start "" "http://localhost:%PORT%/"
)
exit /b
