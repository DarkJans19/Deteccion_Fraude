# Dashboard de Bogotá

**Lista de reportes con el SHA-256 completo, mapa Leaflet y estadísticas por las 20 localidades de Bogotá**. Implementada con React, TypeScript, Tailwind y componentes shadcn/ui. Al usar MongoDB permite consultar y agregar seguimiento por caso. No tiene formularios de registro ni datos simulados en la aplicación.

Para Docker, MongoDB Atlas, credenciales, estructura BSON y API de seguimiento, consulta [la guía del proyecto](../../README.md). El comando `hasher.py` de esta guía conserva el modo local anterior con SQLite.

## Ejecutar

Desde la raíz del repositorio:

```powershell
npm.cmd --prefix frontend/dashboard ci
npm.cmd --prefix frontend/dashboard run build
python backend/core/traceability/hasher.py
```

Abre **http://127.0.0.1:8765/dashboard/trazabilidad**. En desarrollo ejecuta el backend y `npm.cmd --prefix frontend/dashboard run dev` en otra terminal; abre **http://127.0.0.1:5173**. Si cambias el puerto del backend, define `$env:API_TARGET = "http://127.0.0.1:8766"` antes de iniciar Vite. El puerto de Vite es fijo en 5173.

## Interacción

- Selecciona un reporte para centrar el mapa y abrir su hash en un popup. Los reportes con las mismas coordenadas se agrupan en un marcador que permite consultar todos.
- Copia el hash desde la lista o búscalo junto con el título, ID o localidad.
- Filtra con el selector, haciendo clic en una localidad del mapa o en sus estadísticas.
- Los conteos corresponden a todos los reportes que coinciden con la búsqueda, independientemente del filtro de localidad. Los reportes fuera de Bogotá o sin coordenadas válidas se contabilizan por separado.
- Acerca el mapa con los botones `+` y `−` para ver las manzanas. «Ver todo Bogotá» incluye el territorio rural y Sumapaz.

## Base geográfica

`public/geo/` contiene una versión optimizada del **MANZ.geojson adjunto**, con sus 44.521 manzanas, y los límites oficiales de las 20 localidades. El archivo original contiene manzanas, no localidades: se complementa con la capa de [Catastro Bogotá / SDP](https://sig.catastrobogota.gov.co/arcgis/rest/services/ordenamientoterritorial/localidad/MapServer/0).

Los límites de localidad conservan sus coordenadas completas. La clasificación de reportes usa inclusión de punto en polígono con Turf, incluidas geometrías multipolígono y huecos. Se calcula solo para visualizar y contar: no añade campos ni altera el hash almacenado. Debido al redondeo de la ubicación del reporte, la localidad es aproximada, especialmente cerca de sus límites.

Las manzanas se simplificaron y repartieron por localidad: se cargan bajo demanda cuando el zoom es 14 o superior. Las seis manzanas no asignadas se conservan en una capa aparte. La cartografía preparada ocupa unos 15 MB en total frente a los 129 MB originales; no se carga completa al abrir la página.

No se usan tiles, geocodificación ni peticiones a proveedores de mapas en tiempo de ejecución. Todo se sirve localmente; la atribución del mapa enlaza a las fuentes. `manifest.json` documenta la procedencia y el hash del adjunto. El proceso reproducible está en `../../backend/prepare_bogota_geo.py`.

## Verificar

Desde esta carpeta:

```powershell
npm.cmd run build
npm.cmd test
```

Playwright usa Microsoft Edge en Windows y Chromium en Linux/macOS. Instala este último con `npx playwright install chromium`. Las pruebas usan Python y el puerto 18765; los datos ficticios se crean en una base temporal y las capturas quedan en `test-results/` (excluido de Git).

Los componentes shadcn/ui usados están en `src/components/ui/`. El mapa usa [Leaflet](https://leafletjs.com/reference.html), la asignación geográfica está en `src/lib/geography.ts` y la API del backend en `src/lib/reports.ts`.
