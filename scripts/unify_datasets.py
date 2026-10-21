import argparse
import csv
import math
import random
from collections import Counter, defaultdict
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FIELDS = ("text", "url", "label")
SPLITS = ("train", "test", "validation")


def read_rows(path, required):
    with path.open(encoding="utf-8-sig", newline="") as source:
        reader = csv.DictReader(source)
        missing = set(required) - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f"{path.name}: faltan columnas {sorted(missing)}")
        for number, row in enumerate(reader, start=2):
            if None in row or any(row.get(key) is None for key in required):
                raise ValueError(f"{path.name}: registro {number} mal formado")
            yield number, row


def mapped_bool(value, mapping, context):
    key = value.strip().casefold()
    if key not in mapping:
        raise ValueError(f"{context}: valor desconocido {value!r}")
    return mapping[key]


def load_datasets(data_dir, sms_path=None, mail_path=None):
    records = []
    sms_path = Path(sms_path) if sms_path is not None else data_dir / "Dataset_5971.csv"
    for number, row in read_rows(sms_path, ("TEXT", "URL", "LABEL")):
        context = f"{sms_path.name}, registro {number}"
        records.append({
            "text": row["TEXT"].strip(),
            "url": mapped_bool(row["URL"], {"yes": True, "no": False}, context),
            "label": mapped_bool(
                row["LABEL"], {"ham": False, "spam": False, "smishing": True}, context
            ),
        })

    mail_path = Path(mail_path) if mail_path is not None else data_dir / "Spaphish dataset - DiB.csv"
    for number, row in read_rows(mail_path, ("subject", "body", "url_count", "urls", "Label")):
        context = f"{mail_path.name}, registro {number}"
        try:
            count = int(row["url_count"].strip())
        except ValueError as error:
            raise ValueError(f"{context}: url_count debe ser un entero") from error
        if count < 0:
            raise ValueError(f"{context}: url_count no puede ser negativo")
        has_urls = row["urls"].strip().casefold() not in ("", "[]", "null", "none", "nan")
        records.append({
            "text": "\n".join(
                part.strip() for part in (row["subject"], row["body"]) if part.strip()
            ),
            "url": count > 0 or has_urls,
            "label": mapped_bool(row["Label"], {"0": False, "1": True}, context),
        })

    if not records or any(not row["text"] for row in records):
        raise ValueError("Los datasets no pueden estar vacios ni contener textos vacios")
    return records


def split_records(records, ratios=(0.8, 0.1, 0.1), seed=42):
    if (len(ratios) != 3 or any(not math.isfinite(r) or r <= 0 for r in ratios)
            or not math.isclose(sum(ratios), 1.0, abs_tol=1e-9, rel_tol=0)):
        raise ValueError("Las tres proporciones deben ser positivas y sumar 1")

    rng = random.Random(seed)
    groups = defaultdict(list)
    for record in records:
        groups[record["text"]].append(record)
    strata = defaultdict(list)
    for group in groups.values():
        signature = tuple(sorted({row["label"] for row in group}))
        strata[signature].append(group)

    partitions = {name: [] for name in SPLITS}
    for signature in sorted(strata):
        grouped = strata[signature]
        rng.shuffle(grouped)
        grouped.sort(key=len, reverse=True)
        total = sum(len(group) for group in grouped)
        targets = [total * ratio for ratio in ratios]
        sizes = [0, 0, 0]
        for group in grouped:
            index = max(range(3), key=lambda i: targets[i] - sizes[i])
            partitions[SPLITS[index]].extend(group)
            sizes[index] += len(group)

    for rows in partitions.values():
        rng.shuffle(rows)
    if any(not rows for rows in partitions.values()):
        raise ValueError("No hay suficientes grupos para generar tres particiones no vacias")
    return partitions


def write_partitions(partitions, output_dir):
    output_dir.mkdir(parents=True, exist_ok=True)
    for name, rows in partitions.items():
        with (output_dir / f"{name}.csv").open("w", encoding="utf-8-sig", newline="") as target:
            writer = csv.DictWriter(target, fieldnames=FIELDS)
            writer.writeheader()
            # CSV no tiene tipos nativos: serializa los bool como True/False.
            writer.writerows(rows)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=ROOT / "data_original")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "dataset")
    parser.add_argument("--sms-file", type=Path, help="CSV de SMS alternativo")
    parser.add_argument("--mail-file", type=Path, help="CSV de correos alternativo")
    parser.add_argument("--train-ratio", type=float, default=0.8)
    parser.add_argument("--test-ratio", type=float, default=0.1)
    parser.add_argument("--validation-ratio", type=float, default=0.1)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()
    try:
        sms_path = args.sms_file or args.data_dir / "Dataset_5971.csv"
        mail_path = args.mail_file or args.data_dir / "Spaphish dataset - DiB.csv"
        records = load_datasets(args.data_dir, sms_path, mail_path)
        partitions = split_records(
            records, (args.train_ratio, args.test_ratio, args.validation_ratio), args.seed
        )
        write_partitions(partitions, args.output_dir)
    except (OSError, ValueError, csv.Error) as error:
        parser.exit(1, f"Error: {error}\n")

    labels_by_text = defaultdict(set)
    for row in records:
        labels_by_text[row["text"]].add(row["label"])
    conflicts = sum(len(labels) > 1 for labels in labels_by_text.values())
    print(f"Total: {len(records)} registros. Columnas: {', '.join(FIELDS)}")
    if conflicts:
        print(f"Aviso: {conflicts} textos tienen etiquetas contradictorias; se conservan en el mismo split.")
    for name, rows in partitions.items():
        counts = Counter(row["label"] for row in rows)
        print(f"{name}: {len(rows)} | phishing={counts[True]} | no phishing={counts[False]}")
    print(f"Archivos guardados en: {args.output_dir.resolve()}")


if __name__ == "__main__":
    main()
