# Detección de fraude

Reportes locales con **un único registro y un único hash por reporte**, calculado al guardarlo. El dashboard permite crear, buscar, consultar y exportar. No hay historial de eventos, seguimientos, cambios de estado ni comprobantes de integridad.

## Archivos

| Archivo | Función |
| --- | --- |
| `fraud_reports/ledger.py` | Valida y guarda cada reporte en una fila de SQLite. Calcula su SHA-256 y comprueba ese mismo hash al consultar. No depende del LLM ni de la vista. |
| `fraud_reports/server.py` | Adaptador HTTP y arranque del dashboard. Puede recibir un detector externo que analiza antes de guardar. |
| `fraud_reports/static/app.js` | Formularios, búsqueda, filtros y presentación de los reportes usando la API. |
| `fraud_reports/static/index.html` | Estructura de la vista. |
| `fraud_reports/static/style.css` | Apariencia y adaptación a pantallas pequeñas. |
| `tests/test_reports.py` | Pruebas con bases temporales y un detector de prueba. |
| `scripts/unify_datasets.py` | Preparación independiente de los datasets para entrenamiento, prueba y validación. |

Se mantienen solo dos módulos Python de aplicación, sin `__init__.py`, `__main__.py` ni un script adicional de detector.

## Ejecutar

Desde la raíz del proyecto, con Python 3.10 o superior y sin instalar dependencias:

```powershell
.\.venv\Scripts\python.exe -m fraud_reports.server
```

También funciona la ejecución directa del archivo:

```powershell
.\.venv\Scripts\python.exe .\fraud_reports\server.py
```

Ejecuta una de las dos opciones y abre **http://127.0.0.1:8765**. Mantén la terminal abierta y detén el servidor con `Ctrl+C`. Si no tienes el entorno virtual, usa `python` en lugar de `.\.venv\Scripts\python.exe`.

La base predeterminada es `runtime/reports.sqlite3`, excluida de Git. Puedes elegir otra con `--db` y otro puerto con `--port`. El proyecto está dentro de OneDrive; usa una ruta fuera de su sincronización si las evidencias deben permanecer únicamente en almacenamiento local.

Las bases del formato anterior de eventos se conservan. Si se selecciona una con `--db`, el servidor solicita elegir otro archivo y no la transforma ni sobrescribe. El dashboard nuevo no muestra esos registros antiguos. Por ejemplo, para las pruebas actuales usa `runtime/pruebas-registro.sqlite3` en lugar de la antigua `runtime/pruebas-dashboard.sqlite3`.

## Qué se guarda

Cada reporte es un único objeto con estos campos:

| Campo | Contenido |
| --- | --- |
| `id`, `schema_version` | UUID del reporte y versión 2 del formato. |
| `reported_by` | Usuario que reporta, obligatorio. |
| `created_at` | Fecha y hora UTC asignadas por el servidor al registrar. La vista las muestra en la zona horaria del navegador. |
| `title`, `text`, `source`, `source_ref` | Título, contenido original, origen SMS/correo/otro y referencia opcional. |
| `analysis` | Resultado disponible al crear el reporte, o `null` si no había detector conectado. |
| `fraud_positive` | `true` para positivo, `false` para negativo o `null` si no hubo análisis o el resultado fue incierto. |
| `hash` | SHA-256 del objeto completo, excluyendo este mismo campo. |

El hash protege juntos al usuario, fecha, contenido y resultado. No se calcula un segundo hash de contenido ni se encadena con otros reportes. Se serializa JSON en UTF-8, con claves ordenadas, separadores sin espacios, Unicode sin escapes y sin NaN/Infinity.

SQLite tiene una sola tabla `reports`: `id`, `report_json` y `hash`. Los triggers bloquean UPDATE, DELETE y el reemplazo de un ID existente. Las consultas y exportaciones comprueban el hash sin guardar nada adicional. No existe una operación para agregar un análisis después de la creación.

El hash detecta cambios que no coinciden con el valor guardado, pero no impide que un administrador del archivo reescriba tanto el contenido como el hash, ni detecta la eliminación completa de un registro. No cifra la información.

## Integración con otros servicios

El flujo es: datos del usuario → resultado del LLM, si está disponible → registro completo → SHA-256 → una inserción en SQLite.

```python
from fraud_reports.ledger import Ledger

reports = Ledger("runtime/reports.sqlite3")
report = reports.create_report(
    title="Mensaje recibido",
    text="Contenido original del mensaje",
    source="sms",
    source_ref="mensaje-123",
    reported_by="usuario-123",
    analysis=None,  # Sustituir por el resultado del LLM antes de guardar.
)
print(report["id"], report["hash"])
registered = reports.get_report(report["id"])
all_reports = reports.snapshot()
exported = reports.export(report["id"])
```

El resultado `analysis` del servicio LLM debe contener:

| Campo | Contrato |
| --- | --- |
| `label` | `fraude`, `no_fraude` o `incierto`. |
| `score` | Número finito entre 0 y 1, o `None`. No implica probabilidad calibrada. |
| `explanation` | Explicación no vacía, hasta 10.000 caracteres. |
| `model_name`, `model_version` | Nombre y versión del modelo. |
| `model_digest` | SHA-256 de su artefacto, en hexadecimal minúsculo. |
| `prompt_version` | Versión de la plantilla utilizada. |

Para conectarlo al adaptador HTTP, pasa a `ReportServer(("127.0.0.1", 8765), reports, detector=detector)` un objeto con `analyze(text) -> dict`, y ejecuta `serve_forever()`. El servidor valida la entrada, llama al modelo y guarda el reporte completo una sola vez. Si el modelo falla, no devuelve resultado o entrega datos inválidos, no se guarda ningún reporte.

El LLM todavía no está implementado. Sin detector, el reporte se guarda con «Sin análisis al registrar», nunca como negativo por defecto. Ese registro conserva esa condición; no se completa ni se modifica posteriormente. El contenido recibido por el LLM debe tratarse como datos, sin herramientas de ejecución.

## API local

| Método | Ruta | Uso |
| --- | --- | --- |
| GET | `/api/session` | Token CSRF y disponibilidad del detector. |
| POST | `/api/reports` | Registrar `title`, `text`, `source`, `reported_by` y `source_ref` opcional; devuelve `id` y `hash`. |
| GET | `/api/reports` | Consultar los reportes. |
| GET | `/api/reports/{id}` | Consultar un reporte. |
| GET | `/api/reports/{id}/export` | Exportar ese mismo reporte en JSON. |

Los POST requieren `Content-Type: application/json` y `X-CSRF-Token` obtenido de `/api/session`. El cliente HTTP no puede enviar fechas ni resultados: el servidor asigna la fecha y toma el resultado del detector configurado. Otros servicios Python pueden suministrar el análisis al crear mediante `Ledger`.

La CLI escucha solo en `127.0.0.1`. El token y la validación de origen no son autenticación: cualquier proceso con acceso local al puerto puede consultar y registrar datos. El usuario del formulario es declarado; cuando se integre autenticación deberá obtenerse de la sesión. Este servidor de desarrollo está pensado para uso local y volúmenes pequeños, sin roles, paginación ni cifrado en reposo.

## Probar

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
node --check fraud_reports/static/app.js
```

Las pruebas automáticas usan bases y puertos temporales. Comprueban que crear guarda una sola fila, consultar y exportar no agregan registros, el hash cubre todos los campos, los registros no se editan y el modelo se ejecuta antes de guardar. La comprobación con Node es opcional si no lo tienes instalado.

Para probar el dashboard en una base aparte:

```powershell
.\.venv\Scripts\python.exe -m fraud_reports.server --db runtime/pruebas-registro.sqlite3 --port 8766
```

Abre `http://127.0.0.1:8766`, no el HTML directamente. Crea un reporte ficticio con usuario, título, origen y contenido. Comprueba su fecha y hora, resultado disponible y hash. Búscalo por usuario, filtra por resultado y exporta el JSON. Actualizar o consultar varias veces debe conservar el mismo hash y la misma cantidad de reportes. Detén y reinicia con el mismo comando para comprobar la persistencia.

Sin LLM, filtra por «Sin análisis». Los resultados positivo, negativo e incierto se validan con un detector de prueba dentro de las pruebas automáticas; eso no mide la precisión de un modelo real.

## Datasets

```powershell
python scripts/unify_datasets.py
```

Lee `data_original/`, unifica `text`, `url` y `label`, y genera `dataset/train.csv`, `test.csv` y `validation.csv`. No carga esos datos como reportes ni entrena un modelo.
