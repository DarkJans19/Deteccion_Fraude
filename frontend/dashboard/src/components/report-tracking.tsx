import { useCallback, useEffect, useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import {
  api,
  caseStatuses,
  date,
  type CaseStatus,
  type Report,
  type Session,
  type Tracking,
} from "@/lib/reports";

export function ReportTracking({ report }: { report: Report }) {
  const [tracking, setTracking] = useState<Tracking | null>(null);
  const [error, setError] = useState("");
  const [status, setStatus] =
    useState<Exclude<CaseStatus, "registrado">>("en_revision");
  const [note, setNote] = useState("");
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const requestId = useRef(0);
  const refresh = useCallback(async () => {
    const current = ++requestId.current;
    setError("");
    try {
      const result = await api<Tracking>(`/api/reports/${report.id}/tracking`);
      if (current === requestId.current) setTracking(result);
    } catch (failure) {
      if (current === requestId.current) {
        setTracking(null);
        setError((failure as Error).message);
      }
    }
  }, [report.id]);

  useEffect(() => {
    void refresh();
    return () => {
      requestId.current += 1;
    };
  }, [refresh]);

  async function save(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSaving(true);
    setError("");
    setSaved(false);
    try {
      const session = await api<Session>("/api/session");
      await api(`/api/reports/${report.id}/tracking`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-CSRF-Token": session.csrf,
        },
        body: JSON.stringify({ status, note }),
      });
      setNote("");
      setSaved(true);
      await refresh();
    } catch (failure) {
      setError((failure as Error).message);
    } finally {
      setSaving(false);
    }
  }

  return (
    <Card
      className="gap-4 p-5 shadow-none"
      role="region"
      aria-label="Seguimiento del caso"
    >
      <div>
        <h2 className="text-sm font-semibold">Seguimiento del caso</h2>
        <p className="mt-1 break-words text-sm">
          {report.description ?? report.title}
        </p>
        <p className="mt-2 break-all text-xs text-muted-foreground">
          Caso {report.id}
        </p>
        {report.risk_level && (
          <p className="mt-2 text-sm">
            Resultado del análisis: <strong>{report.risk_level}</strong>
          </p>
        )}
      </div>
      <section aria-label="Contenido completo del mensaje">
        <h3 className="text-sm font-semibold">
          Contenido completo del mensaje
        </h3>
        <p className="mt-2 whitespace-pre-wrap break-words rounded-md border bg-muted/30 p-3 text-sm">
          {report.text ||
            "Este reporte anterior no contiene el mensaje original."}
        </p>
      </section>
      {error && (
        <div role="alert" className="text-sm text-destructive">
          {error}
          <Button variant="ghost" size="sm" onClick={() => void refresh()}>
            Reintentar seguimiento
          </Button>
        </div>
      )}
      {tracking ? (
        <>
          <p className="text-sm">
            Estado actual: <strong>{caseStatuses[tracking.status]}</strong>
          </p>
          <ol
            className="space-y-3 border-l pl-4 text-sm"
            aria-label="Historial del caso"
          >
            <li>
              <p className="font-medium">Registrado</p>
              <p className="text-xs text-muted-foreground">
                {date(tracking.registeredAt)}
              </p>
            </li>
            {tracking.events.map((event) => (
              <li key={event._id.$oid}>
                <p className="font-medium">{caseStatuses[event.status]}</p>
                <p className="text-xs text-muted-foreground">
                  {date(event.date.$date)}
                </p>
                <p className="mt-1 whitespace-pre-wrap break-words">
                  {event.note}
                </p>
              </li>
            ))}
          </ol>
          <form
            onSubmit={(event) => void save(event)}
            className="space-y-3 border-t pt-4"
          >
            <label className="block space-y-1 text-sm">
              <span>Estado del caso</span>
              <select
                className="block w-full rounded-md border bg-background p-2"
                value={status}
                onChange={(event) =>
                  setStatus(event.target.value as typeof status)
                }
                disabled={saving}
              >
                {Object.entries(caseStatuses)
                  .filter(([key]) => key !== "registrado")
                  .map(([key, label]) => (
                    <option key={key} value={key}>
                      {label}
                    </option>
                  ))}
              </select>
            </label>
            <label className="block space-y-1 text-sm">
              <span>Nota de seguimiento</span>
              <textarea
                className="block min-h-24 w-full rounded-md border bg-background p-2"
                required
                maxLength={5000}
                value={note}
                onChange={(event) => {
                  setNote(event.target.value);
                  setSaved(false);
                }}
                disabled={saving}
              />
            </label>
            <Button type="submit" disabled={saving || !note.trim()}>
              {saving ? "Guardando…" : "Guardar seguimiento"}
            </Button>
            {saved && (
              <p role="status" className="text-sm">
                Seguimiento guardado.
              </p>
            )}
          </form>
        </>
      ) : (
        !error && (
          <p className="text-sm text-muted-foreground">Cargando seguimiento…</p>
        )
      )}
    </Card>
  );
}
