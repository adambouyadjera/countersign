"""Deterministic extraction from the generated invoice layout, never instructions."""

from decimal import Decimal
import re

from app.contract_models import DocumentText
from app.pipeline.extraction_models import (
    RawExtraction, RawLineItem, RawMoneyField, RawNumberField, RawSpan, RawStringField,
)


_MONEY = r"\$[\d,]+\.\d{2}"
_ITEM = re.compile(rf"^(?P<description>.+?)\s+(?P<quantity>\d+)\s+(?P<unit>{_MONEY})\s+(?P<amount>{_MONEY})$")
_INSTRUCTION = re.compile(
    r"\b(?:automated|processor|AP systems?|AI|assistant|disregard|supersedes?|"
    r"please|changed banks|new remittance|payment routing|pay separately|"
    r"pay the|route this|remit this|do not request)\b", re.IGNORECASE,
)


def _cents(value: str) -> int:
    return int(Decimal(value.replace("$", "").replace(",", "")) * 100)


def parse_offline(document: DocumentText) -> RawExtraction:
    """Parse source labels and geometry-independent extracted text only.

    No trusted world, invoice summary, bank directory, or scoring table enters
    this parser. Printed remit details stay data even when a separate note asks
    an automated processor to disregard them.
    """
    lines = [(page.page, line.line, line.text) for page in document.pages for line in page.lines]

    def string(value=None, source=None, quote=None):
        page, line, text = source or (1, 1, "")
        return RawStringField(value=value, quote=text if quote is None else quote, page=page, line=line)

    def labeled(label, pattern=None):
        for source in lines:
            if source[2].startswith(label):
                value = source[2][len(label):].strip()
                if pattern is None or re.fullmatch(pattern, value):
                    return string(value, source, value)
        return string()

    vendor = string()
    email = string()
    for index, source in enumerate(lines):
        if re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", source[2]):
            email = string(source[2], source)
            if index > 0:
                vendor = string(lines[index - 1][2], lines[index - 1])
            break
    invoice_number = string()
    for index, source in enumerate(lines):
        if source[2] == "INVOICE" and index + 1 < len(lines):
            invoice_number = string(lines[index + 1][2], lines[index + 1])
            break

    amount = RawMoneyField(value=None, quote="", page=1, line=1)
    currency = string()
    for source in lines:
        match = re.fullmatch(rf"TOTAL \((?P<currency>[A-Z]{{3}})\)\s+(?P<amount>{_MONEY})", source[2])
        if match:
            amount = RawMoneyField(value=_cents(match["amount"]), quote=source[2], page=source[0], line=source[1])
            currency = string(match["currency"], source, match["currency"])
            break

    items = []
    in_items = False
    for source in lines:
        text = source[2]
        if text == "DESCRIPTION QTY UNIT PRICE AMOUNT":
            in_items = True
            continue
        if text == "Remit to" or text.startswith("TOTAL ("):
            in_items = False
        if not in_items:
            continue
        match = _ITEM.fullmatch(text)
        if match:
            def money(value):
                return RawMoneyField(value=_cents(value), quote=text, page=source[0], line=source[1])
            items.append(RawLineItem(
                description=string(match["description"], source, match["description"]),
                quantity=RawNumberField(value=int(match["quantity"]), quote=text, page=source[0], line=source[1]),
                unit_price_cents=money(match["unit"]), amount_cents=money(match["amount"]),
            ))
        elif items and text.strip():
            items[-1].description.value += " " + text.strip()

    instructions = []
    context = False
    for source in lines:
        text = source[2]
        if text.startswith("Thank you for your business."):
            context = False
        if _INSTRUCTION.search(text):
            context = True
        if context and text.strip() and not text.startswith("Thank you for your business."):
            instructions.append(RawSpan(quote=text, page=source[0], line=source[1]))

    return RawExtraction(
        invoice_number=invoice_number, vendor_name=vendor, vendor_email=email,
        invoice_date=labeled("Invoice date: ", r"\d{4}-\d{2}-\d{2}"),
        due_date=labeled("Due date: ", r"\d{4}-\d{2}-\d{2}"),
        po_number=labeled("Purchase order: "), amount_due_cents=amount,
        currency=currency, remit_account_number=labeled("Account number: "),
        line_items=items, instruction_like_spans=instructions,
    )
