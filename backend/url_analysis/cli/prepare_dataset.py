"""Une y prepara exclusivamente los datasets de URLs."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[3]
MODULE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from backend.url_analysis.preprocessing import merge_sources, split_records, write_prepared_dataset


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=MODULE_ROOT / "data" / "raw")
    parser.add_argument(
        "--output-dir", type=Path, default=MODULE_ROOT / "data" / "processed"
    )
    parser.add_argument("--train-ratio", type=float, default=0.70)
    parser.add_argument("--validation-ratio", type=float, default=0.15)
    parser.add_argument("--test-ratio", type=float, default=0.15)
    parser.add_argument("--max-url-length", type=int, default=2048)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    try:
        records, conflicts, report = merge_sources(
            args.data_dir, max_url_length=args.max_url_length
        )
        partitions = split_records(
            records,
            (args.train_ratio, args.validation_ratio, args.test_ratio),
            args.seed,
        )
        write_prepared_dataset(args.output_dir, partitions, conflicts, report)
    except (OSError, ValueError) as error:
        parser.exit(1, f"Error: {error}\n")

    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"Dataset preparado en: {args.output_dir.resolve()}")


if __name__ == "__main__":
    main()
