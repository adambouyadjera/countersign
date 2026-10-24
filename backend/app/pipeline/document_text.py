"""Canonical PDF extraction used by document storage and the quarantine lane."""

from io import BytesIO

import pdfplumber

from app.contract_models import DocumentLine, DocumentPage, DocumentText


def extract_document(pdf_bytes: bytes, document_id: str, invoice_id: str) -> DocumentText:
    """Read actual PDF text into stable, one-based page and line coordinates.

    The tight vertical tolerance preserves the generator's hidden one-point
    text as separate lines instead of interleaving it with neighboring content.
    Whitespace is retained for exact source-span character offsets.
    """
    pages = []
    with pdfplumber.open(BytesIO(pdf_bytes)) as pdf:
        for number, page in enumerate(pdf.pages, 1):
            text = page.extract_text(x_tolerance=1, y_tolerance=0.5) or ""
            pages.append(DocumentPage(
                page=number,
                lines=[DocumentLine(line=index, text=line) for index, line in enumerate(text.splitlines(), 1)],
            ))
    return DocumentText(document_id=document_id, invoice_id=invoice_id, pages=pages)


def numbered_lines(document: DocumentText) -> str:
    """Present only source text and explicit coordinates to the extractor."""
    return "\n".join(
        f"[page={page.page} line={line.line}] {line.text}"
        for page in document.pages for line in page.lines
    )
