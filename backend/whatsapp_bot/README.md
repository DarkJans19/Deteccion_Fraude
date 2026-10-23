# Bot de WhatsApp

Bot basado en [`whatsapp-web.js`](https://github.com/pedroslopez/whatsapp-web.js)
que recibe mensajes, imágenes y documentos de texto y los envía a la API de
análisis de fraude. Es el equivalente del bot de Telegram, pero la sesión se
vincula escaneando un código QR como un dispositivo más de WhatsApp.

## Requisitos

- Node.js 20 o superior.
- La API del modelo en ejecución (por defecto `http://127.0.0.1:8000`).
- Tesseract con los idiomas `spa` y `eng` para leer texto de imágenes.
- OpenCV y Pillow, instalados desde `requirements.txt`, para decodificar QR.

En Windows, instala Tesseract desde el instalador oficial y define
`TESSERACT_CMD` si no quedó en el `PATH`:

```env
TESSERACT_CMD=C:\Program Files\Tesseract-OCR\tesseract.exe
```

Las dependencias Python nuevas se instalan desde la raíz:

```powershell
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

## Puesta en marcha

Terminal 1, API del modelo desde la raíz:

```powershell
.venv\Scripts\python.exe -m uvicorn backend.api.main:app --host 127.0.0.1 --port 8000
```

Terminal 2, bot:

```powershell
cd backend\whatsapp_bot
npm install
npm start
```

La primera vez aparece un QR en la terminal. Escanéalo desde WhatsApp en
**Dispositivos vinculados > Vincular un dispositivo**. La sesión se guarda en
`backend/whatsapp_bot/.wwebjs_auth` y no se versiona.

Si prefieres no escanear un QR, define el número de la cuenta que actuará como
bot y el bot imprimirá un código de vinculación de 8 caracteres:

```env
WHATSAPP_PAIRING_PHONE=573001234567
```

En WhatsApp abre **Dispositivos vinculados > Vincular con número de teléfono**
e ingresa el código que aparece en la terminal. Sigue siendo la misma
automatización no oficial de WhatsApp Web; solo cambia la forma de vincular.

## Variables de entorno

Se leen del `.env` de la raíz. Las principales:

```env
# Endpoint de texto; el de imágenes se deriva automáticamente.
ANALYSIS_API_URL=http://127.0.0.1:8000/api/analizar
# WHATSAPP_ANALYSIS_API_URL=http://127.0.0.1:8000/api/analizar
# WHATSAPP_IMAGE_API_URL=http://127.0.0.1:8000/api/analizar-imagen
# WHATSAPP_SESSION_NAME=fraude-bot
# WHATSAPP_PAIRING_PHONE=573001234567
# WHATSAPP_GROUPS_ENABLED=false
# WHATSAPP_GROUP_TRIGGER=!analizar
# WHATSAPP_ALLOWED_CHATS=573001234567,573007654321
# WHATSAPP_MAX_MEDIA_MB=10
# WHATSAPP_MAX_TEXT_CHARS=50000
# WHATSAPP_COOLDOWN_SECONDS=3
# WHATSAPP_HEADLESS=true
# PUPPETEER_EXECUTABLE_PATH=
```

- `WHATSAPP_GROUPS_ENABLED=false` limita el bot a chats privados. En grupos
  responde solo si lo mencionan con `@` o si el mensaje empieza por
  `WHATSAPP_GROUP_TRIGGER`.
- `WHATSAPP_ALLOWED_CHATS` es una lista blanca opcional. Si está vacía, el bot
  atiende a cualquier chat privado. Úsala para evitar respuestas a desconocidos.
- `WHATSAPP_COOLDOWN_SECONDS` evita que un mismo remitente sature el modelo.
- `PUPPETEER_EXECUTABLE_PATH` permite usar un Chrome/Chromium ya instalado.

## Qué analiza

| Entrada | Comportamiento |
| --- | --- |
| Texto | `POST /api/analizar` |
| Imagen o sticker | `POST /api/analizar-imagen` con OCR y lectura de QR |
| Imagen con pie de foto | El pie de foto se envía como contexto adicional |
| Documento de texto | Se lee como UTF-8 y se analiza como texto |
| Audio, video o PDF | Todavía no se procesan |
| QR dentro de una imagen | Se decodifica y su contenido se analiza como URL o texto |

El bot responde con el veredicto combinado, el riesgo del mensaje, el riesgo de
cada enlace y, para imágenes, el texto reconocido, los QR detectados y los
avisos de OCR.

## Docker

El servicio usa el perfil `whatsapp` y guarda la sesión en un volumen:

```bash
docker compose --profile whatsapp up -d --build
docker compose logs -f whatsapp
```

La API del modelo no forma parte de ese perfil. Apunta el bot a una dirección
alcanzable desde el contenedor:

```env
WHATSAPP_ANALYSIS_API_URL=http://host.docker.internal:8000/api/analizar
```

En Linux, el `extra_hosts` de Compose ya resuelve `host.docker.internal`.

## Advertencias

- `whatsapp-web.js` automatiza WhatsApp Web de forma no oficial. Su uso puede
  incumplir los términos de WhatsApp y provocar bloqueos de la cuenta. Usa un
  número secundario y no lo despliegues a gran escala sin evaluar la API
  oficial de WhatsApp Cloud.
- La carpeta `.wwebjs_auth` contiene credenciales de la sesión. No la subas al
  repositorio ni la compartas.
- El bot envía el contenido de los mensajes a la API local. No los almacena,
  pero revisa la política de privacidad si lo usas con terceros.
- Para analizar audios haría falta un servicio de transcripción (por ejemplo
  Whisper). Para PDFs haría falta extraer su texto con una librería adicional.
