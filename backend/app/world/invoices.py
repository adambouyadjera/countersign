"""One-page, extractable vendor invoices and canonical page/line extraction."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from io import BytesIO
from pathlib import Path
from threading import Lock

import reportlab
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

from app.contract_models import DocumentText
from app.world.attacks import InvoiceAttack


_FONT_LOCK = Lock()
_FONT_NAME = "CountersignInvoice"
_FONT_BOLD = "CountersignInvoiceBold"


@dataclass(frozen=True)
class LineItem:
    description: str
    quantity: int
    unit_price_cents: int

    def __post_init__(self) -> None:
        if not self.description or self.quantity <= 0 or self.unit_price_cents < 0:
            raise ValueError("Invoice line items require a description and valid amounts")
        if type(self.quantity) is not int or type(self.unit_price_cents) is not int:
            raise ValueError("Invoice quantities and cent amounts must be integers")

    @property
    def amount_cents(self) -> int:
        return self.quantity * self.unit_price_cents


@dataclass(frozen=True)
class InvoiceSpec:
    invoice_number: str
    vendor_name: str
    vendor_email: str
    remit_account_number: str
    po_number: str
    issued_date: date
    due_date: date
    line_items: tuple[LineItem, ...]
    company_name: str = "Fogline Festival"
    service_description: str = "Three-day San Francisco music festival"

    def __post_init__(self) -> None:
        if not self.line_items or len(self.line_items) > 6:
            raise ValueError("A one-page invoice requires between one and six line items")
        if self.due_date < self.issued_date:
            raise ValueError("Invoice due date cannot precede issue date")

    @property
    def amount_cents(self) -> int:
        return sum(item.amount_cents for item in self.line_items)


def invoice_amount_cents(spec: InvoiceSpec, attack: InvoiceAttack | None = None) -> int:
    """Return the printed total, without modifying any trusted purchase order."""
    return spec.amount_cents + (attack.amount_increase_cents if attack else 0)


def _register_fonts() -> None:
    # ReportLab ships these fonts, so generated invoices do not depend on the
    # server's system fonts. TrueType keeps Latin accented custom text extractable.
    with _FONT_LOCK:
        if _FONT_NAME not in pdfmetrics.getRegisteredFontNames():
            font_dir = Path(reportlab.__file__).parent / "fonts"
            pdfmetrics.registerFont(TTFont(_FONT_NAME, str(font_dir / "Vera.ttf")))
            pdfmetrics.registerFont(TTFont(_FONT_BOLD, str(font_dir / "VeraBd.ttf")))


def _wrap(text: str, font: str, size: float, width: float) -> list[str]:
    """Wrap plain text by actual glyph width, including long unbroken tokens."""
    result: list[str] = []
    for paragraph in text.splitlines() or [""]:
        line = ""
        for char in paragraph:
            candidate = line + char
            if line and pdfmetrics.stringWidth(candidate, font, size) > width:
                split = line.rfind(" ")
                if split > 0:
                    result.append(line[:split])
                    line = line[split + 1:] + char
                else:
                    result.append(line)
                    line = char
            else:
                line = candidate
        result.append(line)
    return result


def _money(amount_cents: int) -> str:
    return f"${amount_cents // 100:,}.{amount_cents % 100:02d}"


def _validate_glyphs(texts: list[str], *, allow_line_breaks: bool = False) -> None:
    """Reject missing glyphs before creating any PDF, without altering fonts.

    The packaged Vera faces cover all built-in invoice and attack text, including
    Latin accents. Custom content must use real glyphs in both faces; assigning
    an unsupported codepoint to .notdef would preserve extraction but draw a box.
    """
    codepoints = {
        ord(char) for text in texts for char in text
        if not (allow_line_breaks and char in "\r\n")
    }
    if any(0xD800 <= code <= 0xDFFF for code in codepoints):
        raise ValueError("Invoice text contains an invalid unpaired Unicode surrogate")
    with _FONT_LOCK:
        faces = [pdfmetrics.getFont(name).face for name in (_FONT_NAME, _FONT_BOLD)]
        unsupported = sorted(
            code for code in codepoints if any(not face.charToGlyph.get(code) for face in faces)
        )
    if unsupported:
        characters = ", ".join(f"U+{code:04X}" for code in unsupported)
        raise ValueError(f"Invoice font does not support these characters: {characters}")


def generate_invoice(spec: InvoiceSpec, attack: InvoiceAttack | None = None) -> bytes:
    """Generate the original PDF; generation metadata never names an attack.

    White attack content is deliberately drawn at 1 point on an unpainted white
    region. The content remains in the PDF text stream for extraction.
    """
    _register_fonts()
    _validate_glyphs([
        spec.invoice_number, spec.vendor_name, spec.vendor_email,
        spec.remit_account_number, spec.po_number, spec.company_name,
        spec.service_description,
    ])
    _validate_glyphs([
        *(item.description for item in spec.line_items),
        *([attack.text, attack.canary] if attack else []),
    ], allow_line_breaks=True)
    output = BytesIO()
    pdf = canvas.Canvas(output, pagesize=letter, pageCompression=1)
    pdf.setTitle(f"Invoice {spec.invoice_number}")
    pdf.setAuthor(spec.vendor_name)
    navy = colors.HexColor("#143D45")
    ink = colors.HexColor("#203137")
    gray = colors.HexColor("#55676D")
    pale = colors.HexColor("#EDF3F2")
    left, right = 48, 564

    pdf.setFillColor(navy)
    pdf.rect(left, 690, right - left, 62, fill=1, stroke=0)
    pdf.setFillColor(colors.white)
    vendor_size = 20
    while pdfmetrics.stringWidth(spec.vendor_name, _FONT_BOLD, vendor_size) > 484:
        vendor_size -= 0.5
    pdf.setFont(_FONT_BOLD, vendor_size)
    pdf.drawString(64, 721, spec.vendor_name)
    pdf.setFont(_FONT_NAME, 9)
    pdf.drawString(64, 704, spec.vendor_email)

    pdf.setFillColor(ink)
    pdf.setFont(_FONT_BOLD, 23)
    pdf.drawString(left, 653, "INVOICE")
    pdf.setFont(_FONT_BOLD, 11)
    pdf.drawRightString(right, 655, spec.invoice_number)
    pdf.setFont(_FONT_NAME, 9)
    pdf.setFillColor(gray)
    pdf.drawRightString(right, 638, f"Invoice date: {spec.issued_date.isoformat()}")
    pdf.drawRightString(right, 622, f"Due date: {spec.due_date.isoformat()}")

    pdf.setFont(_FONT_BOLD, 9)
    pdf.setFillColor(gray)
    pdf.drawString(left, 603, "BILL TO")
    pdf.setFillColor(ink)
    pdf.setFont(_FONT_BOLD, 12)
    pdf.drawString(left, 584, spec.company_name)
    pdf.setFont(_FONT_NAME, 9)
    pdf.drawString(left, 568, "Accounts Payable | San Francisco, CA")
    pdf.drawString(left, 552, spec.service_description)
    pdf.setFont(_FONT_BOLD, 10)
    pdf.drawString(left, 525, f"Purchase order: {spec.po_number}")

    pdf.setFillColor(pale)
    pdf.rect(left, 484, right - left, 25, fill=1, stroke=0)
    pdf.setFillColor(ink)
    pdf.setFont(_FONT_BOLD, 9)
    pdf.drawString(58, 493, "DESCRIPTION")
    pdf.drawRightString(372, 493, "QTY")
    pdf.drawRightString(466, 493, "UNIT PRICE")
    pdf.drawRightString(554, 493, "AMOUNT")
    y = 464
    for item in spec.line_items:
        description_lines = _wrap(item.description, _FONT_NAME, 9, 280)
        if len(description_lines) > 2:
            raise ValueError("Line item description is too long for a one-page invoice")
        pdf.setFont(_FONT_NAME, 9)
        for index, line in enumerate(description_lines):
            pdf.drawString(58, y - index * 12, line)
        pdf.drawRightString(372, y, str(item.quantity))
        pdf.drawRightString(466, y, _money(item.unit_price_cents))
        pdf.drawRightString(554, y, _money(item.amount_cents))
        y -= max(28, len(description_lines) * 12 + 8)

    pdf.setStrokeColor(colors.HexColor("#C8D5D4"))
    pdf.line(348, 268, right, 268)
    pdf.setFont(_FONT_BOLD, 12)
    pdf.drawString(358, 246, "TOTAL (USD)")
    pdf.drawRightString(554, 246, _money(invoice_amount_cents(spec, attack)))

    pdf.setFont(_FONT_BOLD, 10)
    pdf.drawString(left, 287, "Remit to")
    pdf.setFont(_FONT_NAME, 9)
    pdf.drawString(left, 271, spec.vendor_name)
    pdf.drawString(left, 255, f"Account number: {spec.remit_account_number}")
    pdf.drawString(left, 239, f"Payment reference: {spec.invoice_number}")

    if attack:
        pdf.saveState()
        hidden = attack.placement == "hidden_white_text"
        size = 1 if hidden else 8
        # Keep even 1pt hidden lines beyond pdfplumber's default 3pt line
        # tolerance, otherwise adjacent lines are interleaved during extraction.
        leading = 5 if hidden else size + 3
        text_y = 204 if attack.placement != "footer" else 140
        pdf.setFillColor(colors.white if hidden else ink)
        lines = _wrap(attack.text, _FONT_NAME, size, right - left)
        # Valid 600-character input can contain wide glyphs. Keep it on one
        # page by reducing visible body size within a readable bounded range.
        while not hidden and len(lines) * leading > (text_y - 62) and size > 6:
            size -= 0.5
            leading = size + 3
            lines = _wrap(attack.text, _FONT_NAME, size, right - left)
        if len(lines) * leading > (text_y - 62):
            raise ValueError("Attack text is too long for its one-page placement")
        pdf.setFont(_FONT_NAME, size)
        for line in lines:
            pdf.drawString(left, text_y, line)
            text_y -= leading
        # The audit marker always stays in the PDF text stream, but never
        # appears on the visible invoice, even for visible notes and footers.
        pdf.setFillColor(colors.white)
        pdf.setFont(_FONT_NAME, 1)
        pdf.drawString(left, text_y, f"Reference: {attack.canary}")
        pdf.restoreState()

    pdf.setStrokeColor(colors.HexColor("#C8D5D4"))
    pdf.line(left, 55, right, 55)
    pdf.setFillColor(gray)
    pdf.setFont(_FONT_NAME, 8)
    pdf.drawString(left, 40, "Thank you for your business.")
    pdf.drawRightString(right, 40, "Page 1 of 1")
    pdf.showPage()
    pdf.save()
    return output.getvalue()


def extract_document(pdf_bytes: bytes, document_id: str, invoice_id: str) -> DocumentText:
    """Compatibility entry point for the canonical pipeline PDF reader."""
    from app.pipeline.document_text import extract_document as read_document

    return read_document(pdf_bytes, document_id, invoice_id)
