import {
  lazy,
  Suspense,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import {
  Check,
  Copy,
  LoaderCircle,
  MapPin,
  RefreshCw,
  Search,
  X,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { ReportTracking } from "@/components/report-tracking";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  api,
  date,
  normalizeReport,
  type MongoReport,
  type Report,
} from "@/lib/reports";
import {
  countLocalities,
  loadGeography,
  locateReport,
  type Localities,
  type GeoManifest,
} from "@/lib/geography";
import { cn } from "@/lib/utils";

const BogotaMap = lazy(() =>
  import("@/components/bogota-map").then(({ BogotaMap }) => ({
    default: BogotaMap,
  })),
);

export default function App() {
  const [reports, setReports] = useState<Report[]>([]);
  const [geography, setGeography] = useState<{
    localities: Localities;
    manifest: GeoManifest;
  } | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [geoError, setGeoError] = useState("");
  const [query, setQuery] = useState("");
  const [locality, setLocality] = useState("all");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [copied, setCopied] = useState<string | null>(null);
  const [copyError, setCopyError] = useState("");
  const requestId = useRef(0);
  const geoRequestId = useRef(0);

  const refresh = useCallback(async () => {
    const current = ++requestId.current;
    setLoading(true);
    setError("");
    try {
      const data = await api<{ reports: (Report | MongoReport)[] }>(
        "/api/reports",
      );
      if (current !== requestId.current) return;
      const normalized = data.reports.map(normalizeReport);
      setReports(normalized);
      setSelectedId((id) =>
        normalized.some((report) => report.id === id) ? id : null,
      );
    } catch (failure) {
      if (current !== requestId.current) return;
      setReports([]);
      setSelectedId(null);
      setError((failure as Error).message);
    } finally {
      if (current === requestId.current) setLoading(false);
    }
  }, []);

  const fetchGeography = useCallback(async () => {
    const current = ++geoRequestId.current;
    setGeoError("");
    try {
      const data = await loadGeography();
      if (current === geoRequestId.current) setGeography(data);
    } catch {
      if (current === geoRequestId.current)
        setGeoError(
          "No se pudo cargar la cartografía. Reintenta para ver el mapa y las estadísticas.",
        );
    }
  }, []);

  useEffect(() => {
    void refresh();
    void fetchGeography();
    return () => {
      requestId.current += 1;
      geoRequestId.current += 1;
    };
  }, [refresh, fetchGeography]);

  const located = useMemo(
    () =>
      geography
        ? reports.map((report) => locateReport(report, geography.localities))
        : reports.map((report) => ({
            ...report,
            localityCode: "pending",
            localityName: "Localidad por determinar",
          })),
    [reports, geography],
  );
  const searched = useMemo(() => {
    const needle = query.trim().toLocaleLowerCase("es");
    return located.filter((report) =>
      `${report.title} ${report.id} ${report.hash} ${report.localityName}`
        .toLocaleLowerCase("es")
        .includes(needle),
    );
  }, [located, query]);
  const visible = useMemo(
    () =>
      searched.filter(
        (report) => locality === "all" || report.localityCode === locality,
      ),
    [searched, locality],
  );
  const counts = useMemo(() => countLocalities(searched), [searched]);
  const maxCount = Math.max(1, ...Object.values(counts));
  const stats = useMemo(
    () =>
      geography?.localities.features
        .map(({ properties }) => ({
          ...properties,
          count: counts[properties.code] ?? 0,
        }))
        .sort(
          (a, b) => b.count - a.count || a.name.localeCompare(b.name, "es"),
        ) ?? [],
    [geography, counts],
  );
  const ready = geography && !loading && !error;
  const selectedReport = reports.find((report) => report.id === selectedId);

  const selectLocality = useCallback((code: string) => {
    setLocality(code);
    setSelectedId(null);
  }, []);
  const selectReport = useCallback((id: string) => {
    setSelectedId(id);
    if (window.innerWidth < 1024)
      document.getElementById("report-map")?.scrollIntoView({ block: "start" });
  }, []);
  async function copyHash(id: string, hash: string) {
    try {
      await navigator.clipboard.writeText(hash);
      setCopied(id);
      setCopyError("");
    } catch {
      setCopyError(
        "No se pudo copiar. Puedes seleccionar el hash y copiarlo manualmente.",
      );
    }
  }

  return (
    <div className="min-h-screen">
      <header className="border-b bg-card">
        <div className="mx-auto flex h-16 max-w-[1440px] items-center justify-between px-4 sm:px-8">
          <a
            href="/dashboard/trazabilidad"
            className="flex items-center gap-2.5 text-sm font-semibold tracking-[0.12em]"
          >
            <img
              src="/antifraudi-inador-logo.png"
              alt=""
              className="size-8 rounded-lg object-contain"
            />
            ANTIFRAUDI-INADOR BOSA
          </a>
        </div>
      </header>
      <main className="mx-auto max-w-[1440px] space-y-5 px-4 py-7 sm:px-8">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h1 className="text-2xl font-semibold tracking-tight">
              Reportes en Bogotá
            </h1>
            <p className="mt-1.5 text-sm text-muted-foreground">
              Consulta la huella de cada reporte y su ubicación por localidad.
            </p>
          </div>
          <Button
            variant="outline"
            className="bg-card"
            disabled={loading}
            onClick={() => void refresh()}
          >
            <RefreshCw className={cn("size-4", loading && "animate-spin")} />
            Actualizar
          </Button>
        </div>
        {error && (
          <div
            role="alert"
            className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-red-900/70 bg-red-950/40 p-3 text-sm text-destructive"
          >
            <span>{error}</span>
            <Button size="sm" variant="outline" onClick={() => void refresh()}>
              Reintentar reportes
            </Button>
          </div>
        )}
        {geoError && (
          <div
            role="alert"
            className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-amber-800/70 bg-amber-950/40 p-3 text-sm"
          >
            <span>{geoError}</span>
            <Button
              size="sm"
              variant="outline"
              onClick={() => void fetchGeography()}
            >
              Reintentar mapa
            </Button>
          </div>
        )}
        <div className="grid items-start gap-5 lg:grid-cols-[350px_minmax(0,1fr)] xl:grid-cols-[380px_minmax(0,1fr)]">
          <Card
            className="gap-0 overflow-hidden py-0 shadow-none"
            aria-label="Lista de reportes"
          >
            <div className="space-y-3 border-b p-4">
              <div className="flex items-center justify-between">
                <h2 className="text-sm font-semibold">Reportes</h2>
                <Badge variant="secondary" className="font-normal">
                  {!loading && !error ? `${visible.length} registros` : "—"}
                </Badge>
              </div>
              <div className="relative">
                <Search className="absolute left-3 top-2.5 size-4 text-muted-foreground" />
                <Input
                  aria-label="Buscar reportes"
                  placeholder="Buscar título, hash o localidad…"
                  className="bg-muted/30 pl-9 text-xs"
                  value={query}
                  onChange={(event) => {
                    setQuery(event.target.value);
                    setSelectedId(null);
                  }}
                />
              </div>
              <Select
                value={locality}
                onValueChange={selectLocality}
                disabled={!geography}
              >
                <SelectTrigger
                  className="w-full text-xs"
                  aria-label="Filtrar por localidad"
                >
                  <SelectValue placeholder="Todas las localidades" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="all">Todas las localidades</SelectItem>
                  {geography?.localities.features.map(({ properties }) => (
                    <SelectItem key={properties.code} value={properties.code}>
                      {properties.name}
                    </SelectItem>
                  ))}
                  <SelectItem value="outside">Fuera de Bogotá</SelectItem>
                  <SelectItem value="missing">Sin ubicación</SelectItem>
                </SelectContent>
              </Select>
            </div>
            <div className="h-[420px] overflow-y-auto lg:h-[465px]">
              {loading ? (
                <div
                  role="status"
                  className="flex h-full items-center justify-center gap-2 text-sm text-muted-foreground"
                >
                  <LoaderCircle className="size-4 animate-spin" />
                  Cargando reportes…
                </div>
              ) : !visible.length ? (
                <div className="flex h-full flex-col items-center justify-center p-6 text-center">
                  <MapPin className="mb-3 size-7 text-primary/60" />
                  <h3 className="text-sm font-medium">
                    {error || geoError
                      ? "Consulta no disponible"
                      : reports.length
                        ? "No hay reportes para este filtro"
                        : "Aún no hay reportes"}
                  </h3>
                  <p className="mt-2 text-xs leading-5 text-muted-foreground">
                    {error || geoError
                      ? "Reintenta cargar los datos."
                      : "Los reportes registrados aparecerán aquí con su código hash."}
                  </p>
                  {(query || locality !== "all") && (
                    <Button
                      variant="ghost"
                      size="sm"
                      className="mt-3 text-primary"
                      onClick={() => {
                        setQuery("");
                        selectLocality("all");
                      }}
                    >
                      Limpiar filtros
                    </Button>
                  )}
                </div>
              ) : (
                <ul className="divide-y">
                  {visible.map((report) => (
                    <li
                      key={report.id}
                      className={cn(
                        "border-l-[3px] border-l-transparent p-4 transition-colors",
                        selectedId === report.id
                          ? "border-l-primary bg-red-950/40"
                          : "hover:bg-muted/50",
                      )}
                    >
                      <button
                        onClick={() => selectReport(report.id)}
                        aria-pressed={selectedId === report.id}
                        aria-label={`Ver reporte: ${report.title}`}
                        className="block w-full rounded text-left outline-offset-4 focus-visible:outline-2 focus-visible:outline-ring"
                      >
                        <span className="block truncate text-sm font-medium">
                          {report.title}
                        </span>
                        <span className="mt-1.5 flex flex-wrap items-center justify-between gap-1 text-[11px] text-muted-foreground">
                          <span className="flex items-center gap-1">
                            <MapPin className="size-3" />
                            {report.localityName}
                          </span>
                          <span>{date(report.created_at)}</span>
                        </span>
                      </button>
                      <div className="mt-3 flex items-start gap-2 rounded-md border bg-card/80 px-2.5 py-2">
                        <code className="min-w-0 flex-1 select-all break-all font-mono text-[10px] leading-[1.65] text-[#566174]">
                          {report.hash}
                        </code>
                        <Button
                          variant="ghost"
                          size="icon"
                          className="size-6 shrink-0 text-[#566174]"
                          aria-label={`Copiar hash de ${report.title}`}
                          onClick={() => void copyHash(report.id, report.hash)}
                        >
                          {copied === report.id ? (
                            <Check className="size-3 text-primary" />
                          ) : (
                            <Copy className="size-3" />
                          )}
                        </Button>
                      </div>
                    </li>
                  ))}
                </ul>
              )}
            </div>
            <div
              className="border-t px-4 py-3 text-[11px] text-muted-foreground"
              aria-live="polite"
            >
              {copyError ||
                (copied
                  ? "Hash copiado al portapapeles."
                  : "Selecciona un reporte para ubicarlo en el mapa.")}
            </div>
          </Card>
          <Card
            id="report-map"
            className="isolate min-w-0 scroll-mt-4 gap-0 overflow-hidden py-0 shadow-none"
          >
            <div className="flex flex-wrap items-center justify-between gap-2 border-b px-4 py-3.5">
              <h2 className="text-sm font-semibold">Mapa por localidades</h2>
              {locality !== "all" ? (
                <Button
                  variant="ghost"
                  size="sm"
                  className="h-6 text-xs"
                  onClick={() => selectLocality("all")}
                >
                  <X className="size-3" />
                  Quitar filtro
                </Button>
              ) : (
                <span className="text-xs text-muted-foreground">
                  20 localidades · Bogotá D. C.
                </span>
              )}
            </div>
            {geography ? (
              <Suspense
                fallback={
                  <div className="flex h-[450px] items-center justify-center text-sm text-muted-foreground sm:h-[560px]">
                    Cargando mapa…
                  </div>
                }
              >
                <BogotaMap
                  localities={geography.localities}
                  manifest={geography.manifest}
                  reports={loading || error ? [] : visible}
                  counts={loading || error ? {} : counts}
                  selectedId={selectedId}
                  selectedLocality={locality}
                  onSelectReport={selectReport}
                  onSelectLocality={selectLocality}
                />
              </Suspense>
            ) : (
              <div className="flex h-[450px] items-center justify-center bg-muted text-sm text-muted-foreground sm:h-[560px]">
                {geoError ? (
                  "Mapa no disponible"
                ) : (
                  <>
                    <LoaderCircle className="mr-2 size-4 animate-spin" />
                    Cargando cartografía…
                  </>
                )}
              </div>
            )}
          </Card>
        </div>
        {selectedReport?.storage === "mongodb" && (
          <ReportTracking key={selectedReport.id} report={selectedReport} />
        )}
        <Card
          className="gap-4 p-5 shadow-none"
          aria-label="Estadísticas por localidad"
        >
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div>
              <h2 className="text-sm font-semibold">Reportes por localidad</h2>
              <p className="mt-1 text-xs text-muted-foreground">
                {query
                  ? "Totales de la búsqueda actual. "
                  : "Totales de todos los reportes. "}
                Selecciona una localidad para filtrar.
              </p>
            </div>
            <span className="text-xs text-muted-foreground">
              {ready
                ? `${searched.filter((report) => !["outside", "missing"].includes(report.localityCode)).length} en Bogotá`
                : "—"}
            </span>
          </div>
          {!geography || loading || error ? (
            <p className="py-5 text-sm text-muted-foreground">
              {error || geoError
                ? "Estadísticas no disponibles."
                : "Cargando estadísticas…"}
            </p>
          ) : (
            <div className="grid grid-cols-2 gap-x-6 gap-y-2 md:grid-cols-4 xl:grid-cols-5">
              {stats.map((item) => (
                <button
                  key={item.code}
                  aria-label={`${item.name}: ${item.count} reportes`}
                  aria-pressed={locality === item.code}
                  onClick={() =>
                    selectLocality(locality === item.code ? "all" : item.code)
                  }
                  className={cn(
                    "rounded-md px-2 py-2.5 text-left transition-colors hover:bg-muted focus-visible:outline-2 focus-visible:outline-ring",
                    locality === item.code && "bg-red-950/40",
                  )}
                >
                  <span className="flex items-center justify-between gap-2 text-xs">
                    <span className="truncate">{item.name}</span>
                    <span className="font-semibold tabular-nums">
                      {item.count}
                    </span>
                  </span>
                  <span className="mt-2 block h-1 overflow-hidden rounded-full bg-muted">
                    <span
                      className="block h-full rounded-full bg-primary/65"
                      style={{ width: `${(item.count / maxCount) * 100}%` }}
                    />
                  </span>
                </button>
              ))}
            </div>
          )}
          {ready && Boolean(counts.outside || counts.missing) && (
            <div className="flex flex-wrap gap-4 border-t pt-3 text-xs text-muted-foreground">
              {Boolean(counts.outside) && (
                <button
                  className="underline underline-offset-4"
                  onClick={() => selectLocality("outside")}
                >
                  Fuera de Bogotá: {counts.outside}
                </button>
              )}
              {Boolean(counts.missing) && (
                <button
                  className="underline underline-offset-4"
                  onClick={() => selectLocality("missing")}
                >
                  Sin ubicación: {counts.missing}
                </button>
              )}
            </div>
          )}
        </Card>
      </main>
    </div>
  );
}
