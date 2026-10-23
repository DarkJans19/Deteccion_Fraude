# syntax=docker/dockerfile:1

# Compila el dashboard; Node.js no forma parte de las imagenes finales.
FROM node:22-bookworm-slim AS dashboard
WORKDIR /build
COPY frontend/dashboard/package.json frontend/dashboard/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/dashboard/ ./
RUN npm run build

# Instala las dependencias Python en un entorno virtual reutilizable.
# llama-cpp-python puede necesitar compilarse y requiere un compilador C/C++.
FROM python:3.12-slim-bookworm AS python-deps
ENV VIRTUAL_ENV=/opt/venv \
    PATH=/opt/venv/bin:$PATH \
    CC=gcc \
    CXX=g++
WORKDIR /build
RUN apt-get -o Acquire::ForceIPv4=true update \
    && apt-get install --no-install-recommends --yes build-essential \
    && rm -rf /var/lib/apt/lists/*
RUN python -m venv "$VIRTUAL_ENV"
COPY requirements.txt ./requirements.txt
RUN pip install --no-cache-dir --upgrade pip setuptools wheel \
    && pip install --no-cache-dir -r requirements.txt

# Base comun para los procesos del backend modular.
FROM python:3.12-slim-bookworm AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    VIRTUAL_ENV=/opt/venv \
    PATH=/opt/venv/bin:$PATH
WORKDIR /app
RUN apt-get -o Acquire::ForceIPv4=true update \
    && apt-get install --no-install-recommends --yes \
        libgomp1 libglib2.0-0 tesseract-ocr tesseract-ocr-spa tesseract-ocr-eng \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 app \
    && useradd --uid 10001 --gid app --no-create-home app
COPY --from=python-deps /opt/venv /opt/venv
COPY --chown=app:app backend/ backend/
COPY --from=dashboard --chown=app:app /build/dist/ frontend/dashboard/dist/
USER app

# Runtime compartido por los endpoints que usan el modelo y OCR.
FROM runtime AS model-api-runtime
USER root
RUN apt-get -o Acquire::ForceIPv4=true update \
    && apt-get install --no-install-recommends --yes \
        tesseract-ocr tesseract-ocr-eng tesseract-ocr-spa \
    && rm -rf /var/lib/apt/lists/*
USER app

# API FastAPI, interfaz QR y análisis de URLs.
FROM model-api-runtime AS qr
ENV HOST=0.0.0.0 \
    PORT=8000
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=8).read()"]
CMD ["python", "-m", "uvicorn", "backend.api.main:app", "--host", "0.0.0.0", "--port", "8000"]

# Bot opcional, activado mediante el perfil telegram de Compose.
FROM runtime AS telegram
HEALTHCHECK NONE
CMD ["python", "-m", "backend.telegram_bot.bot"]

# Bot de WhatsApp basado en Node.js y Chromium.
FROM node:22-bookworm-slim AS whatsapp
ENV NODE_ENV=production \
    PUPPETEER_SKIP_DOWNLOAD=true \
    PUPPETEER_EXECUTABLE_PATH=/usr/bin/chromium \
    WHATSAPP_HEADLESS=true
WORKDIR /app
RUN apt-get -o Acquire::ForceIPv4=true update \
    && apt-get install --no-install-recommends --yes \
        chromium ca-certificates fonts-liberation fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*
COPY backend/whatsapp_bot/package.json backend/whatsapp_bot/package-lock.json ./
RUN npm ci --omit=dev --no-audit --no-fund
COPY --chown=node:node backend/whatsapp_bot/ ./
RUN mkdir -p /app/.wwebjs_auth /app/.wwebjs_cache \
    && chown -R node:node /app/.wwebjs_auth /app/.wwebjs_cache
USER node
CMD ["node", "index.js"]

# Etapa predeterminada: API principal y dashboard.
FROM model-api-runtime AS api
ENV HOST=0.0.0.0 \
    PORT=8765
EXPOSE 8765
HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8765/api/health', timeout=8).read()"]
CMD ["python", "-m", "backend.api.main"]
