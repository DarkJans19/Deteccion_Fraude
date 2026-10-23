# Bot de Telegram

El bot recibe mensajes de texto en chats privados y los envía a la API de
análisis (`POST /api/analizar`). El token y la URL de la API se leen del `.env`
de la raíz; nunca se almacenan en el repositorio.

Primero inicia la API del modelo desde la raíz:

```powershell
.venv\Scripts\python.exe -m uvicorn backend.api.main:app --host 127.0.0.1 --port 8000
```

En otra terminal:

```powershell
.venv\Scripts\python.exe -m backend.telegram_bot.bot
```

Variables opcionales:

```env
ANALYSIS_API_URL=http://127.0.0.1:8000/api/analizar
ANALYSIS_API_TIMEOUT=120
```

El token que aparecía en el commit original de la rama debe revocarse en
BotFather y reemplazarse por uno nuevo, ya que eliminarlo del archivo actual no
lo elimina del historial de Git.

Todas las dependencias del proyecto se instalan desde `requirements.txt` en la
raíz del repositorio.
