"""Interfaz de línea de comandos (CLI) interactiva para el agente de reportes de fraude."""

import argparse
import os
import sys
from pathlib import Path
from dotenv import load_dotenv

from backend.reporting.agent.config import AgentConfig
from backend.reporting.agent.date_rules import DateValidationError
from backend.reporting.agent.email_sender.service import AuthorizationError, EmailValidationError, OperatorPermissions
from backend.reporting.agent.orchestrator import FraudReportingAgent
from backend.reporting.agent.synthetic_data import create_synthetic_dataset
from backend.reporting.mongo import client_from_environment, environment_config


def print_banner():
    print("=" * 70)
    print(" CENTINELA ANTIFRAUDE - AGENTE DE ANÁLISIS Y GENERACIÓN DE INFORMES ")
    print("=" * 70)


def run_interactive_cli():
    parser = argparse.ArgumentParser(description="Agente analítico de reportes de fraude e informes")
    parser.add_argument("--env-file", type=Path, help="Ruta opcional al archivo .env")
    parser.add_argument("--start-date", help="Fecha inicial (DD/MM/AAAA)")
    parser.add_argument("--end-date", help="Fecha final (DD/MM/AAAA)")
    parser.add_argument("--recipient-email", help="Correo destinatario")
    parser.add_argument("--recipient-name", help="Nombre del destinatario (opcional)")
    parser.add_argument("--recipient-entity", help="Entidad del destinatario (opcional)")
    parser.add_argument("--seed-synthetic", action="store_true", help="Inserta dataset sintético en la base de datos de desarrollo")
    parser.add_argument("--grant-auth", help="Registra autorización para un correo o dominio")
    parser.add_argument("--revoke-auth", help="Revoca autorización para un correo o dominio")
    parser.add_argument("--as-admin", action="store_true", help="Otorga permisos de administración al operador (can_authorize=True)")
    args = parser.parse_args()

    project_root = Path(__file__).resolve().parents[3]
    env_file = args.env_file or project_root / ".env"
    load_dotenv(env_file, override=False, interpolate=False)

    try:
        config_env = environment_config()
        client = client_from_environment(config_env)
        db_name = config_env.get("MONGODB_DATABASE") or client.get_default_database("fraud_detection").name
        database = client.get_database(db_name)
    except Exception as err:
        print(f"\n[ERROR] No se pudo conectar a MongoDB: {err}")
        print("Verifica MONGODB_URI en .env o usa docker compose para levantar el servicio.")
        sys.exit(1)

    agent_config = AgentConfig.from_env()
    agent = FraudReportingAgent(database, agent_config)

    # 1. Comando de revocación
    if args.revoke_auth:
        count = agent.audit.revoke_authorization(args.revoke_auth, revoked_by="cli_operator")
        print(f"\n[AUTORIZACIÓN] Se revocaron {count} registros para '{args.revoke_auth}'.")
        client.close()
        return

    # 2. Comando de concesión
    if args.grant_auth:
        is_dom = "@" not in args.grant_auth
        doc = agent.audit.grant_authorization(args.grant_auth, authorized_by="cli_admin", days_valid=30, is_domain=is_dom)
        print(f"\n[AUTORIZACIÓN] Concedida exitosamente para '{args.grant_auth}' hasta {doc['validUntil']}.")
        client.close()
        return

    # 3. Poblar datos sintéticos si se solicita
    if args.seed_synthetic:
        print("\n[DEMO] Insertando datos sintéticos de prueba en MongoDB...")
        res = create_synthetic_dataset(database)
        print(f"[DEMO] Se crearon {res['reports_created']} reportes y {res['events_created']} eventos.")

    print_banner()

    # 4. Solicitud y validación de fechas (Flujo 1 y 2)
    start_d = args.start_date
    if not start_d:
        start_d = input("\n1. Introduce la fecha inicial (DD/MM/AAAA): ").strip()

    end_d = args.end_date
    if not end_d:
        end_d = input("2. Introduce la fecha final   (DD/MM/AAAA): ").strip()

    print(f"\n[PROCESO] Validando intervalo y consultando reportes en zona horaria '{agent_config.timezone_name}'...")
    try:
        analysis = agent.analyze_period(start_d, end_d, recipient_name=args.recipient_name, recipient_entity=args.recipient_entity)
    except DateValidationError as err:
        print(f"\n[ERROR DE FECHAS] {err}")
        client.close()
        sys.exit(1)
    except Exception as err:
        print(f"\n[ERROR EN ANÁLISIS] {err}")
        client.close()
        sys.exit(1)

    # 5. Mostrar resumen y estado de generación (Flujo 7)
    m = analysis.metrics
    print("\n" + "-" * 70)
    print(f" RESUMEN DEL INFORME GENERADO: {analysis.report_id}")
    print("-" * 70)
    print(f"- Período analizado:       {m.window['start_date']} al {m.window['end_date']} ({m.window['timezone']})")
    print(f"- Total reportes:          {m.total_reports_in_period}")
    print(f"- Mensajes utilizables:    {m.usable_message_reports} ({round(m.usable_message_reports / max(m.total_reports_in_period, 1) * 100, 1)}%)")
    print(f"- Reportes legacy:         {m.empty_or_legacy_message_reports}")
    print(f"- Documentos excluidos:    {m.invalid_data_reports}")
    if m.normalized_messages_ranking:
        top_m = m.normalized_messages_ranking[0]
        print(f"- Mensaje más repetido:    '{top_m.text_sample_anonymized[:60]}...' ({top_m.count} reportes, {top_m.percentage}%)")
    if m.emails_ranking:
        top_e = m.emails_ranking[0]
        print(f"- Correo más recurrente:   {top_e.email_masked} ({top_e.distinct_reports_count} reportes distintos, {top_e.total_occurrences} menciones)")
    if m.categories_ranking:
        print(f"- Categoría principal:     {m.categories_ranking[0].category} ({m.categories_ranking[0].reports_count} reportes)")
    print(f"- Archivo JSON generado:   {analysis.json_path}")
    print(f"- Archivo PDF generado:    {analysis.pdf_path} (SHA-256: {analysis.pdf_sha256[:16]}...)")
    print("-" * 70)

    # 6. Solicitud de correo destinatario tras generación exitosa (Flujo 8)
    recipient = args.recipient_email
    if not recipient:
        recipient = input("\n3. Introduce el correo electrónico del destinatario: ").strip()

    rec_name = args.recipient_name
    rec_entity = args.recipient_entity
    if not rec_name and not args.recipient_email:
        rec_name = input("   Nombre del destinatario (opcional, Enter para omitir): ").strip() or None
    if not rec_entity and not args.recipient_email:
        rec_entity = input("   Entidad del destinatario (opcional, Enter para omitir): ").strip() or None

    operator_perms = OperatorPermissions(can_send=True, can_authorize=args.as_admin)

    # 7. Evaluación de autorización y confirmación interactiva
    is_allowed, can_auto, reason = agent.audit.check_authorization(
        recipient_email=recipient,
        auto_send_enabled_env=agent_config.auto_send_enabled,
        allowed_domains_env=agent_config.allowed_recipient_domains,
        allowed_emails_env=agent_config.allowed_recipient_emails,
    )

    interactive_confirmed = False
    if agent.delivery_service.adapter.is_simulation:
        print(f"\n[AVISO] Modo actual: SIMULACIÓN (Proveedor: mock). No se realizarán envíos externos reales.")
    elif is_allowed and can_auto:
        print(f"\n[AUTORIZACIÓN VIGENTE] {reason}. Procediendo con envío automático.")
        interactive_confirmed = True
    else:
        print(f"\n[CONFIRMACIÓN REQUERIDA] {reason}")
        print(f"Asunto redactado: '{analysis.narrative.email_subject}'")
        print(f"Adjunto: '{analysis.pdf_path.name}' ({analysis.pdf_sha256[:12]}...)")
        conf = input(f"¿Confirma el despacho del informe a {recipient}? [s/N]: ").strip().lower()
        if conf in ("s", "si", "sí", "y", "yes"):
            interactive_confirmed = True
        else:
            print("\n[CANCELADO] Despacho cancelado por el operador. El informe PDF y JSON permanecen guardados.")
            client.close()
            return

    # 8. Despacho
    try:
        success, disp_msg, result = agent.dispatch_report(
            analysis_result=analysis,
            recipient_email=recipient,
            operator_permissions=operator_perms,
            interactive_confirmed=interactive_confirmed,
        )
        if success:
            print(f"\n[ÉXITO] {disp_msg}")
            if result:
                print(f"Estado de despacho: {result.status} | Proveedor: {result.provider_name} | ID: {result.provider_message_id or 'N/A'}")
        else:
            print(f"\n[NO DESPACHADO] {disp_msg}")
    except (AuthorizationError, EmailValidationError) as err:
        print(f"\n[DENEGADO] {err}")
    except Exception as err:
        print(f"\n[FALLO INESPERADO] {err}")

    client.close()


if __name__ == "__main__":
    run_interactive_cli()
