import { jsPDF } from "jspdf";
import type { LocatedReport } from "@/lib/geography";

type ReportPdfInput = {
  reports: LocatedReport[];
  from: Date;
  to: Date;
};

const day = (value: Date) =>
  value.toLocaleDateString("es-CO", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  });

async function fingerprint(reports: LocatedReport[]) {
  if (!globalThis.crypto?.subtle) return null;
  const value = reports
    .map((report) => report.hash)
    .sort()
    .join("\n");
  const digest = await crypto.subtle.digest(
    "SHA-256",
    new TextEncoder().encode(value),
  );
  return Array.from(new Uint8Array(digest), (byte) =>
    byte.toString(16).padStart(2, "0"),
  ).join("");
}

export async function buildReportPdf({ reports, from, to }: ReportPdfInput) {
  const document = new jsPDF({ unit: "mm", format: "a4" });
  const margin = 16;
  const width = 210 - margin * 2;
  let y = 18;

  document.setFont("helvetica", "bold");
  document.setFontSize(18);
  document.text("Alerta Bosa - Informe de reportes", margin, y);
  y += 8;
  document.setFont("helvetica", "normal");
  document.setFontSize(10);
  document.text(
    `Periodo: ${day(from)} a ${day(to)}  |  Total: ${reports.length}`,
    margin,
    y,
  );
  y += 10;

  for (const [index, report] of reports.entries()) {
    const lines = document.splitTextToSize(
      `${index + 1}. ${report.title}\nLocalidad: ${report.localityName}\nFecha: ${new Date(report.created_at).toLocaleString("es-CO")}\nSHA-256: ${report.hash}`,
      width,
    ) as string[];
    const required = lines.length * 5 + 7;
    if (y + required > 282) {
      document.addPage();
      y = 18;
    }
    document.setFont("helvetica", "normal");
    document.setFontSize(9);
    document.text(lines, margin, y);
    y += required;
    document.setDrawColor(210);
    document.line(margin, y - 3, 210 - margin, y - 3);
  }

  const reportFingerprint = await fingerprint(reports);
  if (reportFingerprint) {
    if (y + 18 > 282) {
      document.addPage();
      y = 18;
    }
    document.setFontSize(8);
    document.text(
      document.splitTextToSize(
        `Huella del informe: ${reportFingerprint}`,
        width,
      ),
      margin,
      y,
    );
  }

  const stamp = `${from.toISOString().slice(0, 10)}_${to.toISOString().slice(0, 10)}`;
  return {
    blob: document.output("blob"),
    fileName: `alerta-bosa_${stamp}.pdf`,
    count: reports.length,
    fingerprint: reportFingerprint,
  };
}
