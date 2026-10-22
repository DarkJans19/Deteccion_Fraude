import booleanPointInPolygon from "@turf/boolean-point-in-polygon";
import type { FeatureCollection, MultiPolygon, Polygon } from "geojson";
import type { Report } from "./reports";

export type Locality = { code: string; name: string; center: [number, number] };
export type Localities = FeatureCollection<Polygon | MultiPolygon, Locality>;
export type GeoManifest = {
  source: string;
  source_features: number;
  layers: { code: string; bbox: [number, number, number, number] }[];
};
export type LocatedReport = Report & {
  localityCode: string;
  localityName: string;
};

export function validLocation(report: Report) {
  const geo = report.geolocation;
  return (
    !!geo &&
    Number.isFinite(geo.latitude) &&
    Number.isFinite(geo.longitude) &&
    Math.abs(geo.latitude) <= 90 &&
    Math.abs(geo.longitude) <= 180
  );
}

export function locateReport(
  report: Report,
  localities: Localities,
): LocatedReport {
  if (!validLocation(report))
    return {
      ...report,
      localityCode: "missing",
      localityName: "Sin ubicación",
    };
  const { longitude, latitude } = report.geolocation;
  const locality = localities.features.find((feature) => {
    const bounds = feature.bbox;
    if (
      bounds &&
      (longitude < bounds[0] ||
        latitude < bounds[1] ||
        longitude > bounds[2] ||
        latitude > bounds[3])
    )
      return false;
    return booleanPointInPolygon([longitude, latitude], feature);
  });
  return {
    ...report,
    localityCode: locality?.properties.code ?? "outside",
    localityName: locality?.properties.name ?? "Fuera de Bogotá",
  };
}

export function countLocalities(reports: LocatedReport[]) {
  const counts: Record<string, number> = {};
  for (const report of reports)
    counts[report.localityCode] = (counts[report.localityCode] ?? 0) + 1;
  return counts;
}

export async function loadGeography() {
  const [localities, manifest] = await Promise.all([
    fetch("/geo/localidades.geojson"),
    fetch("/geo/manifest.json"),
  ]);
  if (!localities.ok || !manifest.ok)
    throw new Error(
      "No se pudo cargar la cartografía de Bogotá. Vuelve a intentar.",
    );
  const data = (await localities.json()) as Localities;
  if (data.type !== "FeatureCollection" || data.features.length !== 20)
    throw new Error("La capa de localidades está incompleta.");
  const metadata = (await manifest.json()) as GeoManifest;
  if (!Array.isArray(metadata.layers))
    throw new Error("Falta el índice de las manzanas del mapa.");
  return { localities: data, manifest: metadata };
}
