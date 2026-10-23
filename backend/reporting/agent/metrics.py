"""Cálculo verificable y determinista de métricas sobre reportes de fraude."""

from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set

from bson import ObjectId

from backend.reporting.agent.date_rules import QueryWindow
from backend.reporting.agent.normalization import (
    anonymize_text,
    extract_domain_from_email,
    extract_emails_from_text,
    mask_email,
    normalize_text,
)
from backend.reporting.agent.taxonomy import classify_text
from backend.reporting.agent.tracking import get_latest_status_at_cutoff


@dataclass
class MessageGroupMetric:
    text_sample: str
    text_sample_anonymized: str
    count: int
    percentage: float
    denominator: int
    first_seen: str
    last_seen: str


@dataclass
class EmailMetric:
    email: str
    email_masked: str
    distinct_reports_count: int
    distinct_reports_pct: float
    total_occurrences: int
    denominator: int
    first_seen: str
    last_seen: str


@dataclass
class DomainMetric:
    domain: str
    distinct_reports_count: int
    distinct_reports_pct: float
    total_occurrences: int
    denominator: int


@dataclass
class CategoryMetric:
    category: str
    reports_count: int
    percentage: float
    denominator: int
    sample_evidence: List[str]


@dataclass
class DailyTrend:
    date_str: str
    count: int


@dataclass
class StatusMetric:
    status: str
    count: int
    percentage: float
    denominator: int


@dataclass
class MetricsReport:
    # Parámetros y metadatos
    window: Dict[str, Any]
    generated_at_utc: str

    # A. Cobertura y calidad
    total_reports_in_period: int
    usable_message_reports: int
    empty_or_legacy_message_reports: int
    invalid_data_reports: int
    exclusions_by_reason: Dict[str, int]

    # B. Mensajes repetidos
    exact_messages_ranking: List[MessageGroupMetric]
    normalized_messages_ranking: List[MessageGroupMetric]
    legacy_description_ranking: List[MessageGroupMetric]

    # C. Correos y dominios
    emails_ranking: List[EmailMetric]
    domains_ranking: List[DomainMetric]

    # D. Categorías (Taxonomía v1.0)
    categories_ranking: List[CategoryMetric]

    # E. Evolución temporal
    daily_distribution: List[DailyTrend]
    peak_day: Optional[str]
    peak_count: int

    # F. Estados de seguimiento
    tracking_status_distribution: List[StatusMetric]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def compute_metrics(
    valid_reports: List[Dict[str, Any]],
    invalid_reports: List[Dict[str, Any]],
    all_events: List[Dict[str, Any]],
    window: QueryWindow,
) -> MetricsReport:
    """Calcula todas las métricas reproducibles a partir de datos reales."""
    now_iso = datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    total_valid = len(valid_reports)
    total_invalid = len(invalid_reports)
    total_in_period = total_valid + total_invalid

    exclusions = defaultdict(int)
    if total_invalid > 0:
        for inv in invalid_reports:
            reason = inv.get("exclusion_reason", "Dato o hash inválido")
            exclusions[reason] += 1

    # Agrupaciones para mensajes
    usable_reports = []
    legacy_reports = []

    for r in valid_reports:
        msg = r.get("messageContent")
        if isinstance(msg, str) and msg.strip():
            usable_reports.append(r)
        else:
            legacy_reports.append(r)

    usable_count = len(usable_reports)
    legacy_count = len(legacy_reports)

    if legacy_count > 0:
        exclusions["Documentos sin messageContent utilizable (excluidos del ranking de mensajes originales)"] = legacy_count

    # -------------------------------------------------------------------------
    # B. Mensajes repetidos
    # -------------------------------------------------------------------------
    # 1. Coincidencia exacta
    exact_groups = defaultdict(list)
    for r in usable_reports:
        exact_groups[r["messageContent"]].append(r)

    exact_ranking: List[MessageGroupMetric] = []
    for text_content, group in sorted(exact_groups.items(), key=lambda x: len(x[1]), reverse=True):
        count = len(group)
        dates = [r["date"] for r in group if isinstance(r.get("date"), datetime)]
        first_s = min(dates).isoformat() if dates else ""
        last_s = max(dates).isoformat() if dates else ""
        pct = round((count / usable_count * 100), 2) if usable_count > 0 else 0.0
        exact_ranking.append(
            MessageGroupMetric(
                text_sample=text_content[:200],
                text_sample_anonymized=anonymize_text(text_content[:200]),
                count=count,
                percentage=pct,
                denominator=usable_count,
                first_seen=first_s,
                last_seen=last_s,
            )
        )

    # 2. Coincidencia normalizada (NFKC, espacios unificados, minúsculas)
    norm_groups = defaultdict(list)
    for r in usable_reports:
        norm_key = normalize_text(r["messageContent"])
        norm_groups[norm_key].append(r)

    norm_ranking: List[MessageGroupMetric] = []
    for norm_key, group in sorted(norm_groups.items(), key=lambda x: len(x[1]), reverse=True):
        count = len(group)
        dates = [r["date"] for r in group if isinstance(r.get("date"), datetime)]
        first_s = min(dates).isoformat() if dates else ""
        last_s = max(dates).isoformat() if dates else ""
        pct = round((count / usable_count * 100), 2) if usable_count > 0 else 0.0
        # Tomar como muestra el primer texto original del grupo
        sample_orig = group[0]["messageContent"]
        norm_ranking.append(
            MessageGroupMetric(
                text_sample=sample_orig[:200],
                text_sample_anonymized=anonymize_text(sample_orig[:200]),
                count=count,
                percentage=pct,
                denominator=usable_count,
                first_seen=first_s,
                last_seen=last_s,
            )
        )

    # 3. Análisis separado de description para denuncias legacy
    legacy_desc_groups = defaultdict(list)
    for r in legacy_reports:
        desc = r.get("description", "")
        if desc.strip():
            legacy_desc_groups[normalize_text(desc)].append(r)

    legacy_ranking: List[MessageGroupMetric] = []
    for norm_desc, group in sorted(legacy_desc_groups.items(), key=lambda x: len(x[1]), reverse=True):
        count = len(group)
        dates = [r["date"] for r in group if isinstance(r.get("date"), datetime)]
        first_s = min(dates).isoformat() if dates else ""
        last_s = max(dates).isoformat() if dates else ""
        pct = round((count / legacy_count * 100), 2) if legacy_count > 0 else 0.0
        sample_desc = group[0].get("description", "")
        legacy_ranking.append(
            MessageGroupMetric(
                text_sample=sample_desc[:200],
                text_sample_anonymized=anonymize_text(sample_desc[:200]),
                count=count,
                percentage=pct,
                denominator=legacy_count,
                first_seen=first_s,
                last_seen=last_s,
            )
        )

    # -------------------------------------------------------------------------
    # C. Direcciones de correo y dominios
    # -------------------------------------------------------------------------
    # Extracción por reporte (para reportes distintos) y total de apariciones
    email_reports_map = defaultdict(set)
    email_total_counter = Counter()
    email_dates = defaultdict(list)

    domain_reports_map = defaultdict(set)
    domain_total_counter = Counter()

    for idx, r in enumerate(valid_reports):
        report_key = str(r.get("_id", idx))
        # Extraer de messageContent si existe, y de description
        text_to_scan = f"{r.get('messageContent', '')} {r.get('description', '')}"
        extracted = extract_emails_from_text(text_to_scan)
        r_date = r.get("date")

        for em in extracted:
            email_reports_map[em].add(report_key)
            email_total_counter[em] += 1
            if isinstance(r_date, datetime):
                email_dates[em].append(r_date)

            dom = extract_domain_from_email(em)
            if dom:
                domain_reports_map[dom].add(report_key)
                domain_total_counter[dom] += 1

    email_ranking: List[EmailMetric] = []
    for em, report_set in sorted(email_reports_map.items(), key=lambda x: len(x[1]), reverse=True):
        rep_count = len(report_set)
        tot_occ = email_total_counter[em]
        dates = email_dates[em]
        first_s = min(dates).isoformat() if dates else ""
        last_s = max(dates).isoformat() if dates else ""
        pct = round((rep_count / total_valid * 100), 2) if total_valid > 0 else 0.0
        email_ranking.append(
            EmailMetric(
                email=em,
                email_masked=mask_email(em),
                distinct_reports_count=rep_count,
                distinct_reports_pct=pct,
                total_occurrences=tot_occ,
                denominator=total_valid,
                first_seen=first_s,
                last_seen=last_s,
            )
        )

    domain_ranking: List[DomainMetric] = []
    for dom, report_set in sorted(domain_reports_map.items(), key=lambda x: len(x[1]), reverse=True):
        rep_count = len(report_set)
        tot_occ = domain_total_counter[dom]
        pct = round((rep_count / total_valid * 100), 2) if total_valid > 0 else 0.0
        domain_ranking.append(
            DomainMetric(
                domain=dom,
                distinct_reports_count=rep_count,
                distinct_reports_pct=pct,
                total_occurrences=tot_occ,
                denominator=total_valid,
            )
        )

    # -------------------------------------------------------------------------
    # D. Categorías (Taxonomía v1.0)
    # -------------------------------------------------------------------------
    category_counts = Counter()
    category_evidence = defaultdict(set)

    for r in valid_reports:
        # Clasificar sobre messageContent si existe, o description en legacy
        content_for_cat = r.get("messageContent") or r.get("description", "")
        matches = classify_text(content_for_cat)
        for m in matches:
            category_counts[m.category] += 1
            for ev in m.evidence:
                category_evidence[m.category].add(ev)

    categories_ranking: List[CategoryMetric] = []
    for cat_name, c_count in category_counts.most_common():
        pct = round((c_count / total_valid * 100), 2) if total_valid > 0 else 0.0
        categories_ranking.append(
            CategoryMetric(
                category=cat_name,
                reports_count=c_count,
                percentage=pct,
                denominator=total_valid,
                sample_evidence=sorted(list(category_evidence[cat_name]))[:5],
            )
        )

    # -------------------------------------------------------------------------
    # E. Evolución temporal (Día por día)
    # -------------------------------------------------------------------------
    daily_counter = Counter()
    for r in valid_reports:
        d = r.get("date")
        if isinstance(d, datetime):
            day_str = d.strftime("%Y-%m-%d")
            daily_counter[day_str] += 1

    daily_distribution: List[DailyTrend] = []
    peak_day = None
    peak_count = 0

    for d_str in sorted(daily_counter.keys()):
        cnt = daily_counter[d_str]
        daily_distribution.append(DailyTrend(date_str=d_str, count=cnt))
        if cnt > peak_count:
            peak_count = cnt
            peak_day = d_str

    # -------------------------------------------------------------------------
    # F. Estados de seguimiento al corte del período
    # -------------------------------------------------------------------------
    # Indexar eventos por reportId para búsqueda O(1)
    events_by_report = defaultdict(list)
    for ev in all_events:
        rep_id = ev.get("reportId")
        if rep_id:
            events_by_report[rep_id].append(ev)

    status_counter = Counter()
    for r in valid_reports:
        r_id = r.get("_id")
        r_hash = r.get("hashCode", "")
        rep_events = events_by_report.get(r_id, [])
        latest_status, _ = get_latest_status_at_cutoff(
            report_id=r_id,
            report_hash_code=r_hash,
            events=rep_events,
            cutoff_utc=window.end_exclusive_utc,
        )
        status_counter[latest_status] += 1

    status_distribution: List[StatusMetric] = []
    for st_name, s_count in status_counter.most_common():
        pct = round((s_count / total_valid * 100), 2) if total_valid > 0 else 0.0
        status_distribution.append(
            StatusMetric(
                status=st_name,
                count=s_count,
                percentage=pct,
                denominator=total_valid,
            )
        )

    return MetricsReport(
        window=window.to_dict(),
        generated_at_utc=now_iso,
        total_reports_in_period=total_in_period,
        usable_message_reports=usable_count,
        empty_or_legacy_message_reports=legacy_count,
        invalid_data_reports=total_invalid,
        exclusions_by_reason=dict(exclusions),
        exact_messages_ranking=exact_ranking[:20],
        normalized_messages_ranking=norm_ranking[:20],
        legacy_description_ranking=legacy_ranking[:20],
        emails_ranking=email_ranking[:20],
        domains_ranking=domain_ranking[:20],
        categories_ranking=categories_ranking,
        daily_distribution=daily_distribution,
        peak_day=peak_day,
        peak_count=peak_count,
        tracking_status_distribution=status_distribution,
    )
