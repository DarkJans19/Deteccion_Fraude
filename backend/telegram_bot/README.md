# Bot de Telegram

El bot recibe texto, fotos e imágenes enviadas como archivo en chats privados.
Envía texto a `POST /api/analizar` e imágenes a `POST /api/analizar-imagen`,
que ejecuta OCR y lectura de códigos QR. El token y la URL se leen del `.env`
de la raíz; nunca se almacenan en el repositorio.

Para OCR, el motor Tesseract y los idiomas `spa` y `eng` deben instalarse donde
se ejecuta la API. Instalar `pytesseract` con pip no instala el motor, y no hace
falta instalar Tesseract en Telegram: el bot solo remite la imagen a la API.

En Linux/Debian instala las dependencias en el host:

```bash
sudo apt-get update
sudo apt-get install -y tesseract-ocr tesseract-ocr-spa tesseract-ocr-eng
tesseract --list-langs
```

La salida de `tesseract --list-langs` debe incluir `spa` y `eng`. Si el comando
no está en el `PATH`, define `TESSERACT_CMD=/usr/bin/tesseract` en el `.env`.
Configura también `TELEGRAM_BOT_TOKEN` en ese archivo.

Inicia la API desde la raíz del repositorio:

```bash
.venv/bin/python -m uvicorn backend.api.main:app --host 127.0.0.1 --port 8000
```

En otra terminal, inicia el bot:

```bash
.venv/bin/python -m backend.telegram_bot.bot
```

En Windows, activa el entorno virtual y usa estos comandos equivalentes:

```powershell
.venv\Scripts\python.exe -m uvicorn backend.api.main:app --host 127.0.0.1 --port 8000
.venv\Scripts\python.exe -m backend.telegram_bot.bot
```

Variables opcionales:

```env
ANALYSIS_API_URL=http://127.0.0.1:8000/api/analizar
ANALYSIS_API_TIMEOUT=120
TELEGRAM_IMAGE_API_URL=http://127.0.0.1:8000/api/analizar-imagen
IMAGE_MAX_MB=10
```

`TELEGRAM_IMAGE_API_URL` es opcional: por defecto se deriva de
`ANALYSIS_API_URL` reemplazando `/api/analizar` por `/api/analizar-imagen`.
El bot muestra el veredicto y, para imágenes, el texto OCR, los QR detectados
y las advertencias.

En Windows, define en el `.env` la ruta real del ejecutable, por ejemplo:

```env
TESSERACT_CMD="C:/Program Files/Tesseract-OCR/tesseract.exe"
OCR_LANG=spa+eng
```

La imagen Docker de este proyecto ya instala los paquetes de Tesseract y
configura `/usr/bin/tesseract` automáticamente.

Si el bot y la API corren en equipos distintos, configura `ANALYSIS_API_URL`
con una dirección que el proceso del bot pueda alcanzar.

Con Docker Compose, inicia la API y el bot así; Compose configura la dirección
interna entre ambos servicios:

```bash
docker compose --profile telegram up -d --build qr telegram
docker compose logs -f telegram
```

Para detenerlos: `docker compose --profile telegram stop telegram qr`.

El token que aparecía en el commit original de la rama debe revocarse en
BotFather y reemplazarse por uno nuevo, ya que eliminarlo del archivo actual no
lo elimina del historial de Git.

Todas las dependencias del proyecto se instalan desde `requirements.txt` en la
raíz del repositorio.
