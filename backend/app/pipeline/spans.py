"""Verify extractor claims against original document text, without an LLM."""

from typing import Any
from decimal import Decimal, InvalidOperation
import re

from app.contract_models import DocumentText, ExtractedField, FieldsExtractedPayload, Handle, SourceSpan
from app.pipeline.extraction_models import RawExtraction, RawSpan


_MONEY = re.compile(r"(?<![\w.])(?:\$|USD\s*)?([0-9][0-9,]*\.[0-9]{2})(?![\d.])")


def span_source_verified(span: SourceSpan | None, document: DocumentText) -> bool:
    """Recheck the original slice, including claims restored from a cache."""
    if span is None or not span.span_verified or span.document_id != document.document_id:
        return False
    lines = [line.text for page in document.pages if page.page == span.page
             for line in page.lines if line.line == span.line]
    return len(lines) == 1 and (
        0 <= span.char_start < span.char_end <= len(lines[0])
        and lines[0][span.char_start:span.char_end] == span.quote
    )


def field_source_verified(field: ExtractedField | None, document: DocumentText) -> bool:
    """The same deterministic payment-field check used by the run policy path."""
    if field is None or not span_source_verified(field.span, document):
        return False
    if field.name == "amount_due_cents":
        if type(field.value) is not int:
            return False
        for match in _MONEY.finditer(field.span.quote):
            try:
                if Decimal(match[1].replace(",", "")) * 100 == field.value:
                    return True
            except InvalidOperation:
                continue
        return False
    if not isinstance(field.value, str) or not field.value:
        return False
    if field.name == "remit_account_number":
        normalized = re.sub(r"[\s-]", "", field.value)
        return bool(normalized) and normalized in re.sub(r"[\s-]", "", field.span.quote)
    return " ".join(field.value.split()).casefold() in " ".join(field.span.quote.split()).casefold()


def _normalized(text: str) -> tuple[str, list[int], list[int]]:
    chars, starts, ends = [], [], []
    for index, char in enumerate(text):
        if char.isspace():
            if chars and chars[-1] == " ":
                ends[-1] = index + 1
            else:
                chars.append(" ")
                starts.append(index)
                ends.append(index + 1)
        else:
            chars.append(char)
            starts.append(index)
            ends.append(index + 1)
    return "".join(chars), starts, ends


def _locate(line: str, quote: str) -> tuple[int, int] | None:
    if not quote or not quote.strip():
        return None
    exact = line.find(quote)
    if exact >= 0:
        return exact, exact + len(quote)
    normalized_quote = " ".join(quote.split())
    normalized_line, starts, ends = _normalized(line)
    match = normalized_line.find(normalized_quote)
    if match < 0:
        return None
    return starts[match], ends[match + len(normalized_quote) - 1]


def verify_span(document: DocumentText, raw_span: RawSpan | SourceSpan | dict[str, Any]) -> SourceSpan:
    """Find a quote at its claimed line, then search every document line.

    Successful results retain the exact original substring, including its
    whitespace, so `line.text[char_start:char_end] == quote` always holds.
    A SourceSpan has one line coordinate: cross-line quotes are not certified
    as if they were present on a single line.
    """
    if isinstance(raw_span, RawSpan):
        claim = raw_span
    elif isinstance(raw_span, SourceSpan):
        claim = RawSpan(quote=raw_span.quote, page=raw_span.page, line=raw_span.line)
    else:
        claim = RawSpan.model_validate({key: raw_span[key] for key in ("quote", "page", "line")})
    lines = [(page.page, line.line, line.text) for page in document.pages for line in page.lines]
    ordered = sorted(lines, key=lambda value: (value[0] != claim.page or value[1] != claim.line, value[0], value[1]))
    for page, line, text in ordered:
        match = _locate(text, claim.quote)
        if match is not None:
            start, end = match
            return SourceSpan(
                document_id=document.document_id, page=page, line=line,
                char_start=start, char_end=end, quote=text[start:end], span_verified=True,
            )
    return SourceSpan(
        document_id=document.document_id, page=claim.page, line=claim.line,
        char_start=0, char_end=0, quote=claim.quote, span_verified=False,
    )


def to_payload(document: DocumentText, raw: RawExtraction | dict[str, Any], model: str) -> FieldsExtractedPayload:
    """Validate every quote before constructing public fields and opaque handles."""
    extraction = raw if isinstance(raw, RawExtraction) else RawExtraction.model_validate(raw)
    fields = []

    def add(name, field, kind):
        fields.append(ExtractedField(
            name=name, value=field.value, type=kind,
            span=verify_span(document, field), trust="untrusted",
        ))

    for name in (
        "invoice_number", "vendor_name", "vendor_email", "invoice_date", "due_date",
        "po_number", "amount_due_cents", "currency", "remit_account_number",
    ):
        kind = (
            "money" if name == "amount_due_cents" else
            "account_number" if name == "remit_account_number" else
            "date" if name in {"invoice_date", "due_date"} else "string"
        )
        add(name, getattr(extraction, name), kind)
    for index, item in enumerate(extraction.line_items):
        for name in ("description", "quantity", "unit_price_cents", "amount_cents"):
            kind = "money" if name.endswith("_cents") else "number" if name == "quantity" else "string"
            add(f"line_items.{index}.{name}", getattr(item, name), kind)
    return FieldsExtractedPayload(
        invoice_id=document.invoice_id, extractor_model=model, fields=fields,
        instruction_like_spans=[verify_span(document, claim) for claim in extraction.instruction_like_spans],
        handles=[Handle(
            name=f"${document.invoice_id}.{field.name}", type=field.type,
            trust="untrusted", span_verified=field.span.span_verified,
        ) for field in fields],
    )
