#!/usr/bin/env python3
"""Run the three requested live transfer probes with a fully masked transcript."""

import argparse
import asyncio
from datetime import datetime, timezone
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.adapters.bank import BankError, LiveBankAdapter
from app.adapters.nessie_diagnostics import NessieTranscriptTransport
from app.config import Settings
from app.store import Store


async def main(report: Path, paired_probe: bool = False, docs_probe: bool = False):
    settings = Settings()
    store = Store()
    store.initialize()
    transport = NessieTranscriptTransport(settings.nessie_api_key.get_secret_value(), report)
    try:
        bank = LiveBankAdapter(key=settings.nessie_api_key, base_url=settings.nessie_base_url,
                               store=store, transport=transport)
        if docs_probe:
            for endpoint in ["/documentation", "/openapi.json", "/swagger.json"]:
                try:
                    await bank._request("GET", endpoint)
                except BankError as error:
                    print(str(error), flush=True)
            return
        directory = store.list_records("bank_directory")
        company = next(row for row in directory if row["role"] == "company" and row["mode"] == "live")
        vendor = next(row for row in directory if row["role"] == "vendor" and row["mode"] == "live")
        path = f"/accounts/{company['account_id']}/transfers"
        common = {"amount": 1.0, "transaction_date": datetime.now(timezone.utc).date().isoformat(),
                  "description": "Countersign transfer schema diagnostic; USD 1.00"}
        if paired_probe:
            bank.payment_method = "paired_withdrawal_deposit"
            payment = await bank.transfer(
                company["account_id"], vendor["account_id"], 100,
                "Countersign paired withdrawal/deposit diagnostic; USD 1.00",
            )
            print(payment.model_dump_json(), flush=True)
            await bank.list_transfers(company["account_id"])
            return
        probes = [
            ("a_documented_plus_pending", "POST", path,
             {**common, "medium": "balance", "payee_id": vendor["account_id"], "status": "pending"}),
            ("b_requested_body_shape", "POST", path, {**common, "status": "pending"}),
            ("c_transfer_objects", "GET", path, None),
        ]
        for name, method, endpoint, payload in probes:
            print(name, flush=True)
            try:
                await bank._request(method, endpoint, payload=payload)
            except BankError as error:
                print(str(error), flush=True)
    finally:
        await transport.aclose()
        store.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, default=Path("docs/nessie_diagnostics.jsonl"))
    parser.add_argument("--paired-probe", action="store_true")
    parser.add_argument("--docs-probe", action="store_true")
    arguments = parser.parse_args()
    asyncio.run(main(arguments.report, arguments.paired_probe, arguments.docs_probe))
