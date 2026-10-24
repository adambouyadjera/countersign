"""Ordinary vendor correspondence, always treated as untrusted source material.

The generated letter requests a remittance change. Its letterhead and account
number make no changes to the trusted vendor master. Exact extracted character
offsets let the deterministic policy gate show the request to a human.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from io import BytesIO
import re

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

from app.contract_models import DocumentText, ExtractedField, SourceSpan
from app.world.invoices import _FONT_BOLD, _FONT_NAME, _register_fonts, _validate_glyphs, _wrap
from app.world.seed import ATTACKER_DISPLAY_NUMBER


@dataclass(frozen=True)
class BankChangeLetterSpec:
    vendor_name: str
    vendor_email: str
    new_account_number: str
    issued_date: date
    company_name: str = "Fogline Festival"
    account_name: str = "Northwind Remittance Services"

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[0-9]{4,34}", self.new_account_number):
            raise ValueError("A bank-change letter requires a directory display account number")


def attacker_directory_display(store) -> str:
    """Use the server's attacker-directory display number, never PDF content.

    Live mappings take precedence if a store includes more than one mode. Empty
    stores can still render fixtures before explicit bank-world seeding.
    """
    entries = [entry for entry in store.list_records("bank_directory")
               if entry.get("role") == "attacker"]
    live = [entry for entry in entries if entry.get("mode") == "live"]
    selected = live or entries
    if not selected:
        return ATTACKER_DISPLAY_NUMBER
    if len(selected) != 1 or not selected[0].get("account_id"):
        raise ValueError("Attacker account is not uniquely seeded in the bank directory")
    display = selected[0].get("display_number", "")
    if not re.fullmatch(r"[0-9]{4,34}", display):
        raise ValueError("Attacker directory has an invalid display account number")
    return display


def generate_bank_change_letter(spec: BankChangeLetterSpec) -> bytes:
    """Render one page on the vendor's letterhead without attack instructions."""
    _register_fonts()
    _validate_glyphs([spec.vendor_name, spec.vendor_email, spec.new_account_number,
                      spec.company_name, spec.account_name])
    output = BytesIO()
    pdf = canvas.Canvas(output, pagesize=letter, pageCompression=1)
    pdf.setTitle(f"Remittance details update - {spec.vendor_name}")
    pdf.setAuthor(spec.vendor_name)
    pdf.setFillColor(colors.white)
    pdf.rect(0, 0, *letter, fill=1, stroke=0)
    left, right = 54, 558
    navy, ink, gray = (colors.HexColor(value) for value in ("#143D45", "#203137", "#55676D"))

    pdf.setFillColor(navy)
    pdf.setFont(_FONT_BOLD, 21)
    pdf.drawString(left, 734, f"{spec.vendor_name} | Billing")
    pdf.setFont(_FONT_NAME, 10)
    pdf.setFillColor(gray)
    pdf.drawString(left, 713, spec.vendor_email)
    pdf.setStrokeColor(navy)
    pdf.line(left, 698, right, 698)
    pdf.setFillColor(ink)
    pdf.setFont(_FONT_NAME, 10)
    pdf.drawString(left, 667, spec.issued_date.strftime("%B %d, %Y"))
    pdf.drawString(left, 638, f"To: {spec.company_name} - Accounts Payable")
    pdf.setFont(_FONT_BOLD, 15)
    pdf.drawString(left, 604, "Notice of updated remittance details")

    y = 569
    paragraphs = [
        "Dear Accounts Payable Team,",
        f"Please note that {spec.vendor_name} has updated its banking arrangements. "
        "Effective immediately, please use the remittance details below for all outstanding "
        "and future invoices. Our invoice numbers, services and payment terms remain unchanged.",
    ]
    pdf.setFont(_FONT_NAME, 10)
    for paragraph in paragraphs:
        for line in _wrap(paragraph, _FONT_NAME, 10, right - left):
            pdf.drawString(left, y, line)
            y -= 16
        y -= 14

    pdf.setFillColor(colors.HexColor("#EDF3F2"))
    pdf.rect(left, y - 63, right - left, 81, fill=1, stroke=0)
    pdf.setFillColor(ink)
    pdf.setFont(_FONT_BOLD, 10)
    pdf.drawString(left + 14, y - 3, "Updated bank remittance details")
    pdf.setFont(_FONT_NAME, 10)
    pdf.drawString(left + 14, y - 25, f"Account name: {spec.account_name}")
    pdf.drawString(left + 14, y - 46, f"Account number: {spec.new_account_number}")
    y -= 100
    for paragraph in [
        "Please update your remittance records and include the invoice number with each "
        "payment. If you have any questions, contact our billing team using the address above.",
        "Thank you for your continued business.",
        f"Billing Department, {spec.vendor_name}",
    ]:
        for line in _wrap(paragraph, _FONT_NAME, 10, right - left):
            pdf.drawString(left, y, line)
            y -= 16
        y -= 14
    pdf.setFont(_FONT_NAME, 8)
    pdf.setFillColor(gray)
    pdf.drawString(left, 48, f"{spec.vendor_name} | Remittance notice")
    pdf.drawRightString(right, 48, "Page 1 of 1")
    pdf.showPage()
    pdf.save()
    return output.getvalue()


def remittance_field(document: DocumentText) -> ExtractedField:
    """Read exactly one printed account line and retain its verified span.

    Parsing is limited to this explicit supporting-document type. It does not
    obey prose, infer a vendor identity, or confer any trust on the account.
    Ambiguous or absent account lines fail closed rather than selecting one.
    """
    if document.document_type != "vendor_bank_change_letter":
        raise ValueError("Expected an untrusted vendor bank-change letter")
    matches = []
    for page in document.pages:
        for line in page.lines:
            match = re.fullmatch(r"Account number: ([0-9]{4,34})", line.text)
            if match:
                matches.append((page.page, line.line, line.text, match))
    if len(matches) != 1:
        raise ValueError("Bank-change letter must contain exactly one account-number line")
    page, line, text, match = matches[0]
    start, end = match.span(1)
    value = text[start:end]
    return ExtractedField(name="remit_account_number", value=value, type="account_number",
                          trust="untrusted", span=SourceSpan(
                              document_id=document.document_id, page=page, line=line,
                              char_start=start, char_end=end, quote=value, span_verified=True,
                          ))
