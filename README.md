# Auditoría SAP DAMAGE-BOX

Aplicación Django básica para cargar movimientos de `LTAP` en el bin `DAMAGE-BOX`, resolver sus códigos de barras en `MEAN` y validar cantidades con un escáner.

## Ejecutar

1. Abre SAP Logon, inicia sesión y deja SAP GUI abierto.
2. Habilita SAP GUI Scripting si todavía no está habilitado.
3. En PowerShell, desde esta carpeta, ejecuta:

   ```powershell
   .\venv\Scripts\Activate.ps1
   python manage.py migrate
   python manage.py runserver 127.0.0.1:8765
   ```

4. Abre <http://127.0.0.1:8765/>.

La automatización escribe directamente el rango de fechas de LTAP, agrupa por `MATNR` + `J_3ASIZE` + Stock Category, suma `NISTM` y carga por portapapeles los filtros de `MEAN` para obtener en una sola consulta los `EAN11` que comienzan con `200` o `00200`. Solo permite auditar combinaciones con exactamente un Stock ID; los faltantes, ambiguos o duplicados quedan bloqueados. Cada lectura suma una unidad hasta completar la cantidad de SAP.
