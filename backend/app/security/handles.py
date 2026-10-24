"""Opaque document references; their values never leave the server-side table."""

from __future__ import annotations

import re
from typing import Any

from app.contract_models import DocumentText, ExtractedField, FieldsExtractedPayload, Handle
from app.security.labels import DocumentSpan, Labeled


_SCALARS = {
    "invoice_number": "string", "vendor_name": "string", "vendor_email": "string",
    "invoice_date": "date", "due_date": "date", "po_number": "string",
    "amount_due_cents": "money", "currency": "string",
    "remit_account_number": "account_number",
}
_ITEM = re.compile(r"line_items\.(0|[1-9][0-9]{0,3})\.(description|quantity|unit_price_cents|amount_cents)\Z")


def field_type(name: str) -> str:
    """Only schema-defined names can contribute text to the planner prompt."""
    if name in _SCALARS:
        return _SCALARS[name]
    match = _ITEM.fullmatch(name)
    if match:
        suffix = match[2]
        return "number" if suffix == "quantity" else "string" if suffix == "description" else "money"
    raise ValueError("Extraction contains an unsupported field name")


def _source(document: DocumentText, field: ExtractedField) -> DocumentSpan:
    span = field.span
    verified = False
    if span is not None and span.document_id == document.document_id and span.span_verified:
        lines = [line.text for page in document.pages if page.page == span.page
                 for line in page.lines if line.line == span.line]
        verified = len(lines) == 1 and (
            0 <= span.char_start < span.char_end <= len(lines[0])
            and lines[0][span.char_start:span.char_end] == span.quote
        )
    return DocumentSpan(
        document_id=document.document_id,
        page=span.page if span else 0, line=span.line if span else 0,
        char_start=span.char_start if span else 0, char_end=span.char_end if span else 0,
        span_verified=verified,
    )


class HandleTable:
    """Batch positions are assigned by the server, never by extracted values."""

    def __init__(self) -> None:
        self.values: dict[str, Labeled[Any]] = {}
        self.handles: list[Handle] = []
        self.invoices: dict[str, str] = {}
        self.invoice_handles: dict[str, list[Handle]] = {}

    def add_invoice(
        self, document: DocumentText, extraction: FieldsExtractedPayload, position: int,
    ) -> list[Handle]:
        if type(position) is not int or position < 1:
            raise ValueError("Document position must be a positive server-assigned integer")
        alias = f"D{position}"
        if alias in self.invoices or document.invoice_id in self.invoices.values():
            raise ValueError("Document is already registered in this batch")
        if extraction.invoice_id != document.invoice_id:
            raise ValueError("Extraction does not belong to this document")
        handles, values = [], {}
        for field in extraction.fields:
            kind = field_type(field.name)
            if field.type != kind:
                raise ValueError("Extraction field type does not match its schema")
            name = f"${alias}.{field.name}"
            if name in values:
                raise ValueError("Extraction contains duplicate fields")
            source = _source(document, field)
            handles.append(Handle(name=name, type=kind, trust="untrusted", span_verified=source.span_verified))
            values[name] = Labeled(field.value, frozenset({source}))
        # Commit only after validating every field, avoiding a partial table.
        self.invoices[alias] = document.invoice_id
        self.invoice_handles[alias] = handles
        self.handles.extend(handles)
        self.values.update(values)
        return handles
