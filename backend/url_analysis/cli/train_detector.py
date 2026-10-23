"""Entrena el autoencoder y el clasificador hibrido de URLs."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[3]
MODULE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from backend.url_analysis.autoencoder import train
from backend.url_analysis.hybrid import train_classifier


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dataset-dir", type=Path, default=MODULE_ROOT / "data" / "processed"
    )
    parser.add_argument(
        "--output-dir", type=Path, default=MODULE_ROOT / "artifacts" / "url_detector"
    )
    parser.add_argument("--max-train-samples", type=int, default=200_000)
    parser.add_argument("--max-evaluation-samples", type=int, default=100_000)
    parser.add_argument("--autoencoder-epochs", type=int, default=40)
    parser.add_argument("--classifier-epochs", type=int, default=30)
    parser.add_argument("--batch-size", type=int, default=512)
    parser.add_argument("--decision-fpr", type=float, default=0.01)
    parser.add_argument("--review-fpr", type=float, default=0.05)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    try:
        train(
            dataset_dir=args.dataset_dir,
            output_dir=args.output_dir,
            max_train_samples=min(args.max_train_samples, 100_000),
            max_evaluation_samples=args.max_evaluation_samples,
            epochs=args.autoencoder_epochs,
            batch_size=args.batch_size,
            seed=args.seed,
        )
        metadata = train_classifier(
            dataset_dir=args.dataset_dir,
            model_dir=args.output_dir,
            max_train_samples=args.max_train_samples,
            max_evaluation_samples=args.max_evaluation_samples,
            epochs=args.classifier_epochs,
            batch_size=args.batch_size,
            decision_fpr=args.decision_fpr,
            review_fpr=args.review_fpr,
            seed=args.seed,
        )
    except (OSError, ValueError, RuntimeError) as error:
        parser.exit(1, f"Error: {error}\n")

    print(json.dumps(metadata["test_metrics"], ensure_ascii=False, indent=2))
    print(f"Detector hibrido guardado en: {args.output_dir.resolve()}")


if __name__ == "__main__":
    main()
