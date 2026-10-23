# syntax=docker/dockerfile:1
FROM node:22-bookworm-slim AS dashboard
WORKDIR /build
COPY frontend/dashboard/package.json frontend/dashboard/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/dashboard/ ./
RUN npm run build

FROM python:3.12-slim-bookworm AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    HOST=0.0.0.0 \
    PORT=8765
WORKDIR /app
COPY backend/requirements.txt backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt \
    && groupadd --gid 10001 app \
    && useradd --uid 10001 --gid app --no-create-home app
COPY --chown=app:app backend/__init__.py backend/__init__.py
COPY --chown=app:app backend/api/ backend/api/
COPY --chown=app:app backend/reporting/__init__.py backend/reporting/hasher.py backend/reporting/mongo.py backend/reporting/
COPY --chown=app:app backend/reporting/database/ backend/reporting/database/
COPY --from=dashboard --chown=app:app /build/dist/ frontend/dashboard/dist/
USER app
EXPOSE 8765
HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8765/api/health', timeout=8).read()"]
CMD ["python", "-m", "backend.api.main"]
