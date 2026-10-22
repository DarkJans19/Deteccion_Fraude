# Deteccion_Fraude

## Deteccion de phishing en URLs con autoencoder

Este apartado no abre el enlace ni descarga la pagina. Une las fuentes de URLs
de `data_original`, normaliza y elimina duplicados, y usa la convencion
`0 = legitima`, `1 = phishing`. La lista de entidades vigiladas por la
Superfinanciera se carga desde la columna `URIPAGINAWEB` con etiqueta legitima;
valores como `Pendiente` se descartan por no ser URLs.

El detector es hibrido. Un autoencoder entrenado **solo con URLs legitimas**
aprende sus patrones y produce un puntaje de anomalia. Un segundo modelo
supervisado combina ese puntaje con rasgos lexicos y n-gramas, aprovechando las
etiquetas de phishing para aprender la diferencia entre ambas clases.

### 1. Preparar los datos

Primero crea el entorno e instala las dependencias; `tldextract` usa una copia
local de la Public Suffix List y no consulta Internet durante el analisis.

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe scripts/prepare_url_dataset.py
```

Se generan `train.csv`, `validation.csv`, `test.csv`, `conflicts.csv` y
`report.json` dentro de `dataset/urls`. Una misma URL normalizada nunca aparece
en mas de una particion. Tampoco se comparte un dominio registrable entre
entrenamiento, validacion y prueba. Los registros con etiquetas contradictorias
se excluyen y se documentan en `conflicts.csv`.

No se extraen por defecto las URLs del dataset de correos `Spaphish`: su etiqueta
pertenece al correo completo y puede asignar falsamente phishing a recursos
legitimos incrustados, como imagenes o CDNs.

### 2. Entrenar

```powershell
.venv\Scripts\python.exe scripts/train_url_detector.py
```

El modelo, el escalado, el umbral y las metricas quedan en
`artifacts/url_autoencoder`. Se generan dos umbrales con URLs legitimas de
validacion: revision (5 % maximo de falsos positivos) y alerta (1 %). Los limites
de muestreo se pueden ampliar segun la memoria disponible; consulta `--help`.

### 3. Configurar Google Safe Browsing

Activa **Safe Browsing API** en un proyecto de Google Cloud, crea una clave y
restringela a esa API y, si aplica, a la IP del servidor. No guardes la clave en
el repositorio. Para la sesion actual de PowerShell:

```powershell
$env:GOOGLE_SAFE_BROWSING_API_KEY = "TU_CLAVE"
```

La integracion usa `urls.search` de Safe Browsing v5. Envia la URL completa a
Google, incluida su consulta, por lo que no debe usarse con URLs que contengan
tokens o datos sensibles si esa transferencia no esta autorizada. Safe Browsing
es para uso no comercial; para un producto comercial se debe evaluar Web Risk.

### 4. Analizar una URL

```powershell
.venv\Scripts\python.exe scripts/analyze_url.py "https://ejemplo.com/login?id=123"
```

La consulta a Safe Browsing se hace primero y el modelo local se ejecuta despues.
Una amenaza conocida por Google fuerza riesgo alto. Una respuesta `no_match` no
significa que la URL sea segura: el modelo local conserva su propia evaluacion.
Si ademas la URL base coincide exactamente con un host del registro de la
Superfinanciera, el resultado incluye esa evidencia. Las rutas internas anomalas
siguen en revision aunque pertenezcan a uno de esos hosts.
Si no hay clave o la API falla, el estado queda explicito y, por defecto, el
analisis local continua.

Para exigir que la capa remota responda, agrega `--require-safe-browsing`. Para
una ejecucion deliberadamente local, usa `--skip-safe-browsing`.

La salida JSON incluye el bloque `safe_browsing`, probabilidad de phishing,
nivel de riesgo y puntaje de anomalia. `www` se ignora en la representacion
aprendida, por lo que `google.com` y `www.google.com` producen los mismos rasgos.
El resultado sigue siendo una senal y debe combinarse con DNS/TLS y analisis del
contenido antes de bloquear una URL en produccion.
