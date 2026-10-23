"""Bot de Telegram conectado a la API de análisis de fraude.

La API debe estar disponible (por defecto en
``http://127.0.0.1:8000/api/analizar``). Su URL se puede cambiar con
``ANALYSIS_API_URL`` en el entorno o en el ``.env`` de la raíz.
"""

import logging
import os
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv
from telegram import Update
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
try:
    ANALYSIS_TIMEOUT = float(os.getenv("ANALYSIS_API_TIMEOUT") or "120")
except ValueError:
    ANALYSIS_TIMEOUT = 120.0
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


def _formatear_resultado(resultado: dict[str, Any]) -> str:
    veredicto = str(resultado.get("veredicto") or "NO DISPONIBLE")
    es_fraude = resultado.get("es_fraude")

    if es_fraude is True:
        resumen = "El mensaje presenta señales de fraude."
    elif es_fraude is False:
        resumen = "No se detectaron señales claras de fraude."
    else:
        resumen = "El análisis no es concluyente."

    lineas = [
        "Resultado del análisis",
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

    lineas.extend(
        [
            "",
            "No abras enlaces sospechosos ni compartas datos personales.",
        ]
    )
    return "\n".join(lineas)


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
        "Envíame un mensaje sospechoso o una URL y lo analizaré.",
    )


async def analizar_mensaje(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    del context
    message = update.effective_message
    if message is None or not message.text:
        return

    texto = message.text
    await _responder(update, "Analizando tu mensaje...")

    try:
        async with httpx.AsyncClient(timeout=ANALYSIS_TIMEOUT) as client:
            respuesta = await client.post(
                ANALYSIS_API_URL,
                json={"texto": texto},
            )
            respuesta.raise_for_status()
            resultado = respuesta.json()
    except httpx.TimeoutException:
        LOGGER.warning("La API de análisis agotó el tiempo de espera")
        await _responder(
            update,
            "El análisis tardó demasiado. Inténtalo nuevamente.",
        )
        return
    except httpx.HTTPStatusError as error:
        estado = error.response.status_code
        detalle = _detalle_error(error.response)
        LOGGER.warning("La API de análisis respondió %s: %s", estado, detalle)
        if estado == 422:
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
                "No fue posible analizar el mensaje. "
                f"El servicio respondió con el código {estado}."
            )
        await _responder(update, mensaje)
        return
    except httpx.HTTPError:
        LOGGER.exception("Error de conexión con la API de análisis")
        await _responder(
            update,
            "No fue posible conectar con el servicio de análisis. "
            "Verifica que la API esté en ejecución.",
        )
        return
    except ValueError:
        LOGGER.exception("La API de análisis devolvió una respuesta no válida")
        await _responder(
            update,
            "El servicio de análisis devolvió una respuesta no válida.",
        )
        return

    if not isinstance(resultado, dict):
        await _responder(
            update,
            "El servicio de análisis devolvió una respuesta no válida.",
        )
        return

    await _responder(update, _formatear_resultado(resultado))


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
    bot = Application.builder().token(token).build()
    bot.add_handler(CommandHandler("start", iniciar))
    bot.add_handler(
        MessageHandler(
            filters.ChatType.PRIVATE & filters.TEXT & ~filters.COMMAND,
            analizar_mensaje,
        )
    )
    LOGGER.info("Bot de Telegram iniciado.")
    bot.run_polling()


if __name__ == "__main__":
    main()
