"""Analiza una URL con Google Safe Browsing y el detector local entrenado."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[3]
MODULE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from backend.url_analysis.layered import analyze
from backend.url_analysis.safe_browsing import SafeBrowsingClient


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("url", help="URL HTTP(S) que se desea analizar")
    parser.add_argument(
        "--model-dir", type=Path, default=MODULE_ROOT / "artifacts" / "url_detector"
    )
    parser.add_argument(
        "--safe-browsing-timeout",
        type=float,
        default=5.0,
        help="segundos maximos para la consulta remota (predeterminado: 5)",
    )
    safe_browsing_mode = parser.add_mutually_exclusive_group()
    safe_browsing_mode.add_argument(
        "--skip-safe-browsing",
        action="store_true",
        help="omite expresamente la consulta a Google Safe Browsing",
    )
    safe_browsing_mode.add_argument(
        "--require-safe-browsing",
        action="store_true",
        help="falla si la clave no existe o la consulta remota no responde",
    )
    args = parser.parse_args()
    try:
        client = None
        if not args.skip_safe_browsing:
            client = SafeBrowsingClient.from_environment(args.safe_browsing_timeout)
        result = analyze(
            args.url,
            args.model_dir,
            safe_browsing_client=client,
            require_safe_browsing=args.require_safe_browsing,
            safe_browsing_enabled=not args.skip_safe_browsing,
        )
    except (OSError, ValueError, RuntimeError) as error:
        parser.exit(1, f"Error: {error}\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
