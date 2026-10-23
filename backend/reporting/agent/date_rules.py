"""Reglas de validación, calendario y cálculo de ventanas de consulta en UTC."""

import calendar
import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Tuple
import zoneinfo

DATE_REGEX = re.compile(r"^(\d{2})/(\d{2})/(\d{4})$")


class DateValidationError(ValueError):
    """Error cuando un intervalo o formato de fecha no cumple las reglas."""
    pass


@dataclass(frozen=True)
class QueryWindow:
    start_date_str: str
    end_date_str: str
    start_date: date
    end_date: date
    start_utc: datetime
    end_exclusive_utc: datetime
    timezone_name: str

    def to_dict(self) -> dict:
        return {
            "start_date": self.start_date_str,
            "end_date": self.end_date_str,
            "timezone": self.timezone_name,
            "start_utc": self.start_utc.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            "end_exclusive_utc": self.end_exclusive_utc.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
        }


def parse_date_str(value: str, param_name: str) -> date:
    """Valida estrictamente el formato DD/MM/AAAA y la existencia real del día."""
    if not isinstance(value, str):
        raise DateValidationError(f"{param_name} debe ser una cadena de texto en formato DD/MM/AAAA.")
    
    value = value.strip()
    match = DATE_REGEX.match(value)
    if not match:
        raise DateValidationError(f"{param_name} ('{value}') no cumple con el formato estricto DD/MM/AAAA.")
    
    day_str, month_str, year_str = match.groups()
    day, month, year = int(day_str), int(month_str), int(year_str)

    if year < 1900 or year > 2200:
        raise DateValidationError(f"{param_name}: el año {year} se encuentra fuera del rango permitido [1900, 2200].")

    try:
        parsed_date = date(year, month, day)
    except ValueError as err:
        raise DateValidationError(f"{param_name}: la fecha '{value}' es imposible en el calendario ({err}).") from None

    return parsed_date


def get_min_end_date(start: date) -> date:
    """Calcula la fecha final mínima: exactamente un mes calendario después.
    
    Si el día no existe en el mes siguiente, se utiliza el último día de dicho mes.
    Ejemplos:
    - 15/01/2026 -> 15/02/2026
    - 31/01/2026 -> 28/02/2026 (año no bisiesto)
    - 31/01/2024 -> 29/02/2024 (año bisiesto)
    """
    if start.month == 12:
        target_year = start.year + 1
        target_month = 1
    else:
        target_year = start.year
        target_month = start.month + 1

    max_day = calendar.monthrange(target_year, target_month)[1]
    target_day = min(start.day, max_day)
    return date(target_year, target_month, target_day)


def get_zone_info(tz_name: str) -> zoneinfo.ZoneInfo:
    """Obtiene la zona horaria IANA validando su existencia."""
    try:
        return zoneinfo.ZoneInfo(tz_name)
    except Exception as err:
        raise DateValidationError(f"Zona horaria no válida o no encontrada: '{tz_name}' ({err}).") from None


def build_query_window(start_str: str, end_str: str, tz_name: str = "America/Bogota") -> QueryWindow:
    """Valida el intervalo y construye los límites de consulta en UTC.
    
    Reglas:
    - Validación de formato y existencia de ambas fechas.
    - end_date debe ser estrictamente posterior a start_date.
    - Debe existir al menos un mes calendario de diferencia.
    - Límites de consulta en MongoDB:
        date >= inicio del día inicial (00:00:00 local convertido a UTC)
        date < inicio del día posterior a la fecha final (00:00:00 local del día siguiente convertido a UTC)
    """
    tz = get_zone_info(tz_name)

    start_d = parse_date_str(start_str, "start_date")
    end_d = parse_date_str(end_str, "end_date")

    if end_d <= start_d:
        raise DateValidationError(
            f"La fecha final ({end_str}) debe ser posterior a la fecha inicial ({start_str})."
        )

    min_end = get_min_end_date(start_d)
    if end_d < min_end:
        min_end_str = min_end.strftime("%d/%m/%Y")
        raise DateValidationError(
            f"El intervalo seleccionado ({start_str} a {end_str}) es menor a un mes calendario. "
            f"La fecha final mínima permitida para {start_str} es {min_end_str}."
        )

    # Inicio del día inicial a las 00:00:00 en la zona configurada
    start_dt_local = datetime.combine(start_d, time.min).replace(tzinfo=tz)
    start_utc = start_dt_local.astimezone(timezone.utc)

    # Inicio del día posterior a la fecha final a las 00:00:00 en la zona configurada
    # (Garantiza cobertura del día final completo sin usar 23:59:59)
    day_after_end = end_d + timedelta(days=1)
    end_exclusive_local = datetime.combine(day_after_end, time.min).replace(tzinfo=tz)
    end_exclusive_utc = end_exclusive_local.astimezone(timezone.utc)

    return QueryWindow(
        start_date_str=start_d.strftime("%d/%m/%Y"),
        end_date_str=end_d.strftime("%d/%m/%Y"),
        start_date=start_d,
        end_date=end_d,
        start_utc=start_utc,
        end_exclusive_utc=end_exclusive_utc,
        timezone_name=tz_name,
    )
