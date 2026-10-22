"""
Módulo ``core.model``
=====================

Encapsula el modelo de lenguaje afinado (Qwen2.5-1.5B-Instruct + LoRA fusionado,
exportado a GGUF) y lo expone como una clase lista para usar desde la API.

El modelo fue entrenado para una tarea binaria: dado un mensaje de texto, responder
con un veredicto que empieza por "RIESGO ALTO." (fraude, phishing o spam) o
"RIESGO BAJO." (comunicación legítima). La categoría concreta del fraude NO la
decide este módulo; eso lo hace ``core.fraud_validator``.

Uso rápido
----------
>>> from core.model import get_model
>>> modelo = get_model()
>>> modelo.predecir("Tu cuenta fue bloqueada, ingresa a bit.ly/abc123")
Prediccion(es_fraude=True, veredicto='RIESGO ALTO', respuesta_cruda='RIESGO ALTO. ...')

Dependencias
------------
- llama-cpp-python (motor de inferencia llama.cpp con binding para Python)
"""

import asyncio
import os
import threading
from dataclasses import dataclass
from pathlib import Path

from llama_cpp import Llama

# --------------------------------------------------------------------------- #
# Configuración
# --------------------------------------------------------------------------- #

#: Ruta del archivo GGUF. Se puede sobrescribir con la variable de entorno
#: ``MODEL_PATH``. Por defecto apunta a ``backend/models/qwen_bosa_antifraude.gguf``.
MODEL_PATH = Path(
    os.getenv(
        "MODEL_PATH",
        Path(__file__).resolve().parent.parent / "models" / "qwen_bosa_antifraude.gguf",
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

#: Frase que el modelo emite cuando clasifica un mensaje como fraude.
MARCADOR_FRAUDE = "RIESGO ALTO"


# --------------------------------------------------------------------------- #
# Resultado
# --------------------------------------------------------------------------- #

@dataclass
class Prediccion:
    """Resultado de analizar un texto con el modelo.

    Attributes:
        es_fraude: ``True`` si el modelo clasificó el texto como fraude.
        veredicto: Etiqueta normalizada, ``"RIESGO ALTO"`` o ``"RIESGO BAJO"``.
        respuesta_cruda: Texto exacto que generó el modelo, útil para depurar
            o para mostrar la explicación completa al usuario.
    """

    es_fraude: bool
    veredicto: str
    respuesta_cruda: str


# --------------------------------------------------------------------------- #
# Modelo
# --------------------------------------------------------------------------- #

class FraudModel:
    """Envoltorio del modelo GGUF para detección de fraude.

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
    """

    def __init__(
        self,
        ruta: Path = MODEL_PATH,
        n_ctx: int = 2048,
        n_threads: int | None = None,
    ):
        self.ruta = Path(ruta)
        self.n_ctx = n_ctx
        self.n_threads = n_threads or os.cpu_count() or 4
        self._llm: Llama | None = None
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ #
    # Ciclo de vida
    # ------------------------------------------------------------------ #

    @property
    def cargado(self) -> bool:
        """Indica si el modelo ya está cargado en memoria."""
        return self._llm is not None

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
        """Analiza un texto y devuelve un veredicto estructurado.

        El texto se considera fraude si la respuesta del modelo contiene
        "RIESGO ALTO" (sin distinguir mayúsculas). Cualquier otra respuesta
        se trata como "RIESGO BAJO".

        Args:
            texto: Mensaje a analizar.

        Returns:
            Un objeto ``Prediccion`` con el veredicto y la respuesta cruda.
        """
        crudo = self.generar(texto)
        fraude = MARCADOR_FRAUDE in crudo.upper()
        return Prediccion(
            es_fraude=fraude,
            veredicto="RIESGO ALTO" if fraude else "RIESGO BAJO",
            respuesta_cruda=crudo,
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