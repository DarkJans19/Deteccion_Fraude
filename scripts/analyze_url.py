"""Analiza una URL con el autoencoder entrenado."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from url_analysis.hybrid import predict


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("url", help="URL HTTP(S) que se desea analizar")
    parser.add_argument(
        "--model-dir", type=Path, default=ROOT / "artifacts" / "url_autoencoder"
    )
    args = parser.parse_args()
    try:
        result = predict(args.url, args.model_dir)
    except (OSError, ValueError, RuntimeError) as error:
        parser.exit(1, f"Error: {error}\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
