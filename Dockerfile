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
    && apt-get install --no-install-recommends --yes libgomp1 \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 app \
    && useradd --uid 10001 --gid app --no-create-home app
COPY --from=python-deps /opt/venv /opt/venv
COPY --chown=app:app backend/ backend/
COPY --from=dashboard --chown=app:app /build/dist/ frontend/dashboard/dist/
USER app

# Analisis de comprobantes/QR y URLs.
FROM runtime AS qr
ENV HOST=0.0.0.0 \
    PORT=8000
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=8).read()"]
CMD ["python", "-m", "backend.payment_receipts.server", "--host", "0.0.0.0", "--port", "8000", "--no-browser"]

# Bot opcional, activado mediante el perfil telegram de Compose.
FROM runtime AS telegram
HEALTHCHECK NONE
CMD ["python", "-m", "backend.telegram_bot.bot"]

# Etapa predeterminada: API principal y dashboard.
FROM runtime AS api
ENV HOST=0.0.0.0 \
    PORT=8765
EXPOSE 8765
HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8765/api/health', timeout=8).read()"]
CMD ["python", "-m", "backend.api.main"]
