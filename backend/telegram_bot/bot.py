"""Bot de Telegram conectado a la API de análisis de fraude.

La API debe estar disponible (por defecto en
``http://127.0.0.1:8000/api/analizar``). Su URL se puede cambiar con
``ANALYSIS_API_URL`` en el entorno o en el ``.env`` de la raíz.
"""

import logging
import os
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx
from dotenv import load_dotenv
from telegram import Update
from telegram.error import TelegramError
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env", override=False, interpolate=False)


ANALYSIS_API_URL = (
    os.getenv("ANALYSIS_API_URL") or "http://127.0.0.1:8000/api/analizar"
).strip()


def _derivar_url_imagen(url: str) -> str:
    """Deriva el endpoint de imagen a partir del endpoint de texto."""
    try:
        parsed = urlsplit(url)
        path = parsed.path.rstrip("/")
        if path.endswith("/analizar"):
            path = path[: -len("/analizar")] + "/analizar-imagen"
        else:
            path = "/api/analizar-imagen"
        return urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))
    except ValueError:
        return "http://127.0.0.1:8000/api/analizar-imagen"


IMAGE_ANALYSIS_API_URL = (
    os.getenv("TELEGRAM_IMAGE_API_URL") or _derivar_url_imagen(ANALYSIS_API_URL)
).strip()
try:
    ANALYSIS_TIMEOUT = float(os.getenv("ANALYSIS_API_TIMEOUT") or "120")
except ValueError:
    ANALYSIS_TIMEOUT = 120.0
try:
    MAX_IMAGE_BYTES = max(1, int(os.getenv("IMAGE_MAX_MB") or "10")) * 1024 * 1024
except ValueError:
    MAX_IMAGE_BYTES = 10 * 1024 * 1024
TELEGRAM_MESSAGE_LIMIT = 4096

RISK_LABELS = {
    "high": "ALTO",
    "review": "REVISAR",
    "low": "BAJO",
}

LOGGER = logging.getLogger(__name__)


def _partir_mensaje(texto: str) -> list[str]:
    """Divide el texto en bloques que Telegram pueda aceptar."""
    if len(texto) <= TELEGRAM_MESSAGE_LIMIT:
        return [texto]

    partes: list[str] = []
    actual = ""
    for linea in texto.splitlines(keepends=True):
        while len(linea) > TELEGRAM_MESSAGE_LIMIT:
            if actual:
                partes.append(actual)
                actual = ""
            partes.append(linea[:TELEGRAM_MESSAGE_LIMIT])
            linea = linea[TELEGRAM_MESSAGE_LIMIT:]
        if len(actual) + len(linea) > TELEGRAM_MESSAGE_LIMIT:
            partes.append(actual)
            actual = ""
        actual += linea
    if actual:
        partes.append(actual)
    return partes


async def _responder(update: Update, texto: str) -> None:
    if update.effective_message is None:
        return
    for parte in _partir_mensaje(texto):
        await update.effective_message.reply_text(parte)


def _formatear_resultado(
    resultado: dict[str, Any], titulo: str = "Resultado del análisis"
) -> str:
    veredicto = str(resultado.get("veredicto") or "NO DISPONIBLE")
    es_fraude = resultado.get("es_fraude")

    if es_fraude is True:
        resumen = "El mensaje presenta señales de fraude."
    elif es_fraude is False:
        resumen = "No se detectaron señales claras de fraude."
    else:
        resumen = "El análisis no es concluyente."

    lineas = [
        titulo,
        "",
        f"Veredicto: {veredicto}",
        resumen,
    ]

    veredicto_mensaje = resultado.get("veredicto_mensaje")
    veredicto_url = resultado.get("veredicto_url")
    if veredicto_mensaje or veredicto_url:
        lineas.extend(
            [
                "",
                f"Mensaje: {veredicto_mensaje or 'No evaluado'}",
                f"Enlaces: {veredicto_url or 'No evaluados'}",
            ]
        )

    analisis_urls = resultado.get("analisis_urls") or []
    if isinstance(analisis_urls, list) and analisis_urls:
        lineas.extend(["", "Enlaces analizados:"])
        for item in analisis_urls:
            if not isinstance(item, dict):
                continue
            url = item.get("url") or item.get("normalized_url") or "(URL sin datos)"
            riesgo_url = RISK_LABELS.get(
                str(item.get("risk_level", "")).lower(), "NO CONCLUYENTE"
            )
            probabilidad = item.get("phishing_probability")
            detalle = f"- {url}: {riesgo_url}"
            if isinstance(probabilidad, (int, float)):
                detalle += f" (probabilidad {probabilidad:.1%})"
            error = item.get("error")
            if error:
                detalle += f" - Error: {error}"
            lineas.append(detalle)

    respuesta_cruda = str(resultado.get("respuesta_cruda") or "").strip()
    if respuesta_cruda:
        lineas.extend(["", "Detalle del modelo:", respuesta_cruda])

    texto_extraido = str(resultado.get("texto_extraido") or "").strip()
    if texto_extraido:
        lineas.extend(["", "Texto detectado en la imagen:", texto_extraido])

    qr_detectados = resultado.get("qr_detectados") or []
    if isinstance(qr_detectados, list) and qr_detectados:
        lineas.extend(["", "Contenido de códigos QR:"])
        lineas.extend(f"- {contenido}" for contenido in qr_detectados if contenido)

    advertencias = resultado.get("advertencias") or []
    if isinstance(advertencias, list) and advertencias:
        lineas.extend(["", "Avisos del procesamiento de imagen:"])
        lineas.extend(f"- {aviso}" for aviso in advertencias if aviso)

    lineas.extend(
        [
            "",
            "No abras enlaces sospechosos ni compartas datos personales.",
        ]
    )
    return "\n".join(lineas)


def _formatear_imagen(resultado: dict[str, Any]) -> str:
    bloques: list[str] = []

    qr_detectados = resultado.get("qr_detectados") or []
    if isinstance(qr_detectados, list) and qr_detectados:
        bloques.append(
            "Códigos QR detectados:\n"
            + "\n".join(f"- {contenido}" for contenido in qr_detectados)
        )

    texto_extraido = str(resultado.get("texto_extraido") or "").strip()
    if texto_extraido:
        bloques.append("Texto detectado por OCR:\n" + texto_extraido)

    advertencias = resultado.get("advertencias") or []
    if isinstance(advertencias, list) and advertencias:
        bloques.append(
            "Advertencias:\n" + "\n".join(f"- {aviso}" for aviso in advertencias)
        )

    bloques.append(
        _formatear_resultado(resultado, "Resultado del análisis de la imagen")
    )
    return "\n\n".join(bloques)


def _detalle_error(respuesta: httpx.Response) -> str:
    try:
        detalle = respuesta.json().get("detail")
    except (ValueError, AttributeError):
        return "sin detalle"
    return str(detalle) if detalle else "sin detalle"


async def iniciar(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    del context
    await _responder(
        update,
        "Hola. Soy tu asistente de prevención de fraude.\n\n"
        "Envíame un mensaje sospechoso, una URL o una imagen con "
        "código QR y lo analizaré.",
    )


async def _enviar_a_api(
    update: Update,
    url: str,
    *,
    payload: dict[str, Any] | None = None,
    files: dict[str, tuple[str, bytes, str]] | None = None,
    data: dict[str, str] | None = None,
    es_imagen: bool = False,
) -> dict[str, Any] | None:
    try:
        async with httpx.AsyncClient(timeout=ANALYSIS_TIMEOUT) as client:
            if files is not None:
                respuesta = await client.post(url, files=files, data=data)
            else:
                respuesta = await client.post(url, json=payload)
            respuesta.raise_for_status()
            resultado = respuesta.json()
    except httpx.TimeoutException:
        LOGGER.warning("La API de análisis agotó el tiempo de espera")
        await _responder(update, "El análisis tardó demasiado. Inténtalo nuevamente.")
        return None
    except httpx.HTTPStatusError as error:
        estado = error.response.status_code
        detalle = _detalle_error(error.response)
        LOGGER.warning("La API de análisis respondió %s: %s", estado, detalle)
        if estado == 413 and es_imagen:
            mensaje = "La imagen supera el tamaño máximo permitido para el análisis."
        elif estado == 415:
            mensaje = "El archivo enviado no es una imagen compatible."
        elif estado == 422 and es_imagen:
            mensaje = (
                "No pude extraer texto ni códigos QR de la imagen. "
                f"{detalle} Si falta Tesseract, instálalo en el servidor de la API "
                "y configura TESSERACT_CMD allí."
            )
        elif estado == 422:
            mensaje = (
                "El mensaje no es válido para el análisis. "
                "Debe tener entre 1 y 50.000 caracteres."
            )
        elif estado == 503:
            mensaje = (
                "El modelo de análisis no está disponible en este momento. "
                f"Detalle: {detalle}"
            )
        else:
            mensaje = (
                "No fue posible analizar el contenido. "
                f"El servicio respondió con el código {estado}."
            )
        await _responder(update, mensaje)
        return None
    except httpx.HTTPError:
        LOGGER.exception("Error de conexión con la API de análisis")
        await _responder(
            update,
            "No fue posible conectar con el servicio de análisis. "
            "Verifica que la API esté en ejecución y que su URL sea accesible desde el bot.",
        )
        return None
    except ValueError:
        LOGGER.exception("La API de análisis devolvió una respuesta no válida")
        await _responder(update, "El servicio de análisis devolvió una respuesta no válida.")
        return None

    if not isinstance(resultado, dict):
        await _responder(update, "El servicio de análisis devolvió una respuesta no válida.")
        return None
    return resultado


async def analizar_mensaje(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    del context
    message = update.effective_message
    if message is None or not message.text:
        return

    texto = message.text
    await _responder(update, "Analizando tu mensaje...")
    resultado = await _enviar_a_api(
        update, ANALYSIS_API_URL, payload={"texto": texto}
    )
    if resultado is None:
        return
    await _responder(update, _formatear_resultado(resultado))


async def analizar_imagen(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if message is None:
        return

    if message.photo:
        imagen = message.photo[-1]
        nombre = "imagen.jpg"
        tipo = "image/jpeg"
    elif message.document is not None:
        imagen = message.document
        nombre = message.document.file_name or "imagen"
        tipo = message.document.mime_type or "application/octet-stream"
    else:
        return

    if imagen.file_size is not None and imagen.file_size > MAX_IMAGE_BYTES:
        limite_mb = MAX_IMAGE_BYTES // (1024 * 1024)
        await _responder(update, f"La imagen supera el límite de {limite_mb} MB.")
        return

    await _responder(update, "Analizando la imagen con OCR y lectura de códigos QR...")
    try:
        archivo = await context.bot.get_file(imagen.file_id)
        contenido = bytes(await archivo.download_as_bytearray())
    except TelegramError:
        LOGGER.exception("No se pudo descargar la imagen desde Telegram")
        await _responder(update, "No pude descargar la imagen. Inténtalo nuevamente.")
        return

    if len(contenido) > MAX_IMAGE_BYTES:
        limite_mb = MAX_IMAGE_BYTES // (1024 * 1024)
        await _responder(update, f"La imagen supera el límite de {limite_mb} MB.")
        return

    campos = (
        {"texto": message.caption.strip()}
        if message.caption and message.caption.strip()
        else None
    )
    resultado = await _enviar_a_api(
        update,
        IMAGE_ANALYSIS_API_URL,
        files={"imagen": (nombre, contenido, tipo)},
        data=campos,
        es_imagen=True,
    )
    if resultado is None:
        return
    await _responder(update, _formatear_imagen(resultado))


def main() -> None:
    logging.basicConfig(
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        level=logging.INFO,
    )
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise SystemExit(
            "Falta TELEGRAM_BOT_TOKEN. Configura un token válido en el entorno."
        )

    LOGGER.info("API de análisis: %s", ANALYSIS_API_URL)
    LOGGER.info("API de análisis de imágenes: %s", IMAGE_ANALYSIS_API_URL)
    bot = Application.builder().token(token).build()
    bot.add_handler(CommandHandler("start", iniciar))
    bot.add_handler(
        MessageHandler(
            filters.ChatType.PRIVATE & filters.TEXT & ~filters.COMMAND,
            analizar_mensaje,
        )
    )
    bot.add_handler(
        MessageHandler(
            filters.ChatType.PRIVATE & (filters.PHOTO | filters.Document.IMAGE),
            analizar_imagen,
        )
    )
    LOGGER.info("Bot de Telegram iniciado.")
    bot.run_polling()


if __name__ == "__main__":
    main()