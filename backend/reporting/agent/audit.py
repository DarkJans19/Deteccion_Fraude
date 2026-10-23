"""Persistencia en MongoDB para auditoría, idempotencia, autorizaciones y control de despachos."""

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple
from bson import ObjectId
from pymongo import ASCENDING, DESCENDING
from pymongo.database import Database
from pymongo.errors import DuplicateKeyError

from backend.reporting.hasher import digest

# Estados del intento de despacho
STATUS_PENDING = "PENDING"
STATUS_ACCEPTED = "ACCEPTED"           # Solicitud aceptada por el proveedor (NO implica entrega confirmada)
STATUS_SIMULATED = "SIMULATED"         # Envío simulado en desarrollo/pruebas
STATUS_FAILED = "FAILED"               # Fallo comprobado
STATUS_UNCERTAIN = "UNCERTAIN"         # Resultado de red incierto (ej. timeout tras envío de bytes)


def normalize_recipient(email: str) -> str:
    return email.strip().lower()


def compute_logical_key(report_id: str, recipient_email: str) -> str:
    """Clave lógica estable del envío que no puede eludirse cambiando de proveedor."""
    raw = f"{report_id}:{normalize_recipient(recipient_email)}"
    return digest(raw)


def compute_idempotency_key(report_id: str, recipient_email: str, pdf_sha256: str, is_simulation: bool) -> str:
    """Clave de idempotencia única para la transacción física."""
    sim_flag = "sim" if is_simulation else "real"
    raw = f"{report_id}:{normalize_recipient(recipient_email)}:{pdf_sha256}:{sim_flag}"
    return digest(raw)


class AuditStore:
    def __init__(self, database: Database):
        self.database = database
        self.generations = database.report_generations
        self.deliveries = database.report_deliveries
        self.authorizations = database.report_authorizations

    def initialize_indexes(self):
        """Prepara índices necesarios en las colecciones de auditoría en desarrollo."""
        # report_generations
        self.generations.create_index([("reportId", ASCENDING)], unique=True, name="idx_gen_report_id")
        self.generations.create_index([("generatedAt", DESCENDING)], name="idx_gen_date")

        # report_deliveries
        self.deliveries.create_index([("idempotencyKey", ASCENDING)], unique=True, name="idx_del_idempotency")
        self.deliveries.create_index([("logicalKey", ASCENDING)], name="idx_del_logical")
        self.deliveries.create_index([("reportId", ASCENDING)], name="idx_del_report")
        self.deliveries.create_index([("status", ASCENDING)], name="idx_del_status")

        # report_authorizations
        self.authorizations.create_index([("recipientKey", ASCENDING)], name="idx_auth_recipient")
        self.authorizations.create_index([("revoked", ASCENDING)], name="idx_auth_revoked")

    def record_generation(
        self,
        report_id: str,
        period: Dict[str, Any],
        metrics_summary: Dict[str, Any],
        pdf_sha256: str,
        json_sha256: str,
        parameters_digest: str,
    ) -> Dict[str, Any]:
        """Registra metadatos de un informe generado."""
        doc = {
            "_id": ObjectId(),
            "reportId": report_id,
            "period": period,
            "metricsSummary": metrics_summary,
            "pdfSha256": pdf_sha256,
            "jsonSha256": json_sha256,
            "parametersDigest": parameters_digest,
            "generatedAt": datetime.now(timezone.utc),
        }
        self.generations.insert_one(doc)
        return doc

    def get_generation(self, report_id: str) -> Optional[Dict[str, Any]]:
        return self.generations.find_one({"reportId": report_id})

    # -------------------------------------------------------------------------
    # Autorizaciones
    # -------------------------------------------------------------------------
    def check_authorization(
        self,
        recipient_email: str,
        auto_send_enabled_env: bool,
        allowed_domains_env: List[str],
        allowed_emails_env: List[str],
    ) -> Tuple[bool, bool, str]:
        """Verifica la política de autorización con estricta precedencia:
        
        Precedencia:
        1. Revocación persistida -> denegación absoluta.
        2. AUTO_SEND_ENABLED=false -> bloquea envío automático (requiere interactivo autorizado).
        3. Autorización persistida vigente -> autoriza envío automático.
        4. Listas permitidas por entorno -> autoriza según coincidencia.
        
        Retorna: (is_allowed_to_send, can_auto_send, reason)
        """
        norm_email = normalize_recipient(recipient_email)
        domain = norm_email.split("@")[1] if "@" in norm_email else ""

        # 1. Comprobar si existe revocación explícita persistida
        revoked_rec = self.authorizations.find_one({
            "$or": [
                {"recipient": norm_email, "revoked": True},
                {"domain": domain, "revoked": True},
            ]
        })
        if revoked_rec:
            return False, False, f"La autorización para '{norm_email}' ha sido revocada explícitamente y no puede reactivarse."

        # 2. Comprobar si existe una autorización activa persistida en la BD
        now_utc = datetime.now(timezone.utc)
        active_auth = self.authorizations.find_one({
            "$or": [
                {"recipient": norm_email, "revoked": False, "validUntil": {"$gt": now_utc}},
                {"domain": domain, "revoked": False, "validUntil": {"$gt": now_utc}},
            ]
        })

        if active_auth:
            # Hay autorización en BD. Si el entorno no tiene el kill-switch AUTO_SEND_ENABLED=false, auto-envía.
            can_auto = auto_send_enabled_env
            reason = "Autorización vigente registrada en base de datos."
            return True, can_auto, reason

        # 3. Comprobar listas de entorno
        domain_match = domain in allowed_domains_env if domain else False
        email_match = norm_email in allowed_emails_env

        if domain_match or email_match:
            can_auto = auto_send_enabled_env
            reason = "Destinatario coincide con la política de dominios/correos autorizados en configuración."
            return True, can_auto, reason

        return False, False, f"El destinatario '{norm_email}' no cuenta con autorización previa registrada ni en listas permitidas."

    def grant_authorization(
        self,
        recipient: str,
        authorized_by: str,
        days_valid: int = 30,
        is_domain: bool = False,
    ) -> Dict[str, Any]:
        """Registra una autorización previa persistente (requiere permiso de creación/ampliación)."""
        now_utc = datetime.now(timezone.utc)
        valid_until = now_utc + timedelta(days=days_valid)
        norm_val = recipient.strip().lower()

        doc = {
            "_id": ObjectId(),
            "recipientKey": norm_val,
            "recipient": "" if is_domain else norm_val,
            "domain": norm_val if is_domain else "",
            "isDomain": is_domain,
            "authorizedBy": authorized_by,
            "authorizedAt": now_utc,
            "validUntil": valid_until,
            "revoked": False,
            "revokedAt": None,
        }
        self.authorizations.insert_one(doc)
        return doc

    def revoke_authorization(self, recipient_or_domain: str, revoked_by: str) -> int:
        """Revoca permanentemente autorizaciones para un correo o dominio."""
        norm_val = recipient_or_domain.strip().lower()
        now_utc = datetime.now(timezone.utc)
        result = self.authorizations.update_many(
            {"$or": [{"recipient": norm_val}, {"domain": norm_val}, {"recipientKey": norm_val}]},
            {"$set": {"revoked": True, "revokedAt": now_utc, "revokedBy": revoked_by}},
        )
        return result.modified_count

    # -------------------------------------------------------------------------
    # Idempotencia y Despachos
    # -------------------------------------------------------------------------
    def start_delivery_attempt(
        self,
        report_id: str,
        recipient_email: str,
        pdf_sha256: str,
        provider: str,
        is_simulation: bool,
    ) -> Tuple[Optional[Dict[str, Any]], bool, str]:
        """Inicia un intento de despacho registrando la clave de idempotencia de forma atómica.
        
        Reglas:
        - Si existe un envío real previo con estado ACCEPTED para la clave lógica (mismo report_id y destinatario),
          se bloquea inmediatamente el duplicado (incluso si se cambia de proveedor).
        - Una simulación previa NO bloquea un envío real posterior autorizado.
        - Si existe un intento en PENDING previo (posible solicitud concurrente o interrupción), se evalúa recuperación.
        - Inserción atómica condicional sobre idempotencyKey.
        """
        norm_email = normalize_recipient(recipient_email)
        logical_key = compute_logical_key(report_id, norm_email)
        idempotency_key = compute_idempotency_key(report_id, norm_email, pdf_sha256, is_simulation)

        # 1. Comprobar si ya existe un despacho real aceptado para este informe y destinatario
        if not is_simulation:
            existing_real = self.deliveries.find_one({
                "logicalKey": logical_key,
                "status": STATUS_ACCEPTED,
                "isSimulation": False,
            })
            if existing_real:
                return (
                    existing_real,
                    False,
                    f"Envío duplicado prevenido: El informe '{report_id}' ya fue aceptado previamente para '{norm_email}' "
                    f"(Intento {existing_real.get('deliveryId')}).",
                )

        # 2. Registrar intento atómico
        now_utc = datetime.now(timezone.utc)
        delivery_id = f"ATT-{report_id}-{now_utc.strftime('%Y%m%d%H%M%S%f')[:17]}"

        attempt_doc = {
            "_id": ObjectId(),
            "deliveryId": delivery_id,
            "reportId": report_id,
            "logicalKey": logical_key,
            "idempotencyKey": idempotency_key,
            "recipientMasked": norm_email[:2] + "***@" + norm_email.split("@")[1] if "@" in norm_email else "***",
            "recipientHash": digest(norm_email),
            "pdfSha256": pdf_sha256,
            "provider": provider,
            "isSimulation": is_simulation,
            "status": STATUS_PENDING,
            "attempts": 1,
            "providerMessageId": None,
            "errorDetails": None,
            "createdAt": now_utc,
            "updatedAt": now_utc,
        }

        try:
            self.deliveries.insert_one(attempt_doc)
            return attempt_doc, True, "Intento registrado correctamente en estado PENDING."
        except DuplicateKeyError:
            # Ya existe un intento exacto con esta clave física de idempotencia
            prior = self.deliveries.find_one({"idempotencyKey": idempotency_key})
            if prior:
                st = prior.get("status")
                if st in (STATUS_ACCEPTED, STATUS_SIMULATED):
                    return prior, False, f"Envío duplicado rechazado: Esta transacción ya fue procesada con estado {st}."
                if st == STATUS_PENDING:
                    return prior, False, "Solicitud concurrente o intento previo aún en proceso (PENDING)."
                if st == STATUS_UNCERTAIN:
                    return prior, False, "El intento previo finalizó con resultado incierto de red. Requiere revisión antes de reintentar."
            return None, False, "Conflicto de clave de idempotencia duplicada."

    def finish_delivery_attempt(
        self,
        delivery_id: str,
        status: str,
        provider_message_id: Optional[str] = None,
        error_details: Optional[str] = None,
    ) -> bool:
        """Actualiza el estado final de un intento de despacho."""
        now_utc = datetime.now(timezone.utc)
        update_fields: Dict[str, Any] = {
            "status": status,
            "updatedAt": now_utc,
            "providerMessageId": provider_message_id,
        }
        if error_details:
            update_fields["errorDetails"] = error_details[:1000]

        result = self.deliveries.update_one(
            {"deliveryId": delivery_id},
            {"$set": update_fields},
        )
        return result.modified_count > 0

    def recover_interrupted_attempts(self, older_than_seconds: int = 300) -> int:
        """Recupera registros PENDING tras una interrupción o reinicio del sistema.
        
        Como no puede determinarse con certeza si el proveedor alcanzó a despachar el correo,
        los registros PENDING antiguos se transicionan a UNCERTAIN para impedir reintentos ciegos.
        """
        threshold = datetime.now(timezone.utc) - timedelta(seconds=older_than_seconds)
        result = self.deliveries.update_many(
            {"status": STATUS_PENDING, "createdAt": {"$lt": threshold}},
            {
                "$set": {
                    "status": STATUS_UNCERTAIN,
                    "updatedAt": datetime.now(timezone.utc),
                    "errorDetails": "Proceso interrumpido mientras el envío estaba PENDING. Marcado como UNCERTAIN para prevenir duplicados.",
                }
            },
        )
        return result.modified_count
