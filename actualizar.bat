@echo off
REM Descarga la ultima version de Sweeps y la instala encima de esta
REM carpeta, sin tocar tu clave (.env) ni lo ya descargado (.venv).
setlocal

REM cmd.exe lee el .bat por posicion MIENTRAS lo ejecuta, asi que si la
REM actualizacion sobrescribe este mismo archivo se ejecuta basura. Por eso
REM primero nos copiamos al temporal y seguimos desde ahi.
if "%~1"=="" (
  copy /y "%~f0" "%TEMP%\sweeps_actualizar.bat" >nul 2>&1
  if errorlevel 1 (
    echo.
    echo   No se pudo preparar la actualizacion ^(carpeta temporal bloqueada^).
    echo   Sacale una foto a esta ventana y mandala.
    echo.
    pause
    exit /b 1
  )
  "%TEMP%\sweeps_actualizar.bat" "%~dp0."
  exit /b
)
cd /d "%~1"
if "%PORT%"=="" set PORT=8080

echo.
echo   ====================================================
echo     Sweeps - actualizar a la ultima version
echo   ====================================================
echo.

REM Actualizar con Sweeps abierto deja el servidor viejo corriendo sobre
REM archivos nuevos: la app seguiria mostrando la version anterior.
powershell -NoProfile -Command "$c=New-Object Net.Sockets.TcpClient; try{$c.Connect('127.0.0.1',%PORT%);$c.Close();exit 0}catch{exit 1}"
if not errorlevel 1 (
  echo   Sweeps esta abierto en este momento.
  echo   Cerra la ventana negra de Sweeps y volve a hacer doble clic aca.
  echo.
  pause
  exit /b 1
)

set "ACTUAL=(ninguna)"
if exist VERSION set /p ACTUAL=<VERSION
echo   Version actual: %ACTUAL%
echo   Descargando...
echo.

powershell -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference='Stop'; try { [Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12; $base='https://github.com/chalozabala/yahora/archive/refs/heads'; $urls=@(\"$base/claude/sweeps-web-indicator-ouzwur.zip\", \"$base/main.zip\"); $zip=Join-Path $env:TEMP 'sweeps_update.zip'; $dir=Join-Path $env:TEMP 'sweeps_update'; if(Test-Path $dir){Remove-Item $dir -Recurse -Force}; $ok=$false; foreach($u in $urls){ try { Invoke-WebRequest -Uri $u -OutFile $zip -UseBasicParsing; $ok=$true; break } catch { } }; if(-not $ok){ Write-Host '  No se pudo descargar. Revisa tu internet.'; exit 1 }; Add-Type -AssemblyName System.IO.Compression.FileSystem; [IO.Compression.ZipFile]::ExtractToDirectory($zip,$dir); $src=(Get-ChildItem $dir -Directory | Select-Object -First 1).FullName; if(-not (Test-Path (Join-Path $src 'backend\main.py')) -or -not (Test-Path (Join-Path $src 'run.bat'))){ Write-Host '  Lo descargado no es una copia completa de Sweeps. No se toco nada.'; exit 1 }; foreach($d in @('backend','frontend')){ $t=Join-Path (Get-Location).Path $d; if(Test-Path $t){ Remove-Item $t -Recurse -Force } }; Copy-Item -Path (Join-Path $src '*') -Destination (Get-Location).Path -Recurse -Force; Remove-Item $zip -Force; Remove-Item $dir -Recurse -Force; Write-Host '  Archivos actualizados.'; exit 0 } catch { Write-Host ('  ERROR: ' + $_.Exception.Message); exit 1 }"

if errorlevel 1 (
  echo.
  echo   No se pudo actualizar. Tu instalacion quedo como estaba.
  echo   Si el problema sigue, sacale una foto a esta ventana y mandala.
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
echo.
pause
