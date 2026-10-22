from telegram import Update
from telegram.ext import ApplicationBuilder, MessageHandler, filters, ContextTypes

TOKEN = "8758365079:AAGUjrbAdd-eh9zAxb72HR_QKXlA-WfafAc"

async def on_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    texto = update.message.text or "(mensaje sin texto, ej. una foto)"
    await update.message.reply_text(f"Recibido: {texto}")

def main():
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(MessageHandler(filters.ALL, on_message))
    print("Bot corriendo. Ctrl+C para detener.")
    app.run_polling()

if __name__ == "__main__":
    main()
