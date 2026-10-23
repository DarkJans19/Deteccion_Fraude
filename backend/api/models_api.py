"""API FastAPI para ``backend.models.model``.

Inicio desde la raíz: python -m uvicorn backend.api.main:app --port 8000
"""

from contextlib import asynccontextmanager
from dataclasses import asdict
import os
import threading
from pathlib import Path
from typing import Annotated, Any, Literal

from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, StringConstraints
from starlette.concurrency import run_in_threadpool

from backend.api.media_analysis import (
    ImageAnalysisError,
    compose_analysis_text,
    extract_image_content,
    max_image_bytes,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
QR_STATIC_DIR = PROJECT_ROOT / "backend" / "qr_analysis" / "static"
Riesgo = Literal["RIESGO ALTO", "RIESGO BAJO", "RIESGO NO CONCLUYENTE"]


class TextoRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    texto: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=50_000)
    ] = Field(description="Mensaje completo; se conservan todos los enlaces para el análisis.")


class GeneracionRequest(TextoRequest):
    max_tokens: int = Field(default=60, ge=1, le=512, strict=True)


class QRAnalisisRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2048)
    ] = Field(description="URL HTTP(S) leída del código QR.")


class PrediccionResponse(BaseModel):
    es_fraude: bool | None
    veredicto: Riesgo
    respuesta_cruda: str
    veredicto_mensaje: Riesgo | None
    veredicto_url: Riesgo | None
    analisis_urls: list[dict[str, Any]]


class ImagenResponse(PrediccionResponse):
    texto_extraido: str
    qr_detectados: list[str]
    advertencias: list[str]


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
    app.state.qr_analysis_lock = threading.Lock()
    app.state.check_qr_redirects = os.getenv(
        "QR_CHECK_REDIRECTS", "true"
    ).strip().casefold() not in {"0", "false", "no", "off"}
    yield


app = FastAPI(
    title="API de detección de fraude",
    description=(
        "Análisis de mensajes, imágenes y URLs QR con los modelos locales del backend."
    ),
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
    "/api/analizar-imagen",
    response_model=ImagenResponse,
    responses={
        413: {"model": ErrorResponse},
        415: {"model": ErrorResponse},
        422: {"model": ErrorResponse},
        503: {"model": ErrorResponse},
    },
    tags=["Análisis"],
)
async def analizar_imagen(
    request: Request,
    imagen: Annotated[UploadFile, File(description="Captura, foto o código QR.")],
    texto: Annotated[
        str | None,
        Form(description="Texto opcional, por ejemplo el pie de foto."),
    ] = None,
):
    """Extrae texto y QR de una imagen y aplica el mismo análisis de fraude."""
    content_type = (imagen.content_type or "").lower()
    if content_type and not content_type.startswith("image/"):
        await imagen.close()
        raise HTTPException(
            status_code=415,
            detail="El archivo debe ser una imagen (image/*).",
        )

    limite = max_image_bytes()
    data = await imagen.read(limite + 1)
    await imagen.close()
    if len(data) > limite:
        raise HTTPException(
            status_code=413,
            detail="La imagen supera el tamaño máximo permitido.",
        )

    try:
        extraido = await run_in_threadpool(extract_image_content, data)
    except ImageAnalysisError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error

    texto_analisis = compose_analysis_text(extraido, texto)
    if not texto_analisis:
        raise HTTPException(
            status_code=422,
            detail=(
                "No se pudo extraer texto ni códigos QR de la imagen. "
                "Revisa que la captura tenga texto legible o que el OCR esté instalado."
            ),
        )

    prediccion = await request.app.state.model.predecir_async(texto_analisis)
    return {
        **asdict(prediccion),
        "texto_extraido": extraido.text,
        "qr_detectados": extraido.qr_contents,
        "advertencias": extraido.warnings,
    }


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


@app.middleware("http")
async def limites_y_cabeceras(request: Request, call_next):
    """Limita cargas conocidas y conserva las cabeceras del lector QR."""
    route_limits = {
        "/api/analyze": (8_192, "La solicitud supera el límite de 8 KB."),
        "/api/analizar-imagen": (
            max_image_bytes() + 64 * 1024,
            "La carga supera el límite configurado para imágenes.",
        ),
    }
    limit = route_limits.get(request.url.path) if request.method == "POST" else None
    if limit:
        content_length = request.headers.get("content-length")
        if content_length:
            try:
                request_bytes = int(content_length)
            except ValueError:
                response = JSONResponse(
                    status_code=400,
                    content={"detail": "Content-Length inválido."},
                )
                return _add_security_headers(response)
            if request_bytes > limit[0]:
                response = JSONResponse(
                    status_code=413,
                    content={"detail": limit[1]},
                )
                return _add_security_headers(response)
    return _add_security_headers(await call_next(request))


def _add_security_headers(response):
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Permissions-Policy"] = "camera=(self)"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self'; style-src 'self'; "
        "img-src 'self' blob: data:; media-src 'self' blob:; "
        "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'"
    )
    response.headers["Cache-Control"] = "no-store"
    return response


@app.post(
    "/api/analyze",
    responses={
        413: {"model": ErrorResponse},
        422: {"model": ErrorResponse},
        503: {"model": ErrorResponse},
    },
    tags=["Análisis QR"],
)
async def analizar_qr(payload: QRAnalisisRequest, request: Request) -> dict[str, Any]:
    """Analiza la URL de un QR con las mismas capas del lector existente."""
    from backend.qr_analysis.service import analyze_qr_url

    modelo = request.app.state.model

    def analizar_en_hilo():
        # El detector TensorFlow y las consultas de red se ejecutan fuera del
        # event loop; como en el servidor anterior, se serializan los QR.
        with request.app.state.qr_analysis_lock:
            return analyze_qr_url(
                payload.url,
                modelo.url_model_dir,
                safe_browsing_client=modelo.safe_browsing_client,
                check_redirects=request.app.state.check_qr_redirects,
            )

    try:
        return await run_in_threadpool(analizar_en_hilo)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except (OSError, RuntimeError) as error:
        raise HTTPException(
            status_code=503,
            detail="No fue posible completar el análisis de la URL del QR.",
        ) from error


@app.get("/api/health", tags=["Operación"])
async def health(request: Request) -> dict[str, Any]:
    """Indica si el servicio y los artefactos del detector de URLs están disponibles."""
    modelo = request.app.state.model
    available = modelo.url_model_dir.is_dir()
    return {
        "status": "ok" if available else "degraded",
        "model_available": available,
        "safe_browsing_configured": modelo.safe_browsing_client is not None,
        "redirect_checks_enabled": request.app.state.check_qr_redirects,
    }


# Se registra al final para que las rutas /api definidas arriba tengan prioridad.
app.mount("/", StaticFiles(directory=str(QR_STATIC_DIR), html=True), name="qr-ui")
