# Historial de versiones

La versión que estás corriendo se ve abajo a la derecha en la app.
Si no coincide con la última de esta lista, corré `actualizar.bat`
(Windows) o `./actualizar.sh` (Mac/Linux).

- **2026.08.19-7** — Funciona con planes sin MBP-10: el libro ahora es
  configurable (MBP-10 / MBP-1 / sin libro) y por defecto prueba de mayor
  a menor, quedándose con lo que tu plan permita en vez de fallar entero.
- **2026.08.19-6** — Los errores de datos reales ahora se ven en el medio
  de la pantalla, traducidos y con el diagnóstico concreto (qué falta y en
  qué carpeta), más un botón para verificar la clave contra Databento.
  Aviso de "conectando" para que la pantalla no quede negra y muda.
- **2026.08.19-5** — Arreglo crítico: `datos-reales.bat` no guardaba la
  clave (escribía un error en vez del archivo). Además: no se puede
  actualizar con Sweeps abierto, detección real de versión desactualizada
  (página vs servidor), y la app queda accesible sólo desde tu PC.
- **2026.08.19-4** — Botón de actualizar (`actualizar.bat`), número de
  versión visible en la app y anti-caché del navegador.
- **2026.08.19-3** — Selector de instrumentos (ES, NQ, micros, etc.) y
  muestra el contrato vigente al que resuelve (ej. `NQ.v.0 → NQZ6`).
- **2026.08.19-2** — Instalación liviana: el módulo de datos reales
  (~250 MB) pasó a un paso aparte, `datos-reales.bat`.
- **2026.08.19-1** — Lanzador de Windows `run.bat`.
