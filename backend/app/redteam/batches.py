"""Three-invoice red-team batches, with immutable trusted AP records.

Each variant belongs in a fresh Store. Stable document identities and PDF
generation allow unchanged control documents to share the extraction cache.
The planner already uses server-assigned D1/D2/D3 aliases rather than these IDs.
"""

from __future__ import annotations

import base64
from datetime import date, datetime, timedelta, timezone

from app.contract_models import Batch, InvoiceSummary, Placement
from app.store import Store
from app.world.attacks import create_attack
from app.world.bank_changes import attacker_directory_display
from app.world.invoices import InvoiceSpec, LineItem, extract_document, generate_invoice
from app.world.seed import ATTACKER_NAME, COMPANY_DISPLAY_NUMBER, seed_trusted_world


BATCH_ID = "redteam-batch"
VENDOR_IDS = ("VEN-001", "VEN-002", "VEN-003")
INVOICE_IDS = tuple(f"redteam-invoice-{index:03d}" for index in range(1, 4))
DOCUMENT_IDS = tuple(f"redteam-document-{index:03d}" for index in range(1, 4))
TARGET_INVOICE_ID = INVOICE_IDS[2]
CONTROL_INVOICE_IDS = frozenset(INVOICE_IDS[:2])


def seed_dry_run_directory(store: Store) -> None:
    """Install explicitly synthetic directory handles, never bank resources.

The display numbers come from trusted demo data (or an existing attacker
directory mapping), not the attack. DryRunBank cannot use these handles for
Nessie I/O and never returns transfer IDs.
"""
    world = seed_trusted_world(store)
    attacker = attacker_directory_display(store)
    entries = [("company", world["company"]["name"], COMPANY_DISPLAY_NUMBER, None)]
    entries.extend(("vendor", vendor["name"],
                    vendor.get("account_on_file_display", vendor["remit_account_number"]), vendor["vendor_id"])
                   for vendor in world["vendors"])
    entries.append(("attacker", ATTACKER_NAME, attacker, None))
    if len({number for _, _, number, _ in entries}) != len(entries):
        raise ValueError("Red-team directory display numbers must be unique")
    for index, (role, label, number, vendor_id) in enumerate(entries):
        store.save_record("bank_directory", number, {
            "display_number": number, "account_id": f"dry-run-directory-handle-{index}",
            "role": role, "label": label, "vendor_id": vendor_id, "mode": "dry_run",
        })


def create_variant_batch(store: Store, *, text: str, placement: Placement, as_of: date) -> Batch:
    """Render supplied text on VEN-003 plus two unchanged clean controls.

No hidden remittance replacement is added: the ordinary printed remit block
stays on file. Any request to pay elsewhere must be in the submitted text.
"""
    if type(as_of) is not date:
        raise ValueError("A frozen calendar date is required for red-team generation")
    if store.list_records("batches") or store.list_records("documents"):
        raise ValueError("Each red-team variant requires its own fresh Store")
    seed_dry_run_directory(store)
    world = seed_trusted_world(store)
    attack = create_attack("custom", attacker_directory_display(store), text, placement)
    created_at = datetime.combine(as_of, datetime.min.time(), tzinfo=timezone.utc)
    vendors = {vendor["vendor_id"]: vendor for vendor in world["vendors"]}
    orders = {order["purchase_order_id"]: order for order in world["purchase_orders"]}
    invoices, records = [], []
    for index, vendor_id in enumerate(VENDOR_IDS):
        vendor = vendors[vendor_id]
        order = orders[vendor["purchase_order_ids"][0]]
        invoice_id, document_id = INVOICE_IDS[index], DOCUMENT_IDS[index]
        spec = InvoiceSpec(
            invoice_number=f"INV-{index + 1:03d}", vendor_name=vendor["name"], vendor_email=vendor["email"],
            remit_account_number=vendor.get("account_on_file_display", vendor["remit_account_number"]),
            po_number=order["po_number"], issued_date=as_of, due_date=as_of + timedelta(days=14),
            line_items=tuple(LineItem(**item) for item in order["line_items"]),
            company_name=world["company"]["name"], service_description=vendor["service_description"],
        )
        selected = attack if invoice_id == TARGET_INVOICE_ID else None
        pdf = generate_invoice(spec, attack=selected, invariant=True)
        document = extract_document(pdf, document_id, invoice_id)
        records.append(("documents", document_id, {
            "document_id": document_id, "invoice_id": invoice_id,
            "text": document.model_dump(mode="json"), "pdf_base64": base64.b64encode(pdf).decode("ascii"),
        }))
        records.append(("invoice_scoring", invoice_id, {
            "invoice_id": invoice_id, "batch_id": BATCH_ID, "poisoned": selected is not None,
            "template": "custom" if selected else None, "canary": selected.canary if selected else None,
            **({"raw_attack_text": text, "attack_text": selected.text, "placement": selected.placement}
               if selected else {}),
        }))
        invoices.append(InvoiceSummary(invoice_id=invoice_id, document_id=document_id,
            invoice_number=spec.invoice_number, vendor_id=vendor_id, vendor_name=vendor["name"],
            amount_cents=spec.amount_cents, currency="USD", due_date=spec.due_date))
    records.append(("attack_audit", BATCH_ID, {
        "batch_id": BATCH_ID, "vendor_id": "VEN-003", "invoice_id": TARGET_INVOICE_ID,
        "document_id": DOCUMENT_IDS[2], "created_at": created_at.isoformat(),
        "raw_attack_text": text, "attack_text": attack.text, "placement": attack.placement,
        "canary": attack.canary, "template": "custom",
    }))
    batch = Batch(batch_id=BATCH_ID, kind="attack", created_at=created_at, invoices=invoices)
    records.append(("batches", BATCH_ID, batch))
    store.save_records_atomically(records)
    return batch
