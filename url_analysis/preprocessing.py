"""Carga, normalizacion y particion de los datasets de URLs."""

from __future__ import annotations

import csv
import html
import ipaddress
import json
import math
import random
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator, Sequence
from urllib.parse import urlsplit, urlunsplit

from .domains import registrable_domain


LABEL_LEGITIMATE = 0
LABEL_PHISHING = 1
SPLIT_NAMES = ("train", "validation", "test")


@dataclass(frozen=True)
class UrlRecord:
    url: str
    label: int
    source: str


@dataclass(frozen=True)
class SourceSpec:
    filename: str
    url_column: str
    label_column: str
    label_mapping: dict[str, int]


DEFAULT_SOURCES = (
    SourceSpec(
        filename="URL dataset.csv",
        url_column="url",
        label_column="type",
        label_mapping={"legitimate": LABEL_LEGITIMATE, "phishing": LABEL_PHISHING},
    ),
    SourceSpec(
        filename="Phishing URLs.csv",
        url_column="url",
        label_column="Type",
        label_mapping={"phishing": LABEL_PHISHING},
    ),
    SourceSpec(
        filename="PhiUSIIL_Phishing_URL_Dataset.csv",
        url_column="URL",
        label_column="label",
        # Convencion original de PhiUSIIL: 1=legitima y 0=phishing.
        label_mapping={"1": LABEL_LEGITIMATE, "0": LABEL_PHISHING},
    ),
)


def normalize_url(raw_url: str, max_length: int = 2048) -> str | None:
    """Devuelve una representacion canonica o ``None`` si no es HTTP(S) valida.

    La funcion no descarga la pagina. Conserva ruta, consulta y fragmento porque
    contienen informacion util para detectar ofuscacion.
    """

    value = html.unescape(str(raw_url)).strip()
    if len(value) >= 2 and (value[0], value[-1]) in {
        ("[", "]"),
        ('"', '"'),
        ("'", "'"),
    }:
        value = value[1:-1].strip()
    if not value or len(value) > max_length:
        return None
    if any(character.isspace() or ord(character) < 32 for character in value):
        return None

    # Algunos corpus guardan dominios sin esquema. Se acepta el caso, usando
    # HTTP como valor conservador en vez de atribuirle HTTPS sin evidencia.
    if "://" not in value:
        value = "http://" + value

    try:
        parsed = urlsplit(value)
        scheme = parsed.scheme.casefold()
        if scheme not in {"http", "https"} or not parsed.hostname:
            return None

        hostname = parsed.hostname.rstrip(".").casefold()
        try:
            hostname = hostname.encode("idna").decode("ascii")
        except UnicodeError:
            return None

        try:
            ipaddress.ip_address(hostname)
            host = f"[{hostname}]" if ":" in hostname else hostname
        except ValueError:
            if "." not in hostname and hostname != "localhost":
                return None
            host = hostname

        try:
            port = parsed.port
        except ValueError:
            return None

        user_info = ""
        if parsed.username is not None:
            user_info = parsed.username
            if parsed.password is not None:
                user_info += ":" + parsed.password
            user_info += "@"

        is_default_port = (scheme == "http" and port == 80) or (
            scheme == "https" and port == 443
        )
        port_text = "" if port is None or is_default_port else f":{port}"
        netloc = f"{user_info}{host}{port_text}"
        path = parsed.path or "/"
        normalized = urlunsplit((scheme, netloc, path, parsed.query, parsed.fragment))
        return normalized if len(normalized) <= max_length else None
    except (TypeError, ValueError, UnicodeError):
        return None


def iter_source(path: Path, spec: SourceSpec, max_url_length: int) -> Iterator[UrlRecord]:
    with path.open(encoding="utf-8-sig", newline="", errors="replace") as source:
        reader = csv.DictReader(source)
        columns = set(reader.fieldnames or ())
        required = {spec.url_column, spec.label_column}
        missing = required - columns
        if missing:
            raise ValueError(f"{path.name}: faltan columnas {sorted(missing)}")

        for line_number, row in enumerate(reader, start=2):
            label_text = (row.get(spec.label_column) or "").strip().casefold()
            if label_text not in spec.label_mapping:
                raise ValueError(
                    f"{path.name}, registro {line_number}: etiqueta desconocida "
                    f"{row.get(spec.label_column)!r}"
                )
            normalized = normalize_url(row.get(spec.url_column) or "", max_url_length)
            if normalized is not None:
                yield UrlRecord(normalized, spec.label_mapping[label_text], path.name)


def merge_sources(
    data_dir: Path,
    specs: Sequence[SourceSpec] = DEFAULT_SOURCES,
    max_url_length: int = 2048,
) -> tuple[list[UrlRecord], list[dict[str, str]], dict[str, object]]:
    """Une fuentes, elimina duplicados y aparta etiquetas contradictorias."""

    merged: dict[str, dict[str, object]] = {}
    input_counts: Counter[str] = Counter()
    valid_counts: Counter[str] = Counter()

    for spec in specs:
        path = data_dir / spec.filename
        if not path.is_file():
            raise FileNotFoundError(f"No se encontro la fuente requerida: {path}")

        # Cuenta filas fisicas utiles para que el reporte muestre descartes.
        with path.open(encoding="utf-8-sig", newline="", errors="replace") as source:
            reader = csv.DictReader(source)
            for _ in reader:
                input_counts[path.name] += 1

        for record in iter_source(path, spec, max_url_length):
            valid_counts[path.name] += 1
            item = merged.setdefault(record.url, {"labels": set(), "sources": set()})
            item["labels"].add(record.label)  # type: ignore[union-attr]
            item["sources"].add(record.source)  # type: ignore[union-attr]

    records: list[UrlRecord] = []
    conflicts: list[dict[str, str]] = []
    for url, item in merged.items():
        labels = sorted(item["labels"])  # type: ignore[arg-type]
        sources = sorted(item["sources"])  # type: ignore[arg-type]
        if len(labels) != 1:
            conflicts.append(
                {
                    "url": url,
                    "labels": "|".join(map(str, labels)),
                    "sources": "|".join(sources),
                }
            )
            continue
        records.append(UrlRecord(url, labels[0], "|".join(sources)))

    total_valid = sum(valid_counts.values())
    report: dict[str, object] = {
        "label_convention": {"0": "legitimate", "1": "phishing"},
        "input_rows": dict(input_counts),
        "valid_rows": dict(valid_counts),
        "discarded_invalid_rows": {
            source: input_counts[source] - valid_counts[source] for source in input_counts
        },
        "duplicates_removed": total_valid - len(merged),
        "conflicting_urls_removed": len(conflicts),
        "unique_urls": len(records),
        "class_counts": {
            "legitimate": sum(row.label == LABEL_LEGITIMATE for row in records),
            "phishing": sum(row.label == LABEL_PHISHING for row in records),
        },
    }
    return records, conflicts, report


def split_records(
    records: Iterable[UrlRecord],
    ratios: tuple[float, float, float] = (0.70, 0.15, 0.15),
    seed: int = 42,
) -> dict[str, list[UrlRecord]]:
    """Crea particiones por dominio registrable, sin fuga entre conjuntos."""

    if (
        len(ratios) != 3
        or any(not math.isfinite(value) or value <= 0 for value in ratios)
        or not math.isclose(sum(ratios), 1.0, abs_tol=1e-9, rel_tol=0)
    ):
        raise ValueError("Las tres proporciones deben ser positivas y sumar 1")

    rows = list(records)
    totals = Counter(row.label for row in rows)
    targets = {
        name: {label: totals[label] * ratios[index] for label in totals}
        for index, name in enumerate(SPLIT_NAMES)
    }
    groups: dict[str, list[UrlRecord]] = defaultdict(list)
    for record in rows:
        hostname = urlsplit(record.url).hostname or record.url
        groups[registrable_domain(hostname)].append(record)

    grouped = list(groups.items())
    rng = random.Random(seed)
    rng.shuffle(grouped)
    grouped.sort(key=lambda item: len(item[1]), reverse=True)

    result = {name: [] for name in SPLIT_NAMES}
    current = {name: Counter() for name in SPLIT_NAMES}
    for _, group in grouped:
        group_counts = Counter(row.label for row in group)
        candidates = list(SPLIT_NAMES)
        rng.shuffle(candidates)

        def incremental_cost(name: str) -> float:
            cost = 0.0
            for label, total in totals.items():
                before = current[name][label] - targets[name][label]
                after = current[name][label] + group_counts[label] - targets[name][label]
                cost += (after * after - before * before) / max(total, 1)
            return cost

        selected = min(candidates, key=incremental_cost)
        result[selected].extend(group)
        current[selected].update(group_counts)

    for partition in result.values():
        rng.shuffle(partition)
    if any(not partition for partition in result.values()):
        raise ValueError("No hay suficientes dominios para crear tres particiones")
    return result


def write_prepared_dataset(
    output_dir: Path,
    partitions: dict[str, list[UrlRecord]],
    conflicts: list[dict[str, str]],
    report: dict[str, object],
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    split_counts: dict[str, dict[str, int]] = {}
    for name in SPLIT_NAMES:
        rows = partitions[name]
        with (output_dir / f"{name}.csv").open(
            "w", encoding="utf-8", newline=""
        ) as target:
            writer = csv.DictWriter(target, fieldnames=("url", "label", "source"))
            writer.writeheader()
            writer.writerows(
                {"url": row.url, "label": row.label, "source": row.source} for row in rows
            )
        split_counts[name] = {
            "total": len(rows),
            "legitimate": sum(row.label == LABEL_LEGITIMATE for row in rows),
            "phishing": sum(row.label == LABEL_PHISHING for row in rows),
        }

    with (output_dir / "conflicts.csv").open("w", encoding="utf-8", newline="") as target:
        writer = csv.DictWriter(target, fieldnames=("url", "labels", "sources"))
        writer.writeheader()
        writer.writerows(conflicts)

    report["splits"] = split_counts
    report["split_strategy"] = "registrable_domain_disjoint"
    split_domains = {
        name: {
            registrable_domain(urlsplit(row.url).hostname or row.url) for row in rows
        }
        for name, rows in partitions.items()
    }
    report["domain_overlap"] = {
        "train_validation": len(split_domains["train"] & split_domains["validation"]),
        "train_test": len(split_domains["train"] & split_domains["test"]),
        "validation_test": len(split_domains["validation"] & split_domains["test"]),
    }
    with (output_dir / "report.json").open("w", encoding="utf-8") as target:
        json.dump(report, target, ensure_ascii=False, indent=2, sort_keys=True)
        target.write("\n")
