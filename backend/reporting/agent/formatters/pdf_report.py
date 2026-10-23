"""Generación de informe en PDF en español mediante ReportLab."""

import hashlib
import html
from pathlib import Path
from typing import List

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import HRFlowable, KeepTogether, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from backend.reporting.agent.metrics import MetricsReport
from backend.reporting.agent.narrative import GeneratedNarrative


def escape_p(text: str) -> str:
    """Escapa caracteres HTML para que ReportLab no falle con tags no cerrados o caracteres reservados."""
    if not text:
        return ""
    return html.escape(str(text))


from reportlab.pdfgen import canvas


class NumberedCanvas(canvas.Canvas):
    """Canvas auxiliar para imprimir pie de página con número total de páginas e identificador."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._saved_page_states = []

    def showPage(self):
        self._saved_page_states.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        num_pages = len(self._saved_page_states)
        for state in self._saved_page_states:
            self.__dict__.update(state)
            self.draw_page_number(num_pages)
            super().showPage()
        super().save()

    def draw_page_number(self, page_count):
        self.saveState()
        self.setFont("Helvetica", 8)
        self.setFillColor(colors.HexColor("#64748B"))
        # Línea de pie de página
        self.setStrokeColor(colors.HexColor("#CBD5E1"))
        self.setLineWidth(0.5)
        self.line(40, 30, letter[0] - 40, 30)
        # Texto pie de página
        text_left = "Centinela Antifraude - Informe Confidencial de Trazabilidad"
        text_right = f"Página {self._pageNumber} de {page_count}"
        self.drawString(40, 20, text_left)
        self.drawRightString(letter[0] - 40, 20, text_right)
        self.restoreState()



def build_pdf_report(
    report_id: str,
    metrics: MetricsReport,
    narrative: GeneratedNarrative,
    output_path: Path,
    allow_unmasked_indicators: bool = False,
) -> str:
    """Genera el documento PDF formal en español y devuelve su hash SHA-256."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    doc = SimpleDocTemplate(
        str(output_path),
        pagesize=letter,
        leftMargin=40,
        rightMargin=40,
        topMargin=40,
        bottomMargin=45,
    )

    styles = getSampleStyleSheet()

    # Estilos personalizados
    title_style = ParagraphStyle(
        "DocTitle",
        parent=styles["Heading1"],
        fontName="Helvetica-Bold",
        fontSize=20,
        leading=24,
        textColor=colors.HexColor("#0F172A"),
        spaceAfter=4,
    )
    subtitle_style = ParagraphStyle(
        "DocSubtitle",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=10,
        leading=14,
        textColor=colors.HexColor("#475569"),
        spaceAfter=12,
    )
    h2_style = ParagraphStyle(
        "Heading2_Custom",
        parent=styles["Heading2"],
        fontName="Helvetica-Bold",
        fontSize=12,
        leading=16,
        textColor=colors.HexColor("#1E3A8A"),
        spaceBefore=12,
        spaceAfter=6,
    )
    body_style = ParagraphStyle(
        "Body_Custom",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=9,
        leading=13,
        textColor=colors.HexColor("#1E293B"),
        spaceAfter=6,
    )
    disclaimer_style = ParagraphStyle(
        "Disclaimer",
        parent=styles["Normal"],
        fontName="Helvetica-Oblique",
        fontSize=8,
        leading=11,
        textColor=colors.HexColor("#64748B"),
        spaceAfter=10,
    )
    table_cell_style = ParagraphStyle(
        "TableCell",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=8,
        leading=10,
        textColor=colors.HexColor("#0F172A"),
    )
    table_cell_bold = ParagraphStyle(
        "TableCellBold",
        parent=table_cell_style,
        fontName="Helvetica-Bold",
    )

    elements = []

    # 1. Cabecera y Metadatos del Informe
    elements.append(Paragraph("INFORME ANALÍTICO DE POSIBLES FRAUDES", title_style))
    meta_info = (
        f"<b>Identificador:</b> {escape_p(report_id)} | "
        f"<b>Período:</b> {metrics.window['start_date']} al {metrics.window['end_date']} | "
        f"<b>Zona Horaria:</b> {escape_p(metrics.window.get('timezone', 'America/Bogota'))}<br/>"
        f"<b>Fecha de Generación:</b> {metrics.generated_at_utc} UTC | "
        f"<b>Metodología:</b> Taxonomía v1.0 ({narrative.method})"
    )
    elements.append(Paragraph(meta_info, subtitle_style))
    elements.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor("#CBD5E1"), spaceAfter=10))

    # 2. Descargo de Responsabilidad (Disclaimer)
    disclaimer_text = (
        "<b>Nota legal y de uso:</b> El presente informe describe patrones de comportamiento observados en denuncias "
        "ciudadanas de posibles fraudes. No debe interpretarse como prueba definitiva de culpabilidad ni imputación "
        "directa contra las personas, entidades o dominios referenciados en los mensajes denunciados."
    )
    elements.append(Paragraph(disclaimer_text, disclaimer_style))

    # 3. Resumen Ejecutivo
    elements.append(Paragraph("1. Resumen Ejecutivo", h2_style))
    elements.append(Paragraph(escape_p(narrative.executive_summary), body_style))

    # 4. Calidad y Cobertura de Datos
    elements.append(Paragraph("2. Calidad y Cobertura de Datos", h2_style))
    cov_data = [
        [Paragraph("Métrica de Cobertura", table_cell_bold), Paragraph("Cantidad", table_cell_bold), Paragraph("Porcentaje (con denominador)", table_cell_bold)],
        [
            Paragraph("Total de reportes en el período", table_cell_style),
            Paragraph(str(metrics.total_reports_in_period), table_cell_style),
            Paragraph(f"{metrics.total_reports_in_period} / {metrics.total_reports_in_period} (100.0%)", table_cell_style),
        ],
        [
            Paragraph("Reportes con contenido utilizable (messageContent)", table_cell_style),
            Paragraph(str(metrics.usable_message_reports), table_cell_style),
            Paragraph(
                f"{metrics.usable_message_reports} / {metrics.total_reports_in_period} "
                f"({round(metrics.usable_message_reports / max(metrics.total_reports_in_period, 1) * 100, 1)}%)",
                table_cell_style,
            ),
        ],
        [
            Paragraph("Reportes legacy (sin messageContent / sólo description)", table_cell_style),
            Paragraph(str(metrics.empty_or_legacy_message_reports), table_cell_style),
            Paragraph(
                f"{metrics.empty_or_legacy_message_reports} / {metrics.total_reports_in_period} "
                f"({round(metrics.empty_or_legacy_message_reports / max(metrics.total_reports_in_period, 1) * 100, 1)}%)",
                table_cell_style,
            ),
        ],
        [
            Paragraph("Reportes excluidos por datos o hash alterado", table_cell_style),
            Paragraph(str(metrics.invalid_data_reports), table_cell_style),
            Paragraph(
                f"{metrics.invalid_data_reports} / {metrics.total_reports_in_period} "
                f"({round(metrics.invalid_data_reports / max(metrics.total_reports_in_period, 1) * 100, 1)}%)",
                table_cell_style,
            ),
        ],
    ]
    t_cov = Table(cov_data, colWidths=[200, 80, 220])
    t_cov.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#F1F5F9")),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#CBD5E1")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
    ]))
    elements.append(t_cov)

    # 5. Rankings de Mensajes Repetidos (Normalizados)
    elements.append(Paragraph("3. Mensajes Más Reiterados (Normalizados)", h2_style))
    if not metrics.normalized_messages_ranking:
        elements.append(Paragraph("<i>No se encontraron mensajes de texto utilizables en el período analizado.</i>", body_style))
    else:
        msg_table_data = [
            [
                Paragraph("#", table_cell_bold),
                Paragraph("Muestra del Mensaje (Anonimizada)", table_cell_bold),
                Paragraph("Frecuencia", table_cell_bold),
                Paragraph("Participación", table_cell_bold),
            ]
        ]
        for idx, m in enumerate(metrics.normalized_messages_ranking[:5], 1):
            msg_table_data.append([
                Paragraph(str(idx), table_cell_style),
                Paragraph(escape_p(m.text_sample_anonymized), table_cell_style),
                Paragraph(str(m.count), table_cell_style),
                Paragraph(f"{m.count} / {m.denominator} ({m.percentage}%)", table_cell_style),
            ])
        t_msg = Table(msg_table_data, colWidths=[25, 325, 60, 90])
        t_msg.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#F1F5F9")),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#CBD5E1")),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
        ]))
        elements.append(t_msg)

    # 6. Direcciones de Correo y Dominios
    elements.append(Paragraph("4. Direcciones de Correo y Dominios Mencionados", h2_style))
    if not metrics.emails_ranking and not metrics.domains_ranking:
        elements.append(Paragraph("<i>No se extrajeron direcciones de correo electrónico en los reportes del período.</i>", body_style))
    else:
        em_table_data = [
            [
                Paragraph("Dirección", table_cell_bold),
                Paragraph("Reportes Distintos", table_cell_bold),
                Paragraph("Apariciones Totales", table_cell_bold),
            ]
        ]
        for em in metrics.emails_ranking[:5]:
            display_email = em.email if allow_unmasked_indicators else em.email_masked
            em_table_data.append([
                Paragraph(escape_p(display_email), table_cell_style),
                Paragraph(f"{em.distinct_reports_count} / {em.denominator} ({em.distinct_reports_pct}%)", table_cell_style),
                Paragraph(str(em.total_occurrences), table_cell_style),
            ])
        t_em = Table(em_table_data, colWidths=[250, 150, 100])
        t_em.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#F1F5F9")),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#CBD5E1")),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
        ]))
        elements.append(t_em)

    # 7. Categorías Heurísticas de Fraude (Taxonomía v1.0)
    elements.append(Paragraph("5. Distribución por Categorías Analíticas (Taxonomía v1.0)", h2_style))
    cat_note = "<i>Nota: La clasificación es multi-etiqueta; un reporte puede coincidir con varias categorías.</i>"
    elements.append(Paragraph(cat_note, body_style))
    if not metrics.categories_ranking:
        elements.append(Paragraph("<i>No se clasificaron reportes en el período.</i>", body_style))
    else:
        cat_table_data = [
            [
                Paragraph("Categoría Analítica", table_cell_bold),
                Paragraph("Reportes", table_cell_bold),
                Paragraph("Porcentaje", table_cell_bold),
                Paragraph("Evidencia Léxica Muestra", table_cell_bold),
            ]
        ]
        for cat in metrics.categories_ranking:
            evidence_str = ", ".join(cat.sample_evidence[:3]) if cat.sample_evidence else "N/A"
            cat_table_data.append([
                Paragraph(escape_p(cat.category), table_cell_style),
                Paragraph(str(cat.reports_count), table_cell_style),
                Paragraph(f"{cat.reports_count} / {cat.denominator} ({cat.percentage}%)", table_cell_style),
                Paragraph(escape_p(evidence_str), table_cell_style),
            ])
        t_cat = Table(cat_table_data, colWidths=[150, 60, 110, 180])
        t_cat.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#F1F5F9")),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#CBD5E1")),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
        ]))
        elements.append(t_cat)

    # 8. Estados de Seguimiento al Corte
    elements.append(Paragraph("6. Estado de Seguimiento de los Casos al Corte", h2_style))
    if not metrics.tracking_status_distribution:
        elements.append(Paragraph("<i>Sin datos de seguimiento.</i>", body_style))
    else:
        st_table_data = [
            [
                Paragraph("Estado al Límite del Período", table_cell_bold),
                Paragraph("Reportes", table_cell_bold),
                Paragraph("Porcentaje", table_cell_bold),
            ]
        ]
        for st in metrics.tracking_status_distribution:
            st_table_data.append([
                Paragraph(escape_p(st.status), table_cell_style),
                Paragraph(str(st.count), table_cell_style),
                Paragraph(f"{st.count} / {st.denominator} ({st.percentage}%)", table_cell_style),
            ])
        t_st = Table(st_table_data, colWidths=[200, 100, 200])
        t_st.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#F1F5F9")),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#CBD5E1")),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
        ]))
        elements.append(t_st)

    # 9. Metodología, Limitaciones y Recomendaciones
    elements.append(Paragraph("7. Metodología, Limitaciones y Recomendaciones", h2_style))
    rec_text = (
        "<b>Metodología:</b> Los datos fueron consultados en modo de solo lectura desde MongoDB en UTC. "
        "Las métricas son agregaciones deterministas sin inferencia de valores faltantes. "
        "La normalización textual unifica caracteres Unicode mediante NFKC y colapsa espacios.<br/><br/>"
        "<b>Limitaciones:</b> Los reportes legacy sin <code>messageContent</code> no forman parte del ranking de "
        "mensajes originales. La presencia de direcciones de correo refleja menciones en el texto denunciado y no "
        "identifica al remitente auténtico ni a un atacante comprobado.<br/><br/>"
        "<b>Recomendaciones:</b> Priorizar la divulgación preventiva sobre los patrones de mensajes más reiterados "
        "y reforzar los canales de verificación con las entidades bancarias o institucionales mencionadas."
    )
    elements.append(Paragraph(rec_text, body_style))

    # Construir el documento con NumberedCanvas
    doc.build(elements, canvasmaker=NumberedCanvas)

    # Calcular SHA-256 del PDF generado
    pdf_bytes = output_path.read_bytes()
    return hashlib.sha256(pdf_bytes).hexdigest()
