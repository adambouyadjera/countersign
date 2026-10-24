"""Bank-result detail pending the corresponding public Payment contract addition.

The v1 wire Payment remains unchanged. Adapter callers can inspect and persist
the complete result, including both real Nessie operation identifiers.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, StrictStr

from app.contract_models import Payment


class PairedPaymentDetail(BaseModel):
    model_config = ConfigDict(extra="forbid")
    method: Literal["paired_withdrawal_deposit"]
    atomic: Literal[False]
    withdrawal_id: StrictStr
    deposit_id: StrictStr
    withdrawal_status: StrictStr
    deposit_status: StrictStr


class BankPayment(Payment):
    detail: PairedPaymentDetail
