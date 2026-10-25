"""Translate accepted bills and confirmed bank submissions into journal facts."""

from app.world.vendors import record_vendor_payment


class Bookkeeper:
    def __init__(self, store, ledger, reconciler):
        self.store, self.ledger, self.reconciler = store, ledger, reconciler

    async def before_payment(self):
        await self.reconciler.ensure_baseline()

    def accept_bill(self, *, run_id, invoice_id, invoice_number, vendor_id,
                    amount_cents, created_by="person-ap-agent", **unused):
        vendor = self.store.get_record("vendors", vendor_id) if vendor_id else None
        if vendor is None:
            raise ValueError("A bill needs a trusted vendor binding before posting")
        entry, created = self.ledger.post_bill_with_status({
            "invoice_id": invoice_id, "invoice_number": invoice_number,
            "vendor_id": vendor_id, "vendor_name": vendor["name"],
            "amount_cents": amount_cents, "currency": "USD",
        }, run_id=run_id, created_by=created_by)
        return [entry] if created else []

    def payment_postings(self, event):
        # Comparison payments remain bank/audit evidence, never company books.
        if event.type != "payment.executed" or event.lane != "countersign":
            return []
        payment = event.payload.payment
        transfer = self.store.get_record("transfers", payment.transfer_id) or {}
        vendor_id = transfer.get("vendor_id")
        run = self.store.get_run(event.run_id)
        batch = self.store.get_record("batches", run.batch_id) if run else None
        invoice = next((item for item in (batch or {}).get("invoices", [])
                        if item["invoice_id"] == event.payload.invoice_id), None)
        vendor_id = vendor_id or (invoice or {}).get("vendor_id")
        if vendor_id is None:
            # Legacy approval contexts bind the reviewed invoice even if its
            # generated batch is no longer available in an old session.
            context = next((item for item in self.store.list_records("approval_contexts")
                            if item.get("run_id") == event.run_id
                            and item.get("invoice_id") == event.payload.invoice_id), {})
            vendor_id = context.get("vendor_id")
        if vendor_id is None:
            raise ValueError("Executed invoice payment has no vendor binding for the books")
        created_by = transfer.get("created_by") or "person-ap-agent"
        accepted = any(entry.source.kind == "invoice" for entry in
                       self.ledger.list_entries(invoice_id=event.payload.invoice_id))
        postings = [] if accepted else self.accept_bill(
            run_id=event.run_id, invoice_id=event.payload.invoice_id,
            invoice_number=transfer.get("invoice_number") or (invoice or {}).get("invoice_number", event.payload.invoice_id),
            vendor_id=vendor_id, amount_cents=(invoice or {}).get("amount_cents", payment.amount_cents),
            created_by=created_by,
        )
        entry, created = self.ledger.post_payment_with_status(payment, run_id=event.run_id,
            invoice_id=event.payload.invoice_id, vendor_id=vendor_id,
            created_by=created_by, approval_id=transfer.get("approval_id"))
        if created:
            record_vendor_payment(self.store, vendor_id, entry_id=entry.entry_id,
                date=entry.date.isoformat(), amount_cents=payment.amount_cents,
                account_display=payment.to_display_number)
            postings.append(entry)
        return postings
