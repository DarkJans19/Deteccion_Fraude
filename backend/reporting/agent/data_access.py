"""Acceso de solo lectura a MongoDB con verificación de integridad criptográfica."""

from typing import Any, Dict, List, Tuple
from bson import ObjectId
from pymongo.database import Database

from backend.reporting.agent.date_rules import QueryWindow
from backend.reporting.hasher import IntegrityError
from backend.reporting.mongo import MongoReports, event_hash


class ReadOnlyReportsStore:
    """Acceso estricto de solo lectura a las colecciones originales de reportes."""

    def __init__(self, database: Database):
        self.database = database
        self.reports_col = database.fraud_reports
        self.events_col = database.fraud_report_events

    def fetch_period_data(
        self, window: QueryWindow, limit: int = 50000
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]]]:
        """Consulta los reportes del período y sus eventos de seguimiento anteriores al corte.
        
        Aplica:
        - Consulta por fraud_reports.date:
            date >= window.start_utc
            date < window.end_exclusive_utc
        - Verificación canónica de integridad BSON y hash SHA-256 (moderno o legacy).
        - Documentos con hash o formato alterado se separan en invalid_reports con su motivo.
        - Para los reportes válidos, consulta todos los eventos con date < window.end_exclusive_utc.
        """
        query = {
            "date": {
                "$gte": window.start_utc,
                "$lt": window.end_exclusive_utc,
            }
        }

        # Orden determinista aprovechando el índice { date: -1, _id: -1 }
        cursor = self.reports_col.find(query).sort([("date", 1), ("_id", 1)]).limit(limit)

        valid_reports: List[Dict[str, Any]] = []
        invalid_reports: List[Dict[str, Any]] = []
        valid_report_ids: List[ObjectId] = []

        for doc in cursor:
            try:
                verified_doc = MongoReports.verify(doc)
                valid_reports.append(verified_doc)
                if isinstance(verified_doc.get("_id"), ObjectId):
                    valid_report_ids.append(verified_doc["_id"])
            except IntegrityError as err:
                invalid_copy = dict(doc)
                invalid_copy["exclusion_reason"] = f"Integridad vulnerada: {err}"
                invalid_reports.append(invalid_copy)
            except Exception as err:
                invalid_copy = dict(doc)
                invalid_copy["exclusion_reason"] = f"Formato no válido: {err}"
                invalid_reports.append(invalid_copy)

        # Consultar eventos de seguimiento de los reportes seleccionados hasta el límite final
        events: List[Dict[str, Any]] = []
        if valid_report_ids:
            event_query = {
                "reportId": {"$in": valid_report_ids},
                "date": {"$lt": window.end_exclusive_utc},
            }
            # Índice { reportId: 1, date: 1, _id: 1 }
            event_cursor = self.events_col.find(event_query).sort([("date", 1), ("_id", 1)])
            for ev in event_cursor:
                # Comprobar integridad básica del hash del evento
                try:
                    expected_h = event_hash(ev)
                    if ev.get("hashCode") == expected_h:
                        events.append(ev)
                except Exception:
                    # Eventos alterados no se procesan como válidos
                    continue

        return valid_reports, invalid_reports, events
