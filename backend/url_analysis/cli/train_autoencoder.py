"""Entrena el autoencoder de URLs legitimas y evalua phishing como anomalia."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[3]
MODULE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from backend.url_analysis.autoencoder import train


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dataset-dir", type=Path, default=MODULE_ROOT / "data" / "processed"
    )
    parser.add_argument(
        "--output-dir", type=Path, default=MODULE_ROOT / "artifacts" / "url_detector"
    )
    parser.add_argument("--max-train-samples", type=int, default=100_000)
    parser.add_argument("--max-evaluation-samples", type=int, default=100_000)
    parser.add_argument("--epochs", type=int, default=40)
    parser.add_argument("--batch-size", type=int, default=512)
    parser.add_argument("--false-positive-rate", type=float, default=0.01)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    try:
        metadata = train(
            dataset_dir=args.dataset_dir,
            output_dir=args.output_dir,
            max_train_samples=args.max_train_samples,
            max_evaluation_samples=args.max_evaluation_samples,
            epochs=args.epochs,
            batch_size=args.batch_size,
            false_positive_rate=args.false_positive_rate,
            seed=args.seed,
        )
    except (OSError, ValueError, RuntimeError) as error:
        parser.exit(1, f"Error: {error}\n")

    print(json.dumps(metadata["test_metrics"], ensure_ascii=False, indent=2))
    print(f"Modelo guardado en: {args.output_dir.resolve()}")


if __name__ == "__main__":
    main()
