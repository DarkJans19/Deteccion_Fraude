export type Verdict = "fraude" | "no_fraude" | "incierto" | "sin_analisis";
export type Source = "sms" | "correo" | "otro";
export type ReportLocation = {
  latitude: number;
  longitude: number;
  accuracy_m: number;
};
export type Analysis = {
  label: Exclude<Verdict, "sin_analisis">;
  score: number | null;
  explanation: string;
  model_name: string;
  model_version: string;
  model_digest: string;
  prompt_version: string;
};
export type Report = {
  schema_version: 3;
  id: string;
  title: string;
  text: string;
  source: Source;
  source_ref: string;
  geolocation: ReportLocation;
  created_at: string;
  analysis: Analysis | null;
  fraud_positive: boolean | null;
  hash: string;
};
export type ReportInput = Pick<
  Report,
  "title" | "text" | "source" | "source_ref" | "geolocation"
>;
export type Session = { csrf: string; model_available: boolean };

export const verdicts: Record<Verdict, string> = {
  fraude: "Positivo para fraude",
  no_fraude: "Negativo para fraude",
  incierto: "Incierto",
  sin_analisis: "Sin análisis",
};
export const sources: Record<Source, string> = {
  sms: "SMS",
  correo: "Correo",
  otro: "Otro",
};
export const verdict = (report: Report): Verdict =>
  report.analysis?.label ?? "sin_analisis";
export const coordinates = (location: ReportLocation) =>
  `${location.latitude.toFixed(3)}°, ${location.longitude.toFixed(3)}°`;
const APPROXIMATION_GRID_DEGREES = 0.02;

export const approximateLocation = (
  location: ReportLocation,
): ReportLocation => ({
  ...location,
  latitude:
    Math.floor(location.latitude / APPROXIMATION_GRID_DEGREES) *
      APPROXIMATION_GRID_DEGREES +
    APPROXIMATION_GRID_DEGREES / 2,
  longitude:
    Math.floor(location.longitude / APPROXIMATION_GRID_DEGREES) *
      APPROXIMATION_GRID_DEGREES +
    APPROXIMATION_GRID_DEGREES / 2,
});

export const date = (value: string) =>
  new Date(value).toLocaleString("es-CO", {
    dateStyle: "medium",
    timeStyle: "short",
  });

export async function api<T>(path: string, options?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(path, {
      signal: AbortSignal.timeout(30_000),
      ...options,
    });
  } catch {
    throw new Error(
      "No se pudo conectar con el servicio de trazabilidad. Verifica que hasher.py esté en ejecución y vuelve a intentar.",
    );
  }
  let result;
  try {
    result = await response.json();
  } catch {
    throw new Error(
      "El servicio no devolvió una respuesta válida. Verifica la conexión con el backend.",
    );
  }
  if (!response.ok)
    throw new Error(result.error || "No se pudo completar la operación.");
  return result as T;
}
