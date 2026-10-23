"""Punto de entrada del bot de Telegram.

El token se obtiene del entorno para evitar publicar credenciales en el codigo.
"""

import os

from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, MessageHandler, filters


async def on_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    del context
    message = update.effective_message
    if message is None:
        return
    text = message.text or "(mensaje sin texto, por ejemplo una foto)"
    await message.reply_text(f"Recibido: {text}")


def main() -> None:
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise SystemExit(
            "Falta TELEGRAM_BOT_TOKEN. Configura un token nuevo en el entorno."
        )
    app = ApplicationBuilder().token(token).build()
    app.add_handler(MessageHandler(filters.ALL, on_message))
    print("Bot corriendo. Ctrl+C para detener.")
    app.run_polling()


if __name__ == "__main__":
    main()
