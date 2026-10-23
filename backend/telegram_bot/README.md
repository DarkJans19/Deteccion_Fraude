# Bot de Telegram

El bot conserva la funcionalidad inicial de recibir mensajes y confirmar su
recepcion. El token nunca se almacena en el repositorio.

```powershell
$env:TELEGRAM_BOT_TOKEN = "TOKEN_NUEVO"
.venv\Scripts\python.exe -m backend.telegram_bot.bot
```

El token que aparecia en el commit original de la rama debe revocarse en
BotFather y reemplazarse por uno nuevo, ya que eliminarlo del archivo actual no
lo elimina del historial de Git.

Todas las dependencias del proyecto se instalan desde `requirements.txt` en la
raiz del repositorio.
