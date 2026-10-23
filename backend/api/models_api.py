"""API FastAPI para ``backend.models.model``.

Inicio desde la raíz: python -m uvicorn backend.api.main:app --port 8000
"""

from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any, Literal

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, StringConstraints
from starlette.concurrency import run_in_threadpool


PROJECT_ROOT = Path(__file__).resolve().parents[2]
Riesgo = Literal["RIESGO ALTO", "RIESGO BAJO", "RIESGO NO CONCLUYENTE"]


class TextoRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    texto: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=50_000)
    ] = Field(description="Mensaje completo; se conservan todos los enlaces para el análisis.")


class GeneracionRequest(TextoRequest):
    max_tokens: int = Field(default=60, ge=1, le=512, strict=True)


class PrediccionResponse(BaseModel):
    es_fraude: bool | None
    veredicto: Riesgo
    respuesta_cruda: str
    veredicto_mensaje: Riesgo | None
    veredicto_url: Riesgo | None
    analisis_urls: list[dict[str, Any]]


class GeneracionResponse(BaseModel):
    respuesta_cruda: str


class ErrorResponse(BaseModel):
    detail: str


def _get_model():
    # Importación diferida para leer MODEL_PATH y URL_MODEL_DIR después del .env
    # y evitar cargar las librerías de inferencia en el servidor de reportes.
    from backend.models.model import get_model

    return get_model()


@asynccontextmanager
async def lifespan(app: FastAPI):
    load_dotenv(PROJECT_ROOT / ".env", override=False, interpolate=False)
    app.state.model = await run_in_threadpool(_get_model)
    yield


app = FastAPI(
    title="API de detección de fraude",
    description="Análisis de mensajes y enlaces con el modelo local de backend.models.model.",
    version="1.0.0",
    lifespan=lifespan,
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)


@app.exception_handler(FileNotFoundError)
async def modelo_no_disponible(request: Request, error: FileNotFoundError):
    return JSONResponse(
        status_code=503,
        content={"detail": "No se encontró el modelo. Revisa MODEL_PATH y URL_MODEL_DIR."},
    )


@app.exception_handler(RuntimeError)
@app.exception_handler(ValueError)
async def error_inferencia(request: Request, error: RuntimeError):
    return JSONResponse(
        status_code=503,
        content={"detail": "El modelo no pudo completar la operación."},
    )


@app.post(
    "/api/analizar",
    response_model=PrediccionResponse,
    responses={503: {"model": ErrorResponse}},
    tags=["Análisis"],
)
async def analizar(payload: TextoRequest, request: Request):
    """Combina el veredicto del mensaje y sus URLs mediante predecir_async."""
    return await request.app.state.model.predecir_async(payload.texto)


@app.post(
    "/api/respuesta",
    response_model=GeneracionResponse,
    responses={503: {"model": ErrorResponse}},
    tags=["Modelo"],
)
async def enviar_respuesta(payload: GeneracionRequest, request: Request):
    """Devuelve la respuesta de Qwen con generar, sin el análisis de enlaces."""
    respuesta = await run_in_threadpool(
        request.app.state.model.generar, payload.texto, max_tokens=payload.max_tokens
    )
    return {"respuesta_cruda": respuesta}
