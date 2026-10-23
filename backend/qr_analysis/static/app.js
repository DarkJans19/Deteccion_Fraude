const cameraButton = document.querySelector("#cameraButton");
const cameraState = document.querySelector("#cameraState");
const imageInput = document.querySelector("#imageInput");
const scanMessage = document.querySelector("#scanMessage");
const viewport = document.querySelector("#viewport");
const video = document.querySelector("#video");
const urlForm = document.querySelector("#urlForm");
const urlInput = document.querySelector("#urlInput");
const loading = document.querySelector("#loading");
const errorCard = document.querySelector("#errorCard");
const errorMessage = document.querySelector("#errorMessage");
const result = document.querySelector("#result");

let stream = null;
let scanning = false;
let detector = null;
let lastDetection = 0;
const decodeCanvas = document.createElement("canvas");
const decodeContext = decodeCanvas.getContext("2d", { willReadFrequently: true });

function ensureDecoder() {
  if (!("BarcodeDetector" in window) && typeof window.jsQR !== "function") {
    throw new Error("No fue posible cargar el lector QR. Recarga la página e inténtalo nuevamente.");
  }
}

async function decodeQr(source) {
  if ("BarcodeDetector" in window) {
    if (!detector) detector = new BarcodeDetector({ formats: ["qr_code"] });
    const codes = await detector.detect(source);
    return codes[0]?.rawValue || null;
  }

  const sourceWidth = source.videoWidth || source.width;
  const sourceHeight = source.videoHeight || source.height;
  if (!sourceWidth || !sourceHeight || !decodeContext) return null;

  // Limitar el lienzo mantiene fluida la cámara en equipos modestos y
  // conserva suficiente resolución para la mayoría de códigos impresos.
  const maximumSide = 1400;
  const scale = Math.min(1, maximumSide / Math.max(sourceWidth, sourceHeight));
  decodeCanvas.width = Math.max(1, Math.round(sourceWidth * scale));
  decodeCanvas.height = Math.max(1, Math.round(sourceHeight * scale));
  decodeContext.drawImage(source, 0, 0, decodeCanvas.width, decodeCanvas.height);
  const image = decodeContext.getImageData(0, 0, decodeCanvas.width, decodeCanvas.height);
  const code = window.jsQR(image.data, image.width, image.height, {
    inversionAttempts: "attemptBoth",
  });
  return code?.data || null;
}

function setScanMessage(message, isError = false) {
  scanMessage.textContent = message;
  scanMessage.style.color = isError ? "var(--red)" : "";
}

async function stopCamera() {
  scanning = false;
  if (stream) stream.getTracks().forEach((track) => track.stop());
  stream = null;
  video.srcObject = null;
  viewport.hidden = true;
  cameraState.textContent = "Cámara inactiva";
  cameraState.classList.remove("active");
  cameraButton.innerHTML = '<span aria-hidden="true">▣</span> Usar cámara';
}

async function scanFrame(timestamp) {
  if (!scanning) return;
  if (timestamp - lastDetection > 300 && video.readyState >= 2) {
    lastDetection = timestamp;
    try {
      const value = await decodeQr(video);
      if (value) {
        await stopCamera();
        urlInput.value = value;
        setScanMessage("Código detectado. Iniciando análisis…");
        analyze(value);
        return;
      }
    } catch (error) {
      setScanMessage(`No fue posible leer la cámara: ${error.message}`, true);
    }
  }
  requestAnimationFrame(scanFrame);
}

cameraButton.addEventListener("click", async () => {
  if (stream) {
    await stopCamera();
    return;
  }
  try {
    ensureDecoder();
    stream = await navigator.mediaDevices.getUserMedia({
      video: { facingMode: { ideal: "environment" }, width: { ideal: 1280 } },
      audio: false,
    });
    video.srcObject = stream;
    await video.play();
    viewport.hidden = false;
    scanning = true;
    cameraState.textContent = "Buscando código QR";
    cameraState.classList.add("active");
    cameraButton.textContent = "Detener cámara";
    setScanMessage("Centra el código QR dentro del marco.");
    requestAnimationFrame(scanFrame);
  } catch (error) {
    await stopCamera();
    setScanMessage(error.message || "No se pudo acceder a la cámara.", true);
  }
});

imageInput.addEventListener("change", async () => {
  const file = imageInput.files?.[0];
  if (!file) return;
  let bitmap = null;
  try {
    ensureDecoder();
    bitmap = await createImageBitmap(file);
    const value = await decodeQr(bitmap);
    if (!value) throw new Error("No se encontró un código QR legible en la imagen.");
    urlInput.value = value;
    setScanMessage("Código detectado. Iniciando análisis…");
    analyze(value);
  } catch (error) {
    setScanMessage(error.message, true);
  } finally {
    bitmap?.close();
    imageInput.value = "";
  }
});

urlForm.addEventListener("submit", (event) => {
  event.preventDefault();
  analyze(urlInput.value);
});

function safeBrowsingText(block = {}) {
  if (block.is_threat === true) return ["Amenaza conocida", "Reportada por Google"];
  if (block.verdict === "no_match") return ["Sin coincidencia", "No equivale a sitio seguro"];
  if (block.status === "not_configured") return ["No configurado", "Falta la clave de API"];
  if (block.status === "error") return ["No disponible", "La consulta remota falló"];
  return ["No comprobado", "Resultado del modelo local"];
}

function riskText(level) {
  return { high: "Alto riesgo", review: "Requiere revisión", low: "Riesgo bajo" }[level] || "Sin determinar";
}

function showResult(data) {
  const summary = data.summary;
  const finalAnalysis = data.destination_analysis;
  const banner = document.querySelector("#resultBanner");
  banner.className = `result-banner ${summary.risk_level}`;
  document.querySelector("#riskIcon").textContent = summary.risk_level === "low" ? "✓" : "!";
  document.querySelector("#riskLabel").textContent = riskText(summary.risk_level);
  document.querySelector("#riskTitle").textContent = summary.title;
  document.querySelector("#scannedHost").textContent = data.scanned.hostname;
  document.querySelector("#scannedUrl").textContent = data.scanned.url;
  document.querySelector("#destinationHost").textContent = data.destination.hostname;
  document.querySelector("#destinationUrl").textContent = data.destination.url;

  const redirectNote = document.querySelector("#redirectNote");
  if (data.redirect.status === "unavailable") {
    redirectNote.textContent = `Destino no confirmado: ${data.redirect.error}`;
  } else if (data.redirect.domain_changed) {
    redirectNote.textContent = `Atención: después de ${data.redirect.redirect_count} redirección(es), el dominio cambia.`;
  } else if (data.redirect.redirect_count) {
    redirectNote.textContent = `${data.redirect.redirect_count} redirección(es), sin cambio de dominio registrable.`;
  } else {
    redirectNote.textContent = "El enlace no presentó redirecciones HTTP.";
  }

  document.querySelector("#modelResult").textContent = riskText(finalAnalysis.model_risk_level || finalAnalysis.risk_level);
  const probability = Number(finalAnalysis.phishing_probability);
  document.querySelector("#probability").textContent = Number.isFinite(probability)
    ? `${(probability * 100).toFixed(1)} % de probabilidad estimada`
    : "Probabilidad no disponible";
  const [safeTitle, safeDetail] = safeBrowsingText(finalAnalysis.safe_browsing);
  document.querySelector("#safeBrowsing").textContent = safeTitle;
  document.querySelector("#safeBrowsingDetail").textContent = safeDetail;
  const officialElement = document.querySelector("#officialMatch");
  const officialDetail = officialElement.nextElementSibling;
  if (data.authenticity.status === "suspected_impersonation") {
    officialElement.textContent = "Posible suplantación";
    officialDetail.textContent = `Se parece a ${data.authenticity.suspected_official_host}`;
  } else if (finalAnalysis.superfinanciera_host_match) {
    officialElement.textContent = "Dominio coincidente";
    officialDetail.textContent = "Superfinanciera de Colombia";
  } else {
    officialElement.textContent = "Sin coincidencia";
    officialDetail.textContent = "Superfinanciera de Colombia";
  }

  const reasons = document.querySelector("#reasons");
  reasons.replaceChildren(...summary.reasons.map((reason) => {
    const item = document.createElement("li");
    item.textContent = reason;
    return item;
  }));
  result.hidden = false;
  result.scrollIntoView({ behavior: "smooth", block: "start" });
}

async function analyze(value) {
  const url = value.trim();
  errorCard.hidden = true;
  result.hidden = true;
  if (!url) {
    errorMessage.textContent = "Ingresa o escanea una URL.";
    errorCard.hidden = false;
    return;
  }
  await stopCamera();
  loading.hidden = false;
  try {
    const response = await fetch("/api/analyze", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url }),
    });
    const data = await response.json();
    if (!response.ok) {
      const detail = typeof data.detail === "string" ? data.detail : data.error;
      throw new Error(detail || "El servidor rechazó la solicitud.");
    }
    showResult(data);
  } catch (error) {
    errorMessage.textContent = error.message || "Error inesperado.";
    errorCard.hidden = false;
  } finally {
    loading.hidden = true;
  }
}

window.addEventListener("pagehide", stopCamera);

if (!("BarcodeDetector" in window) && typeof window.jsQR === "function") {
  setScanMessage("Lector compatible activado. La imagen se procesa localmente en este navegador.");
}
