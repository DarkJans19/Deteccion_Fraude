export type RiskVerdict =
  "RIESGO ALTO" | "RIESGO BAJO" | "RIESGO NO CONCLUYENTE";

export type Prediction = {
  es_fraude: boolean | null;
  veredicto: RiskVerdict;
  respuesta_cruda: string;
  veredicto_mensaje: RiskVerdict | null;
  veredicto_url: RiskVerdict | null;
  analisis_urls: Record<string, unknown>[];
};

export type ImagePrediction = Prediction & {
  texto_extraido: string;
  qr_detectados: string[];
  advertencias: string[];
};

export type GeneratedResponse = { respuesta_cruda: string };

export type QrAnalysis = {
  scanned: { url: string; hostname: string; registrable_domain: string };
  destination: { url: string; hostname: string; registrable_domain: string };
  redirect: {
    status: string;
    redirect_count: number;
    domain_changed: boolean;
    error?: string;
  };
  initial_analysis: Record<string, unknown>;
  destination_analysis: Record<string, unknown>;
  authenticity: Record<string, unknown>;
  summary: {
    risk_level: "high" | "review" | "low";
    title: string;
    reasons: string[];
  };
};

export type AnalysisHealth = {
  status: "ok" | "degraded";
  model_available: boolean;
  safe_browsing_configured: boolean;
  redirect_checks_enabled: boolean;
};

const base = (
  import.meta.env.VITE_ANALYSIS_API_BASE || "/analysis-api"
).replace(/\/$/, "");

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${base}${path}`, {
      signal: AbortSignal.timeout(130_000),
      ...init,
    });
  } catch (error) {
    if (error instanceof DOMException && error.name === "TimeoutError")
      throw new Error("El análisis tardó demasiado. Inténtalo nuevamente.");
    throw new Error(
      "No se pudo conectar con el servicio de análisis. Verifica que la API esté en ejecución.",
    );
  }

  let body: unknown;
  try {
    body = await response.json();
  } catch {
    throw new Error("El servicio de análisis devolvió una respuesta inválida.");
  }
  if (!response.ok) {
    const detail =
      body && typeof body === "object" && "detail" in body
        ? (body as { detail?: unknown }).detail
        : undefined;
    throw new Error(
      typeof detail === "string" ? detail : "No se pudo completar el análisis.",
    );
  }
  return body as T;
}

const json = (body: object): RequestInit => ({
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body),
});

export const analysisApi = {
  health: () => request<AnalysisHealth>("/health"),
  analyzeText: (texto: string) =>
    request<Prediction>("/analizar", json({ texto })),
  generate: (texto: string, maxTokens = 120) =>
    request<GeneratedResponse>(
      "/respuesta",
      json({ texto, max_tokens: maxTokens }),
    ),
  analyzeQr: (url: string) => request<QrAnalysis>("/analyze", json({ url })),
  analyzeImage: (imagen: File, texto?: string) => {
    const form = new FormData();
    form.append("imagen", imagen);
    if (texto?.trim()) form.append("texto", texto.trim());
    return request<ImagePrediction>("/analizar-imagen", {
      method: "POST",
      body: form,
    });
  },
};
