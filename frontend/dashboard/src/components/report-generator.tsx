import { useEffect, useMemo, useState } from "react";
import type { DateRange } from "react-day-picker";
import { endOfDay, format, startOfDay, startOfMonth, subDays, subMonths } from "date-fns";
import { es } from "date-fns/locale";
import { CalendarIcon, Download, ExternalLink, FileText, LoaderCircle, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Calendar } from "@/components/ui/calendar";
import { Card } from "@/components/ui/card";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import type { LocatedReport } from "@/lib/geography";
import { cn } from "@/lib/utils";

type GeneratedPdf = {
  url: string;
  fileName: string;
  period: string;
  count: number;
  fingerprint: string | null;
};

const shortDate = (date: Date) => format(date, "d MMM yyyy", { locale: es });
const lastDays = (days: number): DateRange => ({ from: subDays(new Date(), days - 1), to: new Date() });
const presets = [
  { label: "Últimos 7 días", range: () => lastDays(7) },
  { label: "Últimos 30 días", range: () => lastDays(30) },
  { label: "Este mes", range: (): DateRange => ({ from: startOfMonth(new Date()), to: new Date() }) },
];

/** Rango de fechas + informe PDF con vista previa (vista Admin / Informes). */
export function ReportGenerator({ reports, ready }: { reports: LocatedReport[]; ready: boolean }) {
  const [range, setRange] = useState<DateRange | undefined>(() => lastDays(30));
  const [open, setOpen] = useState(false);
  const [months] = useState(() => (window.matchMedia("(min-width: 640px)").matches ? 2 : 1));
  const [generating, setGenerating] = useState(false);
  const [error, setError] = useState("");
  const [pdf, setPdf] = useState<GeneratedPdf | null>(null);

  // El rango incluye el día completo: desde las 00:00 del inicio hasta las 23:59 del final.
  const bounds = useMemo(
    () => (range?.from ? { from: startOfDay(range.from), to: endOfDay(range.to ?? range.from) } : null),
    [range],
  );
  const inRange = useMemo(() => {
    if (!bounds) return [];
    const start = bounds.from.getTime();
    const end = bounds.to.getTime();
    return reports.filter((report) => {
      const time = new Date(report.created_at).getTime();
      return time >= start && time <= end;
    });
  }, [reports, bounds]);

  // Libera la memoria del PDF anterior al generar otro o al salir de Informes.
  useEffect(() => () => {
    if (pdf) URL.revokeObjectURL(pdf.url);
  }, [pdf]);

  async function generate() {
    if (!bounds || !inRange.length || generating) return;
    setGenerating(true);
    setError("");
    try {
      const { buildReportPdf } = await import("@/lib/report-pdf");
      const result = await buildReportPdf({ reports: inRange, from: bounds.from, to: bounds.to });
      setPdf({
        url: URL.createObjectURL(result.blob),
        fileName: result.fileName,
        period: `${shortDate(bounds.from)} – ${shortDate(bounds.to)}`,
        count: result.count,
        fingerprint: result.fingerprint,
      });
    } catch {
      setError("No se pudo generar el PDF. Vuelve a intentarlo.");
    } finally {
      setGenerating(false);
    }
  }

  function download() {
    if (!pdf) return;
    const link = document.createElement("a");
    link.href = pdf.url;
    link.download = pdf.fileName;
    document.body.append(link);
    link.click();
    link.remove();
  }

  const status = !ready
    ? "Cargando reportes…"
    : !bounds
      ? "Elige un rango para ver cuántos reportes incluye."
      : inRange.length
        ? `${inRange.length} ${inRange.length === 1 ? "reporte" : "reportes"} en el rango`
        : "No hay reportes en ese rango.";

  return (
    <Card className="gap-4 p-5 shadow-none" aria-labelledby="report-generator-title">
      <div>
        <h2 id="report-generator-title" className="text-sm font-semibold">
          Generar informe por fechas
        </h2>
        <p className="mt-1 text-xs text-muted-foreground">
          Elige un rango y genera un PDF con los reportes de ese periodo, su localidad y su huella SHA-256.
        </p>
      </div>

      <div className="flex flex-wrap items-end gap-3">
        <div className="space-y-1.5">
          <label htmlFor="report-range" className="block text-xs font-medium text-muted-foreground">
            Rango de fechas
          </label>
          <Popover open={open} onOpenChange={setOpen}>
            <PopoverTrigger asChild>
              <Button
                id="report-range"
                variant="outline"
                className={cn(
                  "group w-[290px] max-w-full justify-between bg-background px-3 font-normal outline-offset-0 hover:bg-background focus-visible:border-ring",
                  !range?.from && "text-muted-foreground",
                )}
              >
                <span className="truncate">
                  {range?.from
                    ? range.to
                      ? `${shortDate(range.from)} – ${shortDate(range.to)}`
                      : shortDate(range.from)
                    : "Elige un rango de fechas"}
                </span>
                <CalendarIcon
                  size={16}
                  strokeWidth={2}
                  className="shrink-0 text-muted-foreground/80 transition-colors group-hover:text-foreground"
                  aria-hidden="true"
                />
              </Button>
            </PopoverTrigger>
            <PopoverContent className="w-auto p-2" align="start">
              <Calendar
                mode="range"
                selected={range}
                onSelect={setRange}
                numberOfMonths={months}
                defaultMonth={months === 2 ? subMonths(range?.to ?? new Date(), 1) : (range?.to ?? new Date())}
                locale={es}
                disabled={{ after: new Date() }}
              />
              <div className="mt-2 flex flex-wrap gap-1 border-t border-border px-1 pt-2">
                {presets.map((preset) => (
                  <Button key={preset.label} size="xs" variant="ghost" onClick={() => setRange(preset.range())}>
                    {preset.label}
                  </Button>
                ))}
              </div>
            </PopoverContent>
          </Popover>
        </div>
        <Button onClick={() => void generate()} disabled={!ready || !inRange.length || generating}>
          {generating ? <LoaderCircle className="animate-spin" aria-hidden="true" /> : <FileText aria-hidden="true" />}
          {generating ? "Generando…" : "Generar PDF"}
        </Button>
        <p className="pb-2 text-xs text-muted-foreground" aria-live="polite">
          {status}
        </p>
      </div>

      {error && (
        <p role="alert" className="text-xs text-destructive">
          {error}
        </p>
      )}

      {pdf && (
        <div className="overflow-hidden rounded-lg border">
          <div className="flex flex-wrap items-center justify-between gap-2 border-b bg-muted/40 px-3 py-2">
            <div className="min-w-0">
              <p className="truncate text-xs font-medium">{pdf.fileName}</p>
              <p className="text-[11px] text-muted-foreground">
                {pdf.period} · {pdf.count} {pdf.count === 1 ? "reporte" : "reportes"}
              </p>
            </div>
            <div className="flex items-center gap-1">
              <Button size="sm" onClick={download}>
                <Download aria-hidden="true" /> Descargar PDF
              </Button>
              <Button size="sm" variant="ghost" onClick={() => window.open(pdf.url, "_blank", "noopener")}>
                <ExternalLink aria-hidden="true" /> Abrir
              </Button>
              <Button size="icon-sm" variant="ghost" aria-label="Cerrar vista previa" onClick={() => setPdf(null)}>
                <X aria-hidden="true" />
              </Button>
            </div>
          </div>
          {/* Visor PDF del navegador: páginas, zoom, impresión y descarga, sin dependencias extra. */}
          <iframe
            title={`Vista previa: ${pdf.fileName}`}
            src={pdf.url}
            className="block h-[70vh] min-h-[480px] w-full bg-white"
          />
          <p className="border-t px-3 py-2 text-[11px] text-muted-foreground">
            Si no ves la vista previa (algunos celulares no muestran PDF dentro de la página), usa «Descargar PDF».
            {pdf.fingerprint && (
              <span className="mt-1 block break-all font-mono text-[10px]">Huella del informe: {pdf.fingerprint}</span>
            )}
          </p>
        </div>
      )}
    </Card>
  );
}
