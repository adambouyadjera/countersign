"""One real, quarantined extraction check; never calls the bank or AP agent."""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.adapters.factory import build_adapters
from app.config import Settings
from app.contract_models import DocumentText
from app.pipeline.extractor import ExtractorError
from app.store import Store
from app.world.batches import create_bank_change_batch


async def check(*, force_haiku=False, output: Path):
    # Verification must make a real request, even if this PDF was cached before.
    settings = Settings().model_copy(update={"extractor_mode": "live", "extractor_cache": "off"})
    store = Store(":memory:")
    store.initialize()
    extractor = build_adapters(settings, store).extractor
    report = {"checked_at": datetime.now(timezone.utc).isoformat(), "force_haiku": force_haiku,
              "configured_primary": settings.extractor_model,
              "configured_fallback": settings.extractor_fallback_model, "cache_enabled": False}
    try:
        batch = create_bank_change_batch(store)
        invoice = next(item for item in batch.invoices if item.vendor_id == "VEN-003")
        document = DocumentText.model_validate(store.get_record("documents", invoice.document_id)["text"])
        try:
            result = await extractor.extract(document, force_haiku=force_haiku, bypass_cache=True)
            report.update(success=True, extraction=result.model_dump(mode="json"),
                          fields=len(result.fields),
                          verified_fields=sum(bool(field.span and field.span.span_verified) for field in result.fields))
        except ExtractorError as error:
            report.update(success=False, error_type=type(error).__name__, cause=error.cause,
                          http_status=error.http_status,
                          attempts=[item.model_dump(mode="json") for item in getattr(error, "extraction_attempts", [])])
        report["http_observations"] = list(extractor.fallback.http_observations)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, indent=2) + "\n")
        printable = {key: value for key, value in report.items() if key not in {"extraction"}}
        if report.get("success"):
            printable["extraction_provenance"] = report["extraction"]["extraction_provenance"]
        print(json.dumps(printable), flush=True)
        return bool(report.get("success"))
    finally:
        await extractor.aclose()
        store.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force-haiku", action="store_true", help="Skip Gemini and cached data for this real verification")
    parser.add_argument("--output", type=Path, default=BACKEND / "docs" / "haiku_extraction_live.json")
    args = parser.parse_args(argv)
    if not asyncio.run(check(force_haiku=args.force_haiku, output=args.output)):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
