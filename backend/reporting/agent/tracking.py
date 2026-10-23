"""Determinación determinista del estado de seguimiento de reportes al corte del período."""

from typing import Dict, List, Optional
from bson import ObjectId

from backend.reporting.mongo import STATUSES, event_hash


def get_latest_status_at_cutoff(
    report_id: ObjectId,
    report_hash_code: str,
    events: List[dict],
    cutoff_utc,
) -> TupleStr:
    """Calcula el último estado conocido de un reporte antes del límite final del período.
    
    Reglas:
    - Incluye eventos con date < cutoff_utc (incluso anteriores al inicio del período).
    - Descarta eventos con date >= cutoff_utc.
    - Desempate determinista cuando varios eventos tienen la misma fecha:
      Orden ascendente por (event["date"], str(event["_id"])).
    - Retorna el status del último evento. Si no hay eventos antes del corte, retorna 'sin_eventos'.
    - Verifica la integridad del hash de cada evento considerado.
    """
    valid_events = []
    for ev in events:
        if ev.get("reportId") != report_id:
            continue
        ev_date = ev.get("date")
        if ev_date is None or ev_date >= cutoff_utc:
            continue
        valid_events.append(ev)

    if not valid_events:
        return "sin_eventos", None

    # Desempate determinista por fecha e identificador
    valid_events.sort(key=lambda x: (x["date"], str(x.get("_id", ""))))
    last_event = valid_events[-1]
    
    status = last_event.get("status")
    if status not in STATUSES:
        status = "desconocido"
        
    return status, last_event


TupleStr = tuple[str, Optional[dict]]
