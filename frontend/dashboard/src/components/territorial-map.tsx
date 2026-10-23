import { lazy, Suspense, useEffect, useMemo, useState } from "react";
import { LoaderCircle } from "lucide-react";
import { ScrollTrigger } from "gsap/ScrollTrigger";
import { SpotlightCard } from "@/components/ui/spotlight-card";
import { api, normalizeReport, type MongoReport, type Report } from "@/lib/reports";
import {
  countLocalities,
  loadGeography,
  locateReport,
  type GeoManifest,
  type Localities,
  type LocatedReport,
} from "@/lib/geography";

const BogotaMap = lazy(() =>
  import("@/components/bogota-map").then(({ BogotaMap }) => ({ default: BogotaMap })),
);

const BOSA = "07";
// Brillo naranja de la paleta Alerta Bosa (en lugar del violeta del snippet).
const SPOTLIGHT = "#f9731640";
// El mapa público nunca recibe reportes individuales (sin pines, títulos ni hashes).
const NO_REPORTS: LocatedReport[] = [];
const ignoreReport = () => {};

type Geography = { localities: Localities; manifest: GeoManifest };

/**
 * Mapa público para ciudadanos: solo muestra totales por localidad.
 * No recibe reportes individuales, títulos, hashes ni seguimiento (eso queda en Informes/Admin).
 */
export default function TerritorialMap() {
  const [geography, setGeography] = useState<Geography | null>(null);
  const [geoError, setGeoError] = useState(false);
  const [reports, setReports] = useState<Report[] | null>(null);
  const [reportsError, setReportsError] = useState(false);
  const [selected, setSelected] = useState(BOSA);

  useEffect(() => {
    let active = true;
    loadGeography()
      .then((data) => active && setGeography(data))
      .catch(() => active && setGeoError(true));
    api<{ reports: (Report | MongoReport)[] }>("/api/reports")
      .then((data) => active && setReports(data.reports.map(normalizeReport)))
      .catch(() => {
        if (!active) return;
        setReports([]);
        setReportsError(true);
      });
    return () => {
      active = false;
    };
  }, []);

  // Cuando el mapa termina de cargar cambia la altura de la página: se recalculan las animaciones.
  useEffect(() => {
    const frame = requestAnimationFrame(() => ScrollTrigger.refresh());
    return () => cancelAnimationFrame(frame);
  }, [geography, reports]);

  const counts = useMemo(
    () =>
      geography && reports
        ? countLocalities(reports.map((report) => locateReport(report, geography.localities)))
        : {},
    [geography, reports],
  );

  const summary = useMemo(() => {
    const localities = geography?.localities.features.map(({ properties }) => ({
      name: properties.name,
      count: counts[properties.code] ?? 0,
    })) ?? [];
    const top = [...localities].sort((a, b) => b.count - a.count)[0];
    return {
      bosa: counts[BOSA] ?? 0,
      bogota: localities.reduce((total, item) => total + item.count, 0),
      top: top && top.count > 0 ? top : null,
    };
  }, [geography, counts]);

  const loading = !geoError && (!geography || reports === null);

  return (
    <div className="af-about-map">
      <div className="af-about-map-stats">
        <SpotlightCard className="af-about-stat-card" spotlightColor={SPOTLIGHT}>
          <span className="af-about-stat-label">Alertas en Bosa</span>
          <span className="af-about-stat-value">{loading ? "—" : summary.bosa}</span>
        </SpotlightCard>
        <SpotlightCard className="af-about-stat-card" spotlightColor={SPOTLIGHT}>
          <span className="af-about-stat-label">Alertas en Bogotá</span>
          <span className="af-about-stat-value">{loading ? "—" : summary.bogota}</span>
        </SpotlightCard>
        <SpotlightCard className="af-about-stat-card" spotlightColor={SPOTLIGHT}>
          <span className="af-about-stat-label">Localidad con más alertas</span>
          <span className="af-about-stat-value is-text">
            {loading ? "—" : summary.top ? `${summary.top.name} (${summary.top.count})` : "Sin datos aún"}
          </span>
        </SpotlightCard>
      </div>

      <div className="af-about-map-frame">
        {geography ? (
          <Suspense fallback={<MapPlaceholder loading text="Cargando mapa…" />}>
            <BogotaMap
              localities={geography.localities}
              manifest={geography.manifest}
              reports={NO_REPORTS}
              counts={counts}
              selectedId={null}
              selectedLocality={selected}
              onSelectReport={ignoreReport}
              onSelectLocality={setSelected}
              aggregateOnly
            />
          </Suspense>
        ) : (
          <MapPlaceholder
            loading={!geoError}
            text={geoError ? "El mapa no está disponible en este momento." : "Cargando cartografía…"}
          />
        )}
      </div>

      <p className="af-about-map-note" role="status">
        {reportsError
          ? "Los totales aparecerán cuando el servicio de reportes esté activo."
          : "Datos agregados y anónimos: solo se muestra cuántas alertas hay por localidad. Toca una localidad para acercarte."}
        {selected !== BOSA && (
          <button type="button" onClick={() => setSelected(BOSA)}>Volver a Bosa</button>
        )}
      </p>
    </div>
  );
}

function MapPlaceholder({ text, loading = false }: { text: string; loading?: boolean }) {
  return (
    <div className="af-about-map-placeholder">
      {loading && <LoaderCircle className="af-spin" size={18} aria-hidden="true" />}
      {text}
    </div>
  );
}
