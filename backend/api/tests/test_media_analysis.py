from __future__ import annotations

import asyncio
import io
import os
import shutil
import sys
import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

# Permite ejecutar este archivo directamente con una ruta absoluta, además de
# `python -m unittest` desde la raíz del repositorio.
PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pytesseract
from PIL import Image, ImageDraw, ImageFont
from fastapi import HTTPException
from starlette.datastructures import Headers, UploadFile

from backend.api import media_analysis, models_api
from backend.api.media_analysis import (
    ExtractedContent,
    ImageAnalysisError,
    _ocr,
    compose_analysis_text,
    extract_image_content,
)


def png_bytes(text: str = "HELLO WORLD") -> bytes:
    font = ImageFont.load_default()
    probe = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    bounds = probe.textbbox((0, 0), text, font=font)
    image = Image.new("RGB", (bounds[2] + 40, bounds[3] + 30), "white")
    ImageDraw.Draw(image).text((20, 10), text, font=font, fill="black")
    image = image.resize((image.width * 5, image.height * 5), Image.Resampling.LANCZOS)
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


@dataclass
class FakePrediction:
    es_fraude: bool
    veredicto: str
    respuesta_cruda: str
    veredicto_mensaje: str
    veredicto_url: str | None
    analisis_urls: list[dict]


def expected_prediction():
    return FakePrediction(
        es_fraude=True,
        veredicto="RIESGO ALTO",
        respuesta_cruda="RIESGO ALTO. Mensaje sospechoso.",
        veredicto_mensaje="RIESGO ALTO",
        veredicto_url=None,
        analisis_urls=[],
    )


def image_upload(data: bytes, content_type: str = "image/png") -> UploadFile:
    image_file = tempfile.SpooledTemporaryFile()
    image_file.write(data)
    image_file.seek(0)
    return UploadFile(
        file=image_file,
        filename="captura.png",
        headers=Headers({"content-type": content_type}),
    )


def fake_request(model):
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(model=model)))


async def run_inline(function, *args, **kwargs):
    return function(*args, **kwargs)


class OCRUnitTests(unittest.TestCase):
    def test_ocr_uses_spanish_and_english_and_strips_result(self):
        image = Image.new("RGB", (200, 80), "white")
        with (
            patch("pytesseract.get_languages", return_value=["eng", "spa"]),
            patch("pytesseract.image_to_string", return_value="  texto leído  ") as recognize,
        ):
            text, warnings = _ocr(image)

        self.assertEqual(text, "texto leído")
        self.assertEqual(warnings, [])
        self.assertEqual(recognize.call_args.kwargs["lang"], "spa+eng")
        self.assertEqual(recognize.call_args.kwargs["timeout"], 15)

    def test_ocr_falls_back_to_english_if_spanish_is_missing(self):
        image = Image.new("RGB", (200, 80), "white")
        with (
            patch.dict(os.environ, {"OCR_LANG": "spa"}),
            patch("pytesseract.get_languages", return_value=["eng"]),
            patch("pytesseract.image_to_string", return_value="account blocked") as recognize,
        ):
            text, warnings = _ocr(image)

        self.assertEqual(text, "account blocked")
        self.assertEqual(recognize.call_args.kwargs["lang"], "eng")
        self.assertTrue(any("se usó 'eng'" in warning for warning in warnings))

    def test_ocr_reports_missing_tesseract_without_crashing(self):
        image = Image.new("RGB", (200, 80), "white")
        with patch("pytesseract.get_languages", side_effect=FileNotFoundError):
            text, warnings = _ocr(image)

        self.assertEqual(text, "")
        self.assertTrue(any("No se pudo ejecutar Tesseract" in warning for warning in warnings))

    def test_extract_image_content_combines_ocr_and_qr(self):
        with (
            patch.object(media_analysis, "_decode_qr", return_value=(["https://fraude.test"], [])),
            patch.object(media_analysis, "_ocr", return_value=("Cuenta bloqueada", [])),
        ):
            result = extract_image_content(png_bytes())

        self.assertEqual(result.text, "Cuenta bloqueada")
        self.assertEqual(result.qr_contents, ["https://fraude.test"])
        self.assertEqual(
            compose_analysis_text(result, "Revísalo"),
            "Revísalo\n\nContenido de códigos QR:\nhttps://fraude.test"
            "\n\nTexto detectado en la imagen:\nCuenta bloqueada",
        )

    def test_rejects_empty_invalid_and_oversized_images(self):
        with self.assertRaisesRegex(ImageAnalysisError, "vacía"):
            extract_image_content(b"")
        with self.assertRaisesRegex(ImageAnalysisError, "imagen válida"):
            extract_image_content(b"esto no es una imagen")
        with (
            patch.object(media_analysis, "max_image_bytes", return_value=2),
            self.assertRaisesRegex(ImageAnalysisError, "tamaño máximo"),
        ):
            extract_image_content(b"123")

    @unittest.skipUnless(shutil.which(os.getenv("TESSERACT_CMD", "tesseract")), "Tesseract no está instalado")
    def test_real_tesseract_recognizes_generated_text(self):
        languages = set(pytesseract.get_languages(config=""))
        if "eng" not in languages:
            self.skipTest("Tesseract no tiene instalado el idioma inglés")

        with patch.object(media_analysis, "_decode_qr", return_value=([], [])):
            result = extract_image_content(png_bytes("HELLO WORLD"))

        self.assertIn("HELLO", result.text.upper())


class OCRApiTests(unittest.TestCase):
    def test_image_endpoint_sends_extracted_text_to_fraud_model(self):
        model = SimpleNamespace(predecir_async=AsyncMock(return_value=expected_prediction()))
        request = fake_request(model)
        with (
            patch.object(models_api, "run_in_threadpool", new=run_inline),
            patch.object(
                media_analysis,
                "_decode_qr",
                return_value=(["https://fraude.test"], []),
            ),
            patch("pytesseract.get_languages", return_value=["eng", "spa"]),
            patch("pytesseract.image_to_string", return_value=" Cuenta bloqueada "),
        ):
            response = asyncio.run(
                models_api.analizar_imagen(
                    request,
                    image_upload(png_bytes()),
                    texto="Revísalo",
                )
            )

        extracted = ExtractedContent(
            text="Cuenta bloqueada",
            qr_contents=["https://fraude.test"],
            warnings=[],
        )
        expected_text = compose_analysis_text(extracted, "Revísalo")
        self.assertEqual(response["texto_extraido"], "Cuenta bloqueada")
        self.assertEqual(response["qr_detectados"], ["https://fraude.test"])
        self.assertEqual(response["veredicto"], "RIESGO ALTO")
        model.predecir_async.assert_awaited_once_with(expected_text)

    def test_image_endpoint_returns_422_when_ocr_is_unavailable_and_no_qr_exists(self):
        model = SimpleNamespace(predecir_async=AsyncMock(return_value=expected_prediction()))
        request = fake_request(model)
        with (
            patch.object(models_api, "run_in_threadpool", new=run_inline),
            patch.object(media_analysis, "_decode_qr", return_value=([], [])),
            patch("pytesseract.get_languages", side_effect=FileNotFoundError),
        ):
            with self.assertRaises(HTTPException) as raised:
                asyncio.run(
                    models_api.analizar_imagen(request, image_upload(png_bytes()))
                )

        self.assertEqual(raised.exception.status_code, 422)
        self.assertIn("No se pudo extraer texto", raised.exception.detail)
        model.predecir_async.assert_not_awaited()

    def test_image_endpoint_rejects_non_image_media_type(self):
        model = SimpleNamespace(predecir_async=AsyncMock(return_value=expected_prediction()))
        upload = image_upload(b"texto", content_type="text/plain")
        with self.assertRaises(HTTPException) as raised:
            asyncio.run(
                models_api.analizar_imagen(
                    fake_request(model),
                    upload,
                )
            )

        self.assertEqual(raised.exception.status_code, 415)
        self.assertTrue(upload.file.closed)
        model.predecir_async.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
