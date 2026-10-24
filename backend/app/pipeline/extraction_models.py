"""Tool-free extractor output before source verification.

The public contract represents extraction as generic scalar fields. This local
schema gives Gemini a complete invoice shape; nested line items are flattened
into named scalar fields when converting to that public contract.
"""

from pydantic import BaseModel, ConfigDict, Field, StrictFloat, StrictInt, StrictStr


class RawSpan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    quote: StrictStr
    page: StrictInt = Field(ge=1)
    line: StrictInt = Field(ge=1)


class RawStringField(RawSpan):
    value: StrictStr | None


class RawMoneyField(RawSpan):
    value: StrictInt | None


class RawNumberField(RawSpan):
    value: StrictInt | StrictFloat | None


class RawLineItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    description: RawStringField
    quantity: RawNumberField
    unit_price_cents: RawMoneyField
    amount_cents: RawMoneyField


class RawExtraction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    invoice_number: RawStringField
    vendor_name: RawStringField
    vendor_email: RawStringField
    invoice_date: RawStringField
    due_date: RawStringField
    po_number: RawStringField
    amount_due_cents: RawMoneyField
    currency: RawStringField
    remit_account_number: RawStringField
    line_items: list[RawLineItem]
    instruction_like_spans: list[RawSpan]
