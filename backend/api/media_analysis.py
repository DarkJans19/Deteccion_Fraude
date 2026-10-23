"""Extracción de texto y códigos QR a partir de imágenes.

El modelo de fraude trabaja con texto, así que este módulo convierte capturas
de pantalla, fotos de conversaciones y códigos QR en texto reutilizable por
``FraudModel.predecir``. Las dependencias son opcionales: si falta Tesseract u
OpenCV se devuelve una advertencia en lugar de impedir el arranque de la API.
"""

from __future__ import annotations

import io
import os
from dataclasses import dataclass, field

MAX_IMAGE_MB_DEFAULT = 10
MAX_OCR_CHARS = 50_000
MAX_QR_ITEMS = 20


class ImageAnalysisError(ValueError):
    """La imagen no se pudo leer o no contiene información analizable."""


@dataclass
class ExtractedContent:
    """Contenido obtenido de una imagen.

    Attributes:
        text: Texto reconocido por OCR, recortado a ``MAX_OCR_CHARS``.
        qr_contents: Contenidos decodificados de los códigos QR encontrados.
        warnings: Avisos sobre dependencias ausentes o fallos parciales.
    """

    text: str
    qr_contents: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def max_image_bytes() -> int:
    """Devuelve el límite configurado para el tamaño de una imagen."""
    raw = os.getenv("IMAGE_MAX_MB", str(MAX_IMAGE_MB_DEFAULT)).strip()
    try:
        megabytes = int(raw)
    except ValueError:
        megabytes = MAX_IMAGE_MB_DEFAULT
    return max(1, megabytes) * 1024 * 1024


def _open_image(data: bytes):
    try:
        from PIL import Image, ImageOps
    except ImportError as error:
        raise ImageAnalysisError(
            "Pillow no está instalado; no se pueden procesar imágenes."
        ) from error

    try:
        image = Image.open(io.BytesIO(data))
        image.load()
    except (OSError, ValueError) as error:
        raise ImageAnalysisError("El archivo no es una imagen válida.") from error

    image = ImageOps.exif_transpose(image)
    if image.mode not in ("L", "RGB"):
        image = image.convert("RGB")
    return image


def _decode_qr(image) -> tuple[list[str], list[str]]:
    try:
        import cv2
        import numpy as np
    except ImportError:
        return [], [
            "Lector QR no disponible: instala opencv-python-headless para procesar códigos QR."
        ]

    array = cv2.cvtColor(np.array(image.convert("RGB")), cv2.COLOR_RGB2BGR)
    detector = cv2.QRCodeDetector()
    contents: list[str] = []

    try:
        ok, decoded, _, _ = detector.detectAndDecodeMulti(array)
        if ok:
            contents = [item.strip() for item in decoded if item and item.strip()]
    except (cv2.error, ValueError):
        contents = []

    if not contents:
        try:
            content, _, _ = detector.detectAndDecode(array)
            if content and content.strip():
                contents = [content.strip()]
        except (cv2.error, ValueError):
            contents = []

    unique: list[str] = []
    for content in contents:
        if content not in unique:
            unique.append(content)
        if len(unique) >= MAX_QR_ITEMS:
            break
    return unique, []


def _ocr(image) -> tuple[str, list[str]]:
    try:
        import pytesseract
    except ImportError:
        return "", [
            "OCR no disponible: instala pytesseract y Tesseract para leer texto de imágenes."
        ]

    command = os.getenv("TESSERACT_CMD", "").strip()
    if command:
        pytesseract.pytesseract.tesseract_cmd = command

    requested_language = os.getenv("OCR_LANG", "spa+eng").strip() or "spa+eng"
    warnings: list[str] = []
    try:
        available = set(pytesseract.get_languages(config=""))
    except Exception:
        return "", [
            "No se pudo ejecutar Tesseract. Instálalo y define TESSERACT_CMD si es necesario."
        ]

    selected = [lang for lang in requested_language.split("+") if lang in available]
    if not selected:
        if "eng" in available:
            selected = ["eng"]
            warnings.append(
                f"El idioma OCR '{requested_language}' no está instalado; se usó 'eng'."
            )
        else:
            return "", [
                f"Tesseract no tiene los idiomas '{requested_language}' ni 'eng' instalados."
            ]

    try:
        from PIL import ImageOps

        prepared = ImageOps.autocontrast(image.convert("L"))
        text = pytesseract.image_to_string(
            prepared,
            lang="+".join(selected),
            config="--psm 6",
        )
    except Exception:
        return "", ["Tesseract no pudo leer la imagen."]

    return text.strip()[:MAX_OCR_CHARS], warnings


def extract_image_content(data: bytes) -> ExtractedContent:
    """Extrae texto y códigos QR de una imagen.

    Args:
        data: Bytes originales de la imagen.

    Returns:
        El texto reconocido, los contenidos QR y las advertencias generadas.

    Raises:
        ImageAnalysisError: Si la imagen está vacía, supera el límite o no se
            puede abrir.
    """
    if not data:
        raise ImageAnalysisError("La imagen está vacía.")
    if len(data) > max_image_bytes():
        raise ImageAnalysisError(
            "La imagen supera el tamaño máximo permitido."
        )

    image = _open_image(data)
    qr_contents, qr_warnings = _decode_qr(image)
    text, ocr_warnings = _ocr(image)
    return ExtractedContent(
        text=text,
        qr_contents=qr_contents,
        warnings=[*qr_warnings, *ocr_warnings],
    )


def compose_analysis_text(content: ExtractedContent, caption: str | None = None) -> str:
    """Combina el pie de foto, los QR y el OCR en un único texto para el modelo."""
    parts: list[str] = []
    if caption and caption.strip():
        parts.append(caption.strip())
    if content.qr_contents:
        parts.append("Contenido de códigos QR:\n" + "\n".join(content.qr_contents))
    if content.text:
        parts.append("Texto detectado en la imagen:\n" + content.text)
    return "\n\n".join(parts).strip()
