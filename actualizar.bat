@echo off
REM Descarga la ultima version de Sweeps y la instala encima de esta
REM carpeta, sin tocar tu clave (.env) ni lo ya descargado (.venv).
setlocal

REM cmd.exe lee el .bat por posicion MIENTRAS lo ejecuta, asi que si la
REM actualizacion sobrescribe este mismo archivo se ejecuta basura. Por eso
REM primero nos copiamos al temporal y seguimos desde ahi.
if "%~1"=="" (
  copy /y "%~f0" "%TEMP%\sweeps_actualizar.bat" >nul
  "%TEMP%\sweeps_actualizar.bat" "%~dp0."
  exit /b
)
cd /d "%~1"

echo.
echo   ====================================================
echo     Sweeps - actualizar a la ultima version
echo   ====================================================
echo.

set "ACTUAL=(ninguna)"
if exist VERSION set /p ACTUAL=<VERSION
echo   Version actual: %ACTUAL%
echo   Descargando...
echo.

powershell -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference='Stop'; try { [Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12; $url='https://github.com/chalozabala/yahora/archive/refs/heads/claude/sweeps-web-indicator-ouzwur.zip'; $zip=Join-Path $env:TEMP 'sweeps_update.zip'; $dir=Join-Path $env:TEMP 'sweeps_update'; if(Test-Path $dir){Remove-Item $dir -Recurse -Force}; Invoke-WebRequest -Uri $url -OutFile $zip -UseBasicParsing; Add-Type -AssemblyName System.IO.Compression.FileSystem; [IO.Compression.ZipFile]::ExtractToDirectory($zip,$dir); $src=(Get-ChildItem $dir -Directory | Select-Object -First 1).FullName; Copy-Item -Path (Join-Path $src '*') -Destination (Get-Location).Path -Recurse -Force; Remove-Item $zip -Force; Remove-Item $dir -Recurse -Force; Write-Host '  Archivos actualizados.'; exit 0 } catch { Write-Host ('  ERROR: ' + $_.Exception.Message); exit 1 }"

if errorlevel 1 (
  echo.
  echo   No se pudo actualizar. Revisa tu internet.
  echo   Si Sweeps esta abierto, cerra esa ventana negra y proba de nuevo.
  echo.
  pause
  exit /b 1
)

set "NUEVA=(ninguna)"
if exist VERSION set /p NUEVA=<VERSION
echo.
echo   Version nueva:  %NUEVA%
echo.
echo   Listo. Abri run.bat.
echo   IMPORTANTE: en el navegador apreta Ctrl+F5 para ver lo nuevo.
echo.
pause
