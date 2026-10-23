# Detección de fraude

Repositorio integrado del dashboard React y de las funcionalidades de detección
de fraude. La imagen Docker compila el dashboard y sirve el frontend y `/api`
desde el mismo proceso Python. El contenedor final usa un usuario sin
privilegios y no contiene credenciales, datasets ni herramientas de compilación
del frontend.

## Organización del proyecto

Cada funcionalidad del backend vive en un paquete independiente:

```text
backend/
├── api/                 # servidor HTTP principal
├── geography/           # preparación de mapas de Bogotá
├── message_analysis/    # modelo GGUF y datasets de SMS/correos
├── payment_receipts/    # lector QR y análisis de comprobantes
├── reporting/           # hashing, MongoDB y trazabilidad
├── telegram_bot/        # integración con Telegram
└── url_analysis/        # detector de URLs, scripts, datos y pruebas
```

Los datasets de URLs están en `backend/url_analysis/data/raw`; los de mensajes
están en `backend/message_analysis/data`. Los datos procesados y modelos
generados no se versionan.

## Docker con MongoDB Atlas

Necesitas Docker Engine/Desktop en ejecución y Docker Compose 2.30 o posterior. Desde la raíz:

1. Usa el archivo existente `.env` en la raíz del proyecto con las credenciales. El backend y Compose leen ese archivo; `.env.example` no se carga.
2. Configura `MONGODB_DATABASE` en ese archivo; si falta, se utiliza la base indicada en la URI o `fraud_detection`. La URI con credenciales incluidas tiene prioridad sobre `MONGODB_USERNAME` y `MONGODB_PASSWORD`. Con una URI sin credenciales, ambos valores se pasan directamente a PyMongo.
3. En Atlas, permite la IP de salida de la máquina que ejecuta Docker y concede acceso a la base al usuario configurado.

```bash
docker compose up -d --build
docker compose ps
docker compose logs --tail=100 app
```

Abre **http://localhost:8765/dashboard/trazabilidad**. `/api/health` comprueba también la conexión a MongoDB y devuelve 503 si no está disponible. El backend falla al iniciar si no puede conectar o preparar las colecciones e índices.

Compose carga las credenciales con `env_file` en formato `raw` y el backend interpreta las comillas dotenv sin interpolar variables. Se aceptan valores con o sin comillas y los caracteres `$` de las contraseñas se conservan. `.gitignore` y `.dockerignore` excluyen los archivos de secretos.

Para usar el nombre anterior `backend/atlas-credentials.env`:

```bash
MONGODB_ENV_FILE=./backend/atlas-credentials.env docker compose up -d --build
```

Puedes cambiar `APP_PORT` y otras opciones de Compose mediante variables del entorno, por ejemplo `APP_PORT=9000 docker compose up -d --build`. Las credenciales siguen en el `.env` existente en la raíz. El puerto se publica en `127.0.0.1`; los hosts y orígenes permitidos se configuran con `ALLOWED_HOSTS` y `FRONTEND_ORIGINS`. Para un dominio detrás de un proxy, incluye el host con su puerto si corresponde y el origen HTTPS completo. El control de origen/CSRF no reemplaza la autenticación de usuarios si se publica el servicio.

## MongoDB local con volumen persistente

Esta alternativa usa un archivo Compose independiente, sin leer las credenciales de Atlas. Define `LOCAL_MONGO_PASSWORD` en el `.env` de la raíz o en tu entorno; opcionalmente define `LOCAL_MONGO_USERNAME`.

```bash
docker compose -f compose.local.yaml up -d --build
docker compose -f compose.local.yaml ps
docker compose -f compose.local.yaml down
```

MongoDB queda accesible únicamente dentro de la red de Compose y conserva los datos en `mongo_data`, incluso después de `down`. Evita `down -v` si necesitas conservarlos. Esta configuración local usa el usuario de inicialización de MongoDB; Atlas debe usar credenciales propias con acceso a la base de la aplicación. Si cambias la contraseña después de crear el volumen, debes actualizar también el usuario en MongoDB: las variables de inicialización solo se aplican a un volumen nuevo.

El servicio `mongodb` tiene su propio contenedor. Monta `backend/reporting/database/` para crear las colecciones, validadores e índices en un volumen nuevo. El backend y el script de inicialización usan la misma definición de [schema.json](backend/reporting/database/schema.json).

La configuración local usa `mongo:7.0`, comprobado con este entorno. La imagen `mongo:8.0` probada se detenía al detectar el kernel `7.0.12-linuxkit` de Docker Desktop, por la incompatibilidad [SERVER-121912](https://jira.mongodb.org/browse/SERVER-121912). Esta elección corresponde al servicio local; la configuración principal continúa conectándose a Atlas mediante `.env`.

## Documento del reporte

Los nuevos documentos de `fraud_reports` contienen estos seis campos. `_id` es un `ObjectId` BSON, `date` es un `Date` BSON UTC y las coordenadas GeoJSON tienen el orden **[longitud, latitud]**:

```json
{
  "_id": { "$oid": "672a1b2c3d4e5f6789012345" },
  "description": "Intento de phishing mediante SMS bancario",
  "messageContent": "BANCO: Su cuenta fue bloqueada. Verifique sus datos en https://ejemplo.invalid/verificar",
  "date": { "$date": "2026-10-22T15:30:00.000Z" },
  "hashCode": "239f798c7f5c19ef56aaa350fcfb8fdecf86dc7c92a8805b3457d234159dfd3e",
  "location": { "type": "Point", "coordinates": [-74.0721, 4.7110] }
}
```

`$oid` y `$date` son la representación Extended JSON de los tipos BSON; no se almacenan como subdocumentos. La API devuelve esa representación. El dashboard la transforma para mostrar descripciones, fechas, hashes y ubicaciones, sin agregar campos al documento persistido. No se inventan análisis ni precisión GPS para los reportes de MongoDB.

`description` es el resumen, de 1 a 2.000 caracteres. `messageContent` es obligatorio para los nuevos reportes y guarda el mensaje completo, de 1 a 50.000 caracteres. Se preservan exactamente Unicode, espacios y saltos de línea; no se sustituye por el resumen ni se recorta el texto antes de persistirlo.

El servidor genera `_id` y `hashCode`; si omites `date`, registra la hora actual. El hash SHA-256 cubre **`description`, `messageContent`, `date` y `location`**. La fecha se normaliza a UTC con milisegundos y las coordenadas a flotantes. Se usa JSON UTF-8, claves ordenadas, sin espacios y sin escapar Unicode. `_id` y `hashCode` no forman parte del cálculo. La misma descripción, mensaje completo, fecha al milisegundo y ubicación se considera un duplicado y devuelve 409.

El patrón `^[a-fA-F0-9]{64}$` solo comprueba que un hash tenga el formato adecuado. No calcula un SHA-256 ni comprueba que corresponda al reporte. La API también verifica esa correspondencia: un valor de ejemplo de 64 caracteres puede ser rechazado aunque pase el validador de MongoDB. Si envías `hashCode`, debe coincidir con el calculado; se aceptan mayúsculas y minúsculas y la API genera valores en minúsculas. Lo recomendable es omitirlo al registrar por API.

Al iniciar se crean las colecciones nuevas y se actualizan mediante `collMod` los validadores anteriores que difieran del esquema. También se preparan un índice único sobre `hashCode`, otro por fecha, uno geográfico `2dsphere` y el índice del historial. Para preparar únicamente la base configurada en `.env`:

```bash
.venv/bin/python -m backend.api.main --init-db
```

Actualizar un validador existente requiere permisos `collMod` sobre la colección. Una vez actualizado, no se vuelve a modificar si ya coincide con el esquema. Las reglas usan `validationLevel: "strict"` y `validationAction: "error"`: se rechazan nuevas escrituras sin `messageContent` o con tipos, longitudes o campos inválidos. Actualizar el validador no reescribe ni comprueba retroactivamente los documentos anteriores, según [la documentación de MongoDB](https://www.mongodb.com/docs/manual/core/schema-validation/update-schema-validation/).

Los reportes anteriores de cinco campos se pueden consultar si su hash coincide con el algoritmo previo; la interfaz indica que no contienen el mensaje original. Los hashes arbitrarios o calculados con otra convención siguen necesitando una migración explícita. El mensaje original de esos reportes debe obtenerse de la fuente real: no se puede recuperar del resumen o del hash. Las bases SQLite existentes se conservan.

También puedes insertar directamente con `mongosh`; este ejemplo incluye el hash correcto para ese contenido:

```javascript
db.getSiblingDB("fraud_detection").fraud_reports.insertOne({
  description: "Intento de phishing mediante SMS bancario",
  messageContent: "BANCO: Su cuenta fue bloqueada. Verifique sus datos en https://ejemplo.invalid/verificar",
  date: ISODate("2026-10-22T15:30:00Z"),
  hashCode: "239f798c7f5c19ef56aaa350fcfb8fdecf86dc7c92a8805b3457d234159dfd3e",
  location: { type: "Point", coordinates: [-74.0721, 4.7110] }
});
```

Para calcular hashes de otros documentos usa `backend.reporting.mongo.report_hash` con `description`, `messageContent`, `date` y `location`. El hash detecta cambios del contenido sin recalcularlo; no es una firma ni garantiza protección frente a quien pueda modificar también los hashes o eliminar documentos en MongoDB. El script de preparación es idempotente: crea cada colección una sola vez y usa `collMod` en las ya existentes.

## Registro y seguimiento por API

Las operaciones POST requieren `Content-Type: application/json` y el token obtenido en `/api/session`. Ejemplo desde bash, con Python 3 disponible:

```bash
csrf=$(curl -fsS http://localhost:8765/api/session | python3 -c 'import json,sys; print(json.load(sys.stdin)["csrf"])')
curl -fsS http://localhost:8765/api/reports \
  -H 'Content-Type: application/json' \
  -H "X-CSRF-Token: $csrf" \
  --data '{"description":"Mensaje SMS fraudulento que suplanta una entidad bancaria.","messageContent":"BANCO: Su cuenta fue bloqueada. Verifique sus datos en https://ejemplo.invalid/verificar","date":"2026-10-22T15:30:00Z","location":{"type":"Point","coordinates":[-74.0721,4.7110]}}'
```

La respuesta 201 contiene el documento y su `_id.$oid`. La fecha de entrada acepta ISO 8601 con zona horaria o `{ "$date": "..." }`. `_id` se genera en el servidor. Fechas sin zona horaria, coordenadas inválidas y campos adicionales se rechazan con 400.

| Método | Ruta | Resultado |
| --- | --- | --- |
| GET | `/api/reports` | Lista de reportes verificados, recientes primero |
| POST | `/api/reports` | Registra el documento original |
| GET | `/api/reports/{id}` | Consulta un reporte por ObjectId |
| GET | `/api/reports/{id}/tracking` | Estado e historial del caso |
| POST | `/api/reports/{id}/tracking` | Agrega una nota y un estado |
| GET | `/api/reports/{id}/export` | Descarga el reporte y su seguimiento |

Ejemplo de seguimiento, sustituyendo `{id}` por el ObjectId recibido:

```bash
curl -fsS 'http://localhost:8765/api/reports/{id}/tracking' \
  -H 'Content-Type: application/json' \
  -H "X-CSRF-Token: $csrf" \
  --data '{"status":"en_revision","note":"Se inició la revisión del mensaje y del enlace reportado."}'
```

Cada caso empieza como `registrado`. Se pueden añadir estados `en_revision`, `confirmado`, `descartado` y `cerrado`, con una nota obligatoria de hasta 5.000 caracteres. El estado actual corresponde al último evento por fecha e ID; se puede reabrir un caso añadiendo otro evento `en_revision`. No se impone una secuencia de transiciones.

Los eventos se insertan en `fraud_report_events`, con `reportId`, el hash del reporte, fecha, estado, nota y hash propio. No modifican el reporte original. Cada inserción es atómica y permite actualizaciones simultáneas sin sobrescribir notas. No se registran identidad, IP ni agente del navegador. La API no ofrece modificación ni eliminación de reportes o eventos. Los hashes se verifican al consultar o exportar; una discrepancia devuelve 409.

En el dashboard, selecciona un reporte para abrir su resumen, el mensaje completo y el seguimiento debajo del mapa. Puedes consultar el historial y guardar nuevas notas y estados. El mapa mantiene su aproximación visual de ubicación.

## Desarrollo y pruebas

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
npm --prefix frontend/dashboard ci
npm --prefix frontend/dashboard run build
.venv/bin/python -m backend.api.main
```

`backend.api.main` carga el `.env` de la raíz del proyecto; también acepta `--env-file RUTA` como selección explícita. Las variables del proceso tienen prioridad. Para usar Vite, inicia `npm --prefix frontend/dashboard run dev` en otra terminal; los orígenes locales del puerto 5173 están permitidos por defecto.

Para comprobar la base configurada en el `.env` existente, sin insertar ni modificar datos:

```bash
.venv/bin/python -m backend.api.main --check
```

Esta comprobación verifica conexión, lectura de reportes y hashes de reportes y seguimientos, sin mostrar credenciales ni contenidos.

```bash
.venv/bin/python -m unittest discover -s backend/reporting/tests -v
# Validación real de BSON, índices y esquemas: requiere mongod instalado.
.venv/bin/python backend/reporting/tests/run_mongo_integration.py
```

La primera suite usa mongomock para MongoDB. La segunda inicia un `mongod` temporal, ejecuta toda la suite y lo detiene al terminar. Nunca carga las credenciales de Atlas. También puedes definir `TEST_MONGODB_URI` para una instancia de pruebas propia: se crean y eliminan únicamente bases con nombre aleatorio `test_fraud_*`.

Para navegador, desde `frontend/dashboard`, instala Chromium con `npx playwright install chromium` y ejecuta `npm test`. En Windows, Playwright usa Edge. Las pruebas verifican la vista anterior con SQLite y el contrato del dashboard para documentos MongoDB y seguimiento. Para regenerar la cartografía consulta [el README del dashboard](frontend/dashboard/README.md); las capas preparadas ya se incluyen en la imagen.

Referencias de configuración: [env_file de Compose](https://docs.docker.com/reference/compose-file/services/#env_file), [tipos de fecha de PyMongo](https://www.mongodb.com/docs/languages/python/pymongo-driver/current/data-formats/dates-and-times/) e [índices MongoDB](https://www.mongodb.com/docs/languages/python/pymongo-driver/current/indexes/).

## Análisis de URLs

Instala sus dependencias, prepara los datos y entrena el detector híbrido:

```powershell
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe -m backend.url_analysis.cli.prepare_dataset
.venv\Scripts\python.exe -m backend.url_analysis.cli.train_detector
.venv\Scripts\python.exe -m backend.url_analysis.cli.analyze "https://ejemplo.com"
```

Los datos derivados quedan en `backend/url_analysis/data/processed` y los
modelos en `backend/url_analysis/artifacts/url_detector`. Para habilitar Google
Safe Browsing configura `GOOGLE_SAFE_BROWSING_API_KEY`. La integración usa
`threatMatches.find` v4, que envía la URL completa a Google. Safe Browsing está
destinado a uso no comercial; para productos comerciales se debe evaluar Web
Risk.

## Comprobantes y códigos QR

El lector decodifica el QR en el navegador, analiza la URL y revisa sus
redirecciones sin abrir automáticamente el destino:

```powershell
.venv\Scripts\python.exe -m backend.payment_receipts.server
```

Requiere que exista el modelo entrenado del módulo de URLs. Consulta las
[opciones y límites de seguridad](backend/payment_receipts/README.md).

## Mensajes y Telegram

Los datasets de SMS/correo se regeneran con:

```powershell
.venv\Scripts\python.exe -m backend.message_analysis.prepare_dataset
```

El modelo GGUF se carga desde
`backend/message_analysis/models/qwen_bosa_antifraude.gguf` o desde la ruta
indicada por `MODEL_PATH`.

Para combinar mensajes y enlaces usa `backend.models.model`. Este módulo carga
Qwen desde `backend/models/qwen_bosa_antifraude.gguf` (o `MODEL_PATH`), extrae
URLs por RegEx y ejecuta el detector de URLs existente:

```python
from backend.models.model import get_model

resultado = get_model().predecir("Revisa tu cuenta en https://ejemplo.com/validar")
print(resultado.veredicto)
```

| Riesgo del mensaje | Riesgo de URL | Resultado combinado |
| --- | --- | --- |
| ALTO | ALTO | RIESGO ALTO |
| ALTO | BAJO | RIESGO NO CONCLUYENTE |
| ALTO | NO CONCLUYENTE | RIESGO ALTO |
| BAJO | ALTO | RIESGO ALTO |
| BAJO | BAJO | RIESGO BAJO |
| BAJO | NO CONCLUYENTE | RIESGO NO CONCLUYENTE |

Si la entrada contiene únicamente uno o varios enlaces, se ejecuta solo el
analizador de URLs y su riesgo determina el resultado general, sin ejecutar Qwen.
Sin URLs se conserva el riesgo del mensaje. Con varios enlaces se prioriza
ALTO, seguido de NO CONCLUYENTE y BAJO. El resultado `review` del detector y
los errores al analizar un enlace se consideran NO CONCLUYENTE. El detector
entrenado debe existir en `backend/url_analysis/artifacts/url_detector`; puedes
cambiar esa ruta con `URL_MODEL_DIR` o `FraudModel(url_model_dir=...)`.
`es_fraude` vale `True` para ALTO, `False` para BAJO y `None` para NO CONCLUYENTE.
`respuesta_cruda` conserva la respuesta de Qwen (vacía si solo hay enlaces);
`veredicto` contiene el resultado
combinado. `predecir_async` aplica las mismas reglas.

Las pruebas de esta integración no cargan modelos ni consultan la red:

```bash
.venv/bin/python -m unittest discover -s backend/models/tests -v
```

Para ejecutar el bot, instala sus dependencias y configura un token nuevo:

```powershell
.venv\Scripts\python.exe -m pip install -r requirements.txt
$env:TELEGRAM_BOT_TOKEN = "TOKEN_NUEVO"
.venv\Scripts\python.exe -m backend.telegram_bot.bot
```

El token publicado en el commit original de `telegram-bot` debe revocarse en
BotFather: retirarlo del archivo actual no lo elimina del historial Git.
