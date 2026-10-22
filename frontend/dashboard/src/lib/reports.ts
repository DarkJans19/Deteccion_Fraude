export type Verdict = "fraude" | "no_fraude" | "incierto" | "sin_analisis";
export type Source = "sms" | "correo" | "otro";
export type ReportLocation = {
  latitude: number;
  longitude: number;
  accuracy_m: number | null;
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
  description?: string;
  text: string;
  source: Source;
  source_ref: string;
  geolocation: ReportLocation;
  created_at: string;
  analysis: Analysis | null;
  fraud_positive: boolean | null;
  hash: string;
  storage?: "mongodb";
};
export type MongoReport = {
  _id: { $oid: string };
  description: string;
  messageContent?: string;
  date: { $date: string };
  hashCode: string;
  location: { type: "Point"; coordinates: [number, number] };
};
export type CaseStatus =
  "registrado" | "en_revision" | "confirmado" | "descartado" | "cerrado";
export const caseStatuses: Record<CaseStatus, string> = {
  registrado: "Registrado",
  en_revision: "En revisión",
  confirmado: "Fraude confirmado",
  descartado: "Descartado",
  cerrado: "Cerrado",
};
export type Tracking = {
  reportId: string;
  status: CaseStatus;
  registeredAt: string;
  events: {
    _id: { $oid: string };
    date: { $date: string };
    status: Exclude<CaseStatus, "registrado">;
    note: string;
    hashCode: string;
  }[];
};

export function normalizeReport(report: Report | MongoReport): Report {
  if (!("_id" in report)) return report;
  return {
    schema_version: 3,
    id: report._id.$oid,
    title: report.description.slice(0, 160),
    description: report.description,
    text: report.messageContent ?? "",
    source: "otro",
    source_ref: "",
    geolocation: {
      latitude: report.location.coordinates[1],
      longitude: report.location.coordinates[0],
      accuracy_m: null,
    },
    created_at: report.date.$date,
    analysis: null,
    fraud_positive: null,
    hash: report.hashCode,
    storage: "mongodb",
  };
}
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
      "No se pudo conectar con el servicio de trazabilidad. Verifica que el backend esté en ejecución y vuelve a intentar.",
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
