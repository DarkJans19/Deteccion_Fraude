"""Extracción limitada de texto desde imágenes con Tesseract OCR."""

from __future__ import annotations

import io
import os

import pytesseract
from PIL import Image, ImageOps, UnidentifiedImageError


MAX_IMAGE_BYTES = 10 * 1024 * 1024
MAX_IMAGE_PIXELS = 20_000_000
OCR_TIMEOUT_SECONDS = 15
OCR_LANGUAGES = "spa+eng"
SUPPORTED_IMAGE_FORMATS = {"BMP", "GIF", "JPEG", "PNG", "TIFF", "WEBP"}


class InvalidImageError(Exception):
    """La entrada no se puede decodificar como imagen."""


class UnsupportedImageFormatError(Exception):
    """El formato de imagen no está admitido."""


class ImageTooLargeError(Exception):
    """El archivo o las dimensiones superan los límites de OCR."""


class OCRUnavailableError(Exception):
    """Falta el ejecutable de Tesseract o su idioma configurado."""


class OCRTimeoutError(Exception):
    """Tesseract excedió el tiempo máximo de análisis."""


class OCRProcessingError(Exception):
    """Tesseract encontró un error procesando la imagen."""


def extraer_texto_imagen(contenido: bytes) -> str:
    """Reconoce texto impreso en una imagen y devuelve el texto normalizado."""
    if not contenido:
        raise InvalidImageError("La imagen está vacía.")
    if len(contenido) > MAX_IMAGE_BYTES:
        raise ImageTooLargeError("La imagen supera el límite de 10 MB.")

    try:
        with Image.open(io.BytesIO(contenido)) as original:
            if original.format not in SUPPORTED_IMAGE_FORMATS:
                raise UnsupportedImageFormatError(
                    "Usa una imagen PNG, JPEG, WEBP, TIFF, BMP o GIF."
                )
            if original.width * original.height > MAX_IMAGE_PIXELS:
                raise ImageTooLargeError(
                    "La imagen supera el límite de 20 megapíxeles."
                )

            image = ImageOps.exif_transpose(original)
            if "A" in image.getbands() or "transparency" in image.info:
                rgba = image.convert("RGBA")
                image = Image.new("RGB", rgba.size, "white")
                image.paste(rgba, mask=rgba.getchannel("A"))
            else:
                image = image.convert("RGB")
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as error:
        raise InvalidImageError("El archivo no contiene una imagen válida.") from error

    tesseract_cmd = os.getenv("TESSERACT_CMD")
    if tesseract_cmd:
        pytesseract.pytesseract.tesseract_cmd = tesseract_cmd

    try:
        return pytesseract.image_to_string(
            image,
            lang=OCR_LANGUAGES,
            config="--psm 6",
            timeout=OCR_TIMEOUT_SECONDS,
        ).strip()
    except pytesseract.TesseractNotFoundError as error:
        raise OCRUnavailableError(
            "Tesseract no está instalado o no está disponible en PATH."
        ) from error
    except pytesseract.TesseractError as error:
        raise OCRUnavailableError(
            "Tesseract no tiene instalados los idiomas español e inglés."
        ) from error
    except RuntimeError as error:
        if "timeout" in str(error).casefold():
            raise OCRTimeoutError("El análisis OCR excedió el tiempo límite.") from error
        raise OCRProcessingError("Tesseract no pudo procesar la imagen.") from error
