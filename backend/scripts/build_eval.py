"""Generate the committed planted-errors suite's PDFs without any live calls."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys


BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app.evaluation.suite import DEFAULT_OUTPUT_DIR, DEFAULT_SUITE_PATH, build_suite


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", type=Path, default=DEFAULT_SUITE_PATH)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_DIR)
    options = parser.parse_args()
    manifest = build_suite(options.output, path=options.suite)
    print(f"Generated {len(manifest['items'])} deterministic PDFs for {manifest['suite']}")
    print(f"Suite SHA-256: {manifest['suite_sha256']}")
    print(f"Manifest: {options.output / 'manifest.json'}")


if __name__ == "__main__":
    main()
