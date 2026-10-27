# Auditoría SAP DAMAGE-BOX

Aplicación Django básica para cargar movimientos de `LTAP` en el bin `DAMAGE-BOX`, resolver sus códigos de barras en `MEAN` y validar cantidades con un escáner.

## Ejecutar

1. Copia `.env.example` a `.env` y completa `SAP_CONNECTION_NAME` (nombre exacto de la conexión en SAP Logon), `SAP_CLIENT`, `SAP_USER` y `SAP_PASSWORD`. Configura también `AUDIT_DELETE_PASSWORD` para habilitar el borrado del historial. No compartas ni subas `.env` al repositorio.
2. Si Windows no encuentra `saplogon.exe`, completa `SAPLOGON_PATH` en `.env`. Habilita SAP GUI Scripting si todavía no está habilitado.
3. Reinicia Django después de modificar `.env`. En PowerShell, desde esta carpeta, ejecuta:

   ```powershell
   .\venv\Scripts\Activate.ps1
   python manage.py migrate
   python manage.py runserver 127.0.0.1:8765
   ```

4. Abre <http://127.0.0.1:8765/>. La automatización inicia SAP Logon, abre la conexión indicada y completa el login si aparece la pantalla estándar de SAP; si SAP ya tiene una sesión iniciada, la reutiliza.

La automatización escribe directamente el rango de fechas de LTAP, agrupa por `MATNR` + `J_3ASIZE` + Stock Category, suma `NISTM` y carga por portapapeles los filtros de `MEAN` para obtener en una sola consulta los `EAN11` que comienzan con `200` o `00200`. Solo permite auditar combinaciones con exactamente un Stock ID; los faltantes, ambiguos o duplicados quedan bloqueados. Cada lectura suma una unidad hasta completar la cantidad de SAP.
