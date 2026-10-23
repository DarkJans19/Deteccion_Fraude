"""
Módulo ``backend.models.model``
==============================

Combina el modelo de mensajes Qwen2.5-1.5B-Instruct (GGUF) con el analizador
de URLs para los enlaces detectados mediante expresiones regulares.

El modelo fue entrenado para una tarea binaria: dado un mensaje de texto, responder
con un veredicto que empieza por "RIESGO ALTO." (fraude, phishing o spam) o
"RIESGO BAJO." (comunicación legítima). Si hay enlaces, el veredicto final
se calcula con ``MATRIZ_RIESGO``; sin enlaces se conserva el resultado de Qwen.

Uso rápido
----------
>>> from backend.models.model import get_model
>>> modelo = get_model()
>>> prediccion = modelo.predecir("Tu cuenta fue bloqueada, ingresa a bit.ly/abc123")
>>> print(prediccion.veredicto)

Dependencias
------------
- llama-cpp-python (motor de inferencia llama.cpp con binding para Python)
"""

import asyncio
import os
import re
import threading
from dataclasses import dataclass, field
from pathlib import Path

# Permite también ejecutar este archivo directamente desde el IDE.
if __name__ == "__main__" and not __package__:
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from llama_cpp import Llama

from backend.url_analysis.layered import analyze as analyze_url
from backend.url_analysis.preprocessing import normalize_url
from backend.url_analysis.safe_browsing import SafeBrowsingClient

# --------------------------------------------------------------------------- #
# Configuración
# --------------------------------------------------------------------------- #

#: Ruta del archivo GGUF. Se puede sobrescribir con la variable de entorno
#: ``MODEL_PATH``. Por defecto apunta a ``backend/models/qwen_bosa_antifraude.gguf``.
MODEL_PATH = Path(
    os.getenv(
        "MODEL_PATH",
        Path(__file__).resolve().parent / "qwen_bosa_antifraude.gguf",
    )
)

#: Directorio del detector de URLs existente, configurable con ``URL_MODEL_DIR``.
URL_MODEL_DIR = Path(
    os.getenv(
        "URL_MODEL_DIR",
        Path(__file__).resolve().parents[1] / "url_analysis" / "artifacts" / "url_detector",
    )
)

#: Prompt de sistema. Debe ser idéntico al que ``tokenizer.apply_chat_template``
#: de Qwen añadió automáticamente durante el entrenamiento. Si se cambia, el
#: modelo puede dejar de responder con el formato "RIESGO ALTO/BAJO".
SYSTEM_PROMPT = "You are Qwen, created by Alibaba Cloud. You are a helpful assistant."

#: Longitud máxima, en caracteres, del texto que se envía al modelo. El
#: entrenamiento usó ``max_length=512`` tokens; 1500 caracteres en español
#: equivalen aproximadamente a ese límite.
MAX_CHARS = 1500

RIESGO_ALTO = "RIESGO ALTO"
RIESGO_BAJO = "RIESGO BAJO"
RIESGO_NO_CONCLUYENTE = "RIESGO NO CONCLUYENTE"

#: Frase que Qwen emite cuando clasifica un mensaje como fraude.
MARCADOR_FRAUDE = RIESGO_ALTO

#: Claves: (riesgo del mensaje, riesgo agregado de los enlaces).
MATRIZ_RIESGO = {
    (RIESGO_ALTO, RIESGO_ALTO): RIESGO_ALTO,
    (RIESGO_ALTO, RIESGO_BAJO): RIESGO_NO_CONCLUYENTE,
    (RIESGO_ALTO, RIESGO_NO_CONCLUYENTE): RIESGO_ALTO,
    (RIESGO_BAJO, RIESGO_ALTO): RIESGO_ALTO,
    (RIESGO_BAJO, RIESGO_BAJO): RIESGO_BAJO,
    (RIESGO_BAJO, RIESGO_NO_CONCLUYENTE): RIESGO_NO_CONCLUYENTE,
}

#: Incluye HTTP(S), www y dominios sin esquema, como bit.ly/abc123.
#: El límite izquierdo evita extraer dominios de direcciones de correo.
URL_PATTERN = re.compile(
    r"(?<![\w@./-])(?:https?://[^\s<>\"'`“”‘’]+|"
    r"(?:[^\W_](?:[\w-]{0,61}[^\W_])?\.)+"
    r"(?:xn--[a-z0-9-]+|[^\W\d_]{2,63})(?![\w@-])"
    r"(?::\d{1,5})?(?:[/?#][^\s<>\"'`“”‘’]*)?)",
    re.IGNORECASE,
)


def extraer_urls(texto: str) -> list[str]:
    """Extrae enlaces únicos del texto completo sin incluir puntuación externa."""
    urls = []
    vistas = set()
    for coincidencia in URL_PATTERN.finditer(texto):
        url = coincidencia.group().rstrip(".,;:!?")
        # Conserva paréntesis de la ruta y quita cierres del texto o Markdown.
        while url and url[-1] in ")]}":
            cierre = url[-1]
            apertura = {")": "(", "]": "[", "}": "{"}[cierre]
            if url.count(cierre) <= url.count(apertura):
                break
            url = url[:-1].rstrip(".,;:!?")
        clave = normalize_url(url) or url
        if clave not in vistas:
            vistas.add(clave)
            urls.append(url)
    return urls


# --------------------------------------------------------------------------- #
# Resultado
# --------------------------------------------------------------------------- #

@dataclass
class Prediccion:
    """Resultado combinado del mensaje y sus enlaces.

    Attributes:
        es_fraude: ``True`` para riesgo alto, ``False`` para riesgo bajo y
            ``None`` para un resultado no concluyente.
        veredicto: ``"RIESGO ALTO"``, ``"RIESGO BAJO"`` o
            ``"RIESGO NO CONCLUYENTE"`` según ambos análisis.
        respuesta_cruda: Texto exacto que generó Qwen, o una cadena vacía si
            la entrada solo contiene enlaces.
        veredicto_mensaje: Clasificación de Qwen, o ``None`` si solo hay enlaces.
        veredicto_url: Riesgo agregado de los enlaces, o ``None`` si no hay URLs.
        analisis_urls: Resultados del detector existente para cada enlace.
    """

    es_fraude: bool | None
    veredicto: str
    respuesta_cruda: str
    veredicto_mensaje: str | None = None
    veredicto_url: str | None = None
    analisis_urls: list[dict[str, object]] = field(default_factory=list)


# --------------------------------------------------------------------------- #
# Modelo
# --------------------------------------------------------------------------- #

class FraudModel:
    """Analizador de fraude que combina Qwen con el detector de URLs.

    La clase carga el modelo de forma perezosa (solo cuando se necesita) y es
    segura entre hilos: una instancia de ``Llama`` no admite inferencias
    simultáneas, por lo que las llamadas se serializan con un candado.

    Normalmente no se instancia directamente; se usa ``get_model()``, que
    devuelve una única instancia compartida por toda la aplicación.

    Args:
        ruta: Ruta al archivo ``.gguf``. Por defecto, ``MODEL_PATH``.
        n_ctx: Tamaño de la ventana de contexto en tokens. Debe cubrir el
            prompt de sistema, el texto del usuario y la respuesta generada.
        n_threads: Hilos de CPU para la inferencia. Por defecto, todos los
            núcleos disponibles.
        url_model_dir: Directorio de los modelos entrenados de URLs.
    """

    def __init__(
        self,
        ruta: Path = MODEL_PATH,
        n_ctx: int = 2048,
        n_threads: int | None = None,
        *,
        url_model_dir: Path = URL_MODEL_DIR,
    ):
        self.ruta = Path(ruta)
        self.n_ctx = n_ctx
        self.n_threads = n_threads or os.cpu_count() or 4
        self.url_model_dir = Path(url_model_dir)
        self._safe_browsing_client = SafeBrowsingClient.from_environment()
        self._llm: Llama | None = None
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ #
    # Ciclo de vida
    # ------------------------------------------------------------------ #

    @property
    def cargado(self) -> bool:
        """Indica si el modelo ya está cargado en memoria."""
        return self._llm is not None

    @property
    def safe_browsing_client(self) -> SafeBrowsingClient | None:
        """Cliente opcional compartido para comprobar la reputación de URLs."""
        return self._safe_browsing_client

    def cargar(self) -> None:
        """Carga el modelo en memoria si aún no lo está.

        Es idempotente: llamarla varias veces no recarga el modelo. Se
        recomienda invocarla al iniciar la aplicación, para que la primera
        petición del usuario no pague el costo de la carga.

        Raises:
            FileNotFoundError: Si el archivo GGUF no existe en ``self.ruta``.
        """
        with self._lock:
            if self._llm is not None:
                return
            if not self.ruta.exists():
                raise FileNotFoundError(f"No se encontró el modelo en: {self.ruta}")
            self._llm = Llama(
                model_path=str(self.ruta),
                chat_format="chatml",  # formato de plantilla que usa Qwen
                n_ctx=self.n_ctx,
                n_threads=self.n_threads,
                verbose=False,
            )

    # ------------------------------------------------------------------ #
    # Inferencia
    # ------------------------------------------------------------------ #

    def generar(self, texto: str, max_tokens: int = 60) -> str:
        """Envía un texto al modelo y devuelve su respuesta sin procesar.

        El texto se recorta a ``MAX_CHARS`` caracteres. La generación es
        determinista (``temperature=0``), así que el mismo texto produce
        siempre la misma respuesta.

        Args:
            texto: Mensaje a analizar (SMS, WhatsApp, correo, etc.).
            max_tokens: Máximo de tokens a generar. 60 sobra para las
                respuestas fijas con las que se entrenó el modelo.

        Returns:
            La respuesta del modelo, sin espacios sobrantes.

        Raises:
            FileNotFoundError: Si el modelo no se ha podido cargar.
        """
        self.cargar()
        texto = texto.strip()[:MAX_CHARS]
        with self._lock:
            salida = self._llm.create_chat_completion(
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": texto},
                ],
                temperature=0,
                max_tokens=max_tokens,
            )
        return salida["choices"][0]["message"]["content"].strip()

    def predecir(self, texto: str) -> Prediccion:
        """Analiza el mensaje con Qwen y cada URL con el detector existente.

        Si un enlace tiene riesgo alto, el riesgo agregado de URLs es alto.
        Si ninguno es alto pero alguno requiere revisión o falla al analizarse,
        el riesgo de URLs es no concluyente. Solo es bajo si todos son bajos.
        Después se aplica ``MATRIZ_RIESGO`` con el resultado del mensaje.
        Sin URLs se conserva la clasificación de Qwen.
        Si solo hay enlaces, se omite Qwen y se usa el riesgo de URLs.

        Args:
            texto: Mensaje a analizar.

        Returns:
            Un objeto ``Prediccion`` con el veredicto y la respuesta cruda.
        """
        urls = extraer_urls(texto)
        texto_sin_urls = URL_PATTERN.sub("", texto)
        solo_urls = bool(urls) and not texto_sin_urls.strip(
            " \t\r\n.,;:!?()[]{}<>\"'`“”‘’"
        )
        crudo = ""
        veredicto_mensaje = None
        if not solo_urls:
            crudo = self.generar(texto)
            veredicto_mensaje = RIESGO_ALTO if MARCADOR_FRAUDE in crudo.upper() else RIESGO_BAJO
        analisis_urls = []
        for url in urls:
            try:
                resultado = analyze_url(
                    url,
                    self.url_model_dir,
                    safe_browsing_client=self._safe_browsing_client,
                )
            except (OSError, ValueError, RuntimeError) as error:
                resultado = {
                    "url": url,
                    "risk_level": "review",
                    "error": str(error),
                }
            analisis_urls.append(resultado)

        veredicto_url = None
        veredicto = veredicto_mensaje
        if analisis_urls:
            riesgos = {resultado.get("risk_level") for resultado in analisis_urls}
            if "high" in riesgos:
                veredicto_url = RIESGO_ALTO
            elif riesgos == {"low"}:
                veredicto_url = RIESGO_BAJO
            else:
                veredicto_url = RIESGO_NO_CONCLUYENTE
            veredicto = (
                veredicto_url if solo_urls
                else MATRIZ_RIESGO[veredicto_mensaje, veredicto_url]
            )

        return Prediccion(
            es_fraude=None if veredicto == RIESGO_NO_CONCLUYENTE else veredicto == RIESGO_ALTO,
            veredicto=veredicto,
            respuesta_cruda=crudo,
            veredicto_mensaje=veredicto_mensaje,
            veredicto_url=veredicto_url,
            analisis_urls=analisis_urls,
        )

    async def predecir_async(self, texto: str) -> Prediccion:
        """Versión asíncrona de ``predecir`` para usar dentro de FastAPI.

        La inferencia bloquea la CPU, así que se ejecuta en un hilo aparte
        para no congelar el bucle de eventos del servidor.

        Args:
            texto: Mensaje a analizar.

        Returns:
            Un objeto ``Prediccion``.
        """
        return await asyncio.to_thread(self.predecir, texto)


# --------------------------------------------------------------------------- #
# Instancia compartida
# --------------------------------------------------------------------------- #

_instancia: FraudModel | None = None


def get_model() -> FraudModel:
    """Devuelve la instancia única de ``FraudModel`` de la aplicación.

    Evita cargar el modelo (unos 1.6 GB en Q8_0) más de una vez. La instancia
    se crea en la primera llamada, pero el modelo no se carga hasta invocar
    ``cargar()`` o hacer la primera predicción.

    Returns:
        La instancia compartida de ``FraudModel``.
    """
    global _instancia
    if _instancia is None:
        _instancia = FraudModel()
    return _instancia


# EJEMPLO DE COMO USAR LA CLASE


if __name__ == "__main__":
    import time

    modelo = get_model()
    print("Ruta:", modelo.ruta, "| existe:", modelo.ruta.exists())

    t0 = time.perf_counter()
    modelo.cargar()
    print(f"Carga: {time.perf_counter() - t0:.1f} s | cargado: {modelo.cargado}")

    casos = [
        "Bancolombia: tu cuenta fue bloqueada, ingresa a bit.ly/abc123",
        "Hola mamá, cambié de número, guárdalo y envíame $200.000",
        "Te confirmo la reunión de mañana a las 3 pm en la oficina.",
        "🎉 Nequi está dando $600.000 de saldo por su Aniversario Yo ya recibí mi saldo de $600.000 😳 Revisa si tú también puedes reclamarlo Haz clic aquí 👇 https://adza.fmhgl.xyz/cHg7iSuvm/f762ee4e6494cd6c94c82dd8bb?p=w"
    ]
    for texto in casos:
        t0 = time.perf_counter()
        p = modelo.predecir(texto)
        print(f"{p.veredicto:22} {time.perf_counter() - t0:.1f}s | {texto[:45]}")
