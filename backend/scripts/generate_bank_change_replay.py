"""Generate a frontend fixture from saved real evidence; no live services."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app.evidence import BankChangeEvidenceRepository, DEFAULT_BANK_CHANGE_RUN_ID


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", default=DEFAULT_BANK_CHANGE_RUN_ID)
    parser.add_argument("--output", type=Path,
                        default=BACKEND.parent / "contracts" / "fixtures" / "bank_change_replay.json")
    arguments = parser.parse_args()
    replay = BankChangeEvidenceRepository().get_run(arguments.run_id)
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_text(json.dumps(replay.model_dump(mode="json"), indent=2, ensure_ascii=False) + "\n")
    print(f"Generated recorded replay {replay.run_id} from {replay.source_file}")
    print(f"Source SHA-256: {replay.source_sha256}; events: {len(replay.events)}")
    print(f"Output: {arguments.output}")


if __name__ == "__main__":
    main()
