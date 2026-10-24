"""Recover invoice references from verified source, never payment timestamps."""

from datetime import date
import re

from app.contract_models import DocumentText, FieldsExtractedPayload
from app.pipeline.spans import field_source_verified


def normalized_invoice_date(value):
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value.strip()).isoformat()
    except ValueError:
        return None


def verified_invoice_references(store, run_id, invoice_id):
    """Return grounded date/PO values from this payment's saved extraction.

    Old transfers may lack explicit reference metadata. Their original bound
    extraction is still evidence; a transfer's timestamp or a batch creation
    date is not evidence of the printed invoice date.
    """
    if not run_id or not invoice_id:
        return {}
    raw = store.get_record("extractions", f"{run_id}:{invoice_id}")
    if raw is None or raw.get("run_id") != run_id or raw.get("invoice_id") != invoice_id:
        return {}
    try:
        extraction = FieldsExtractedPayload.model_validate({
            key: value for key, value in raw.items() if key not in {"run_id", "extraction_id"}
        })
    except (ValueError, TypeError):
        return {}
    result = {}
    for name in ("invoice_date", "po_number"):
        fields = [field for field in extraction.fields if field.name == name]
        if len(fields) != 1 or fields[0].span is None:
            continue
        field = fields[0]
        source = store.get_record("documents", field.span.document_id)
        try:
            document = DocumentText.model_validate(source["text"]) if source else None
        except (ValueError, TypeError, KeyError):
            continue
        if (document is None or document.invoice_id != invoice_id
                or document.document_id != field.span.document_id
                or document.document_type not in (None, "invoice")
                or not field_source_verified(field, document)):
            continue
        normalized = " ".join(field.value.split()).casefold()
        quote = " ".join(field.span.quote.split()).casefold()
        if not re.search(rf"(?<!\w){re.escape(normalized)}(?!\w)", quote):
            continue
        if name == "invoice_date":
            value = normalized_invoice_date(field.value)
        else:
            value = field.value.strip()
        if value:
            result[name] = value
    return result
