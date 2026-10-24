#!/usr/bin/env python3
"""Create/reuse the explicit live or offline Countersign demo bank world."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import sys

# Support `python scripts/seed_world.py` without installing this repository.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.adapters.factory import build_adapters
from app.config import Settings
from app.store import DEFAULT_DATABASE_PATH, Store
from app.world.seed import seed_world


async def _seed(database: Path) -> dict:
    settings = Settings()
    store = Store(database)
    store.initialize()
    try:
        return await seed_world(store, build_adapters(settings, store).bank)
    finally:
        store.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE_PATH)
    args = parser.parse_args()
    try:
        result = asyncio.run(_seed(args.database))
    except Exception:
        # HTTP exception reprs can contain Nessie's credential query parameter.
        print("World seed failed; verify bank configuration and connectivity. Completed IDs remain saved for a retry.", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
