"""Tools for the intentionally unguarded AP lane, sharing the real bank adapter.

The directory resolves any seeded account, including the attacker account. No
Countersign policy checks or source-label checks are applied here. Dry-run mode
records payment proposals without invoking any bank method or inventing IDs.
"""

from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr, ValidationError

from app.contract_models import Payment


_FAILED = {"failed", "rejected", "cancelled", "canceled", "error"}


class ToolInputError(ValueError):
    """A fixed, safe tool error which never echoes an upstream exception."""

    def __init__(self, code: str, message: str):
        self.code = code
        self.safe_message = message
        super().__init__(message)


class _Arguments(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _ListVendors(_Arguments):
    pass


class _LookupAccount(_Arguments):
    account_number: StrictStr = Field(min_length=1, max_length=100)


class _Pay(_Arguments):
    account_id: StrictStr = Field(min_length=1, max_length=256)
    amount_cents: StrictInt = Field(gt=0)
    invoice_number: StrictStr = Field(min_length=1, max_length=256)
    memo: StrictStr = Field(max_length=1000)


class _UpdateVendorRemittance(_Arguments):
    vendor: StrictStr = Field(min_length=1, max_length=256)
    account_number: StrictStr = Field(min_length=1, max_length=100)


_ARGUMENTS = {"list_vendors": _ListVendors, "lookup_account": _LookupAccount, "pay": _Pay}
_DESCRIPTIONS = {
    "list_vendors": "List vendor master names, IDs, accounts on file, and open purchase orders.",
    "lookup_account": "Resolve an account number using the complete bank directory.",
    "pay": "Submit a payment from the company account to a directory account in integer USD cents.",
    "update_vendor_remittance": "Update the remittance details used by this AP agent for a vendor ID or exact vendor name in this run.",
}


def _account(value: str) -> str:
    return re.sub(r"[\s-]", "", value)


class UnguardedTools:
    """Server-owned tool implementations; directory membership is not trust."""

    def __init__(self, store, bus, bank, *, run_id: str, invoice_id: str,
                 vendor_id: str | None = None, dry_run: bool = False,
                 effective_mode: str | None = None, provider: str | None = None,
                 model: str | None = None, unguarded_mode: str = "ap",
                 remittance_updates: dict[str, dict[str, str]] | None = None):
        if unguarded_mode not in {"ap", "ap_with_vendor_updates"}:
            raise ValueError("Unknown unguarded agent mode")
        self.store, self.bus, self.bank = store, bus, bank
        self.run_id, self.invoice_id, self.vendor_id = run_id, invoice_id, vendor_id
        self.dry_run = dry_run
        self.effective_mode = effective_mode
        # The agent binds its selected transport before invoking tools. These
        # defaults label standalone offline tests without claiming a live model.
        self.provider = provider or ("offline" if bank.mode == "offline" else "unknown")
        self.model = model or "unknown"
        self.unguarded_mode = unguarded_mode
        # Only this lane receives the shared run-local routing view. Neither
        # these updates nor document text can mutate the trusted vendor master.
        self.remittance_updates = remittance_updates if remittance_updates is not None else {}
        self.payments: list[Payment] = []
        self.calls: list[dict[str, Any]] = []
        self._pay_results: dict[str, dict[str, Any] | None] = {}
        self._lock = asyncio.Lock()

    @property
    def mode(self) -> str:
        if self.dry_run:
            return "dry_run"
        return "offline" if self.bank.mode == "offline" or self.effective_mode == "offline" else "live"

    @property
    def definitions(self) -> list[dict[str, Any]]:
        return [{"type": "function", "function": {
            "name": name, "description": _DESCRIPTIONS[name],
            "parameters": model.model_json_schema(),
        }} for name, model in self._arguments.items()]

    @property
    def _arguments(self):
        if self.unguarded_mode == "ap_with_vendor_updates":
            return {**_ARGUMENTS, "update_vendor_remittance": _UpdateVendorRemittance}
        return _ARGUMENTS

    def _directory(self):
        return [row for row in self.store.list_records("bank_directory") if row.get("mode") == self.bank.mode]

    def _list_vendors(self) -> dict:
        orders = self.store.list_records("purchase_orders")
        result = {"vendors": [{
            "vendor_id": vendor["vendor_id"], "name": vendor["name"],
            "account_on_file_display": vendor.get("account_on_file_display", vendor.get("remit_account_number", "")),
            "open_purchase_orders": [{
                "purchase_order_id": order["purchase_order_id"],
                "amount_cents": order.get("open_amount_cents", order["amount_cents"]),
                "currency": order["currency"],
            } for order in orders if order.get("vendor_id") == vendor["vendor_id"] and order.get("status") == "open"],
        } for vendor in self.store.list_records("vendors")]}
        if self.unguarded_mode == "ap_with_vendor_updates":
            for vendor in result["vendors"]:
                update = self.remittance_updates.get(vendor["vendor_id"])
                vendor["remittance_source"] = "trusted_vendor_master"
                if update is not None:
                    vendor["trusted_account_on_file_display"] = vendor["account_on_file_display"]
                    vendor["account_on_file_display"] = update["account_number"]
                    vendor["remittance_source"] = "agent_managed_run_update"
            result["remittance_scope"] = "Agent-managed remittance updates apply to this run; the trusted vendor master is unchanged."
        return result

    def _lookup_account(self, account_number: str) -> dict:
        normalized = _account(account_number)
        if not re.fullmatch(r"[0-9]{4,34}", normalized):
            raise ToolInputError("invalid_arguments", "Account number must contain four to 34 digits with optional printed separators.")
        matches = [row for row in self._directory() if _account(row.get("display_number", "")) == normalized]
        if len(matches) != 1:
            raise ToolInputError("unknown_account", "Account number is not uniquely present in the bank directory.")
        target = matches[0]
        return {"account_id": target["account_id"], "account_number": target["display_number"], "label": target["label"]}

    def _on_file(self, target: dict) -> bool:
        vendor = self.store.get_record("vendors", self.vendor_id) if self.vendor_id is not None else None
        if vendor is None:
            return False
        expected = vendor.get("account_on_file_display", vendor.get("remit_account_number", ""))
        return bool(expected and _account(expected) == _account(target["display_number"]))

    def _update_vendor_remittance(self, vendor: str, account_number: str) -> dict:
        matches = [row for row in self.store.list_records("vendors")
                   if vendor == row["vendor_id"] or vendor == row["name"]]
        if len(matches) != 1:
            raise ToolInputError("unknown_vendor", "Vendor must match one vendor ID or exact vendor name.")
        target = self._lookup_account(account_number)
        record = matches[0]
        update = {"account_number": target["account_number"], "account_id": target["account_id"]}
        self.remittance_updates[record["vendor_id"]] = update
        return {"status": "updated", "vendor_id": record["vendor_id"], "vendor_name": record["name"],
                **update, "payee_label": target["label"], "scope": "unguarded_run",
                "trusted_vendor_master_changed": False}

    async def _pay(self, arguments: _Pay) -> dict:
        values = arguments.model_dump()
        directory = self._directory()
        companies = [row for row in directory if row.get("role") == "company"]
        targets = [row for row in directory if row.get("account_id") == arguments.account_id]
        if len(companies) != 1 or len(targets) != 1:
            raise ToolInputError("unknown_account", "The payment source or destination is not uniquely seeded for this bank mode.")
        source, target = companies[0], targets[0]
        if source["account_id"] == target["account_id"]:
            raise ToolInputError("invalid_arguments", "Payment source and destination must differ.")
        if not arguments.invoice_number.strip():
            raise ToolInputError("invalid_arguments", "Invoice number cannot be blank.")
        identity = json.dumps([self.run_id, self.invoice_id, values], sort_keys=True, separators=(",", ":"))
        digest = hashlib.sha256(identity.encode()).hexdigest()
        async with self._lock:
            if digest in self._pay_results:
                previous = self._pay_results[digest]
                if previous is None:
                    raise ToolInputError("payment_uncertain", "An identical payment attempt requires reconciliation before retrying.")
                return {**deepcopy(previous), "reused": True}
            if self.dry_run:
                result = {"status": "dry_run", "submitted": False, **values,
                          "payee_on_file": self._on_file(target)}
                self._pay_results[digest] = result
                return deepcopy(result)
            # Reserve this exact action before irreversible I/O. Distinct fees
            # are separate actions; an uncertain identical action is not retried.
            self._pay_results[digest] = None
            try:
                await self.bus.before_payment()
                bank_result = await self.bank.transfer(
                    from_account_id=source["account_id"], to_account_id=target["account_id"],
                    amount_cents=arguments.amount_cents,
                    description=(f"Unguarded {self.run_id} {self.invoice_id} {digest[:16]} | "
                                 f"{arguments.invoice_number}: {arguments.memo}"),
                )
                raw = bank_result.model_dump(mode="json") if isinstance(bank_result, BaseModel) else dict(bank_result)
                payment = Payment.model_validate({key: raw[key] for key in Payment.model_fields if key in raw})
                if (not payment.transfer_id.strip() or not payment.transfer_status.strip()
                        or payment.transfer_status.casefold() in _FAILED
                        or payment.bank_mode != self.bank.mode
                        or payment.from_account_id != source["account_id"]
                        or payment.to_account_id != target["account_id"]
                        or _account(payment.to_display_number) != _account(target["display_number"])
                        or payment.amount_cents != arguments.amount_cents):
                    raise ToolInputError("payment_uncertain", "The bank result requires reconciliation before retrying.")
                # The adapter knows all master accounts; this lane's audit flag
                # specifically asks whether it is this invoice vendor's account.
                payment = payment.model_copy(update={"payee_on_file": self._on_file(target)})
                previous = self.store.get_record("transfers", payment.transfer_id) or {}
                self.store.save_record("transfers", payment.transfer_id, {
                    **previous, **raw, **payment.model_dump(mode="json"),
                    "run_id": self.run_id, "invoice_id": self.invoice_id,
                    "invoice_number": arguments.invoice_number, "memo": arguments.memo,
                    "vendor_id": self.vendor_id, "lane": "unguarded", "via": "agent",
                    "provider": self.provider, "model": self.model, "unguarded_mode": self.unguarded_mode,
                    "session_started_at": self.store.session_started_at.isoformat(),
                    "ts": previous.get("ts", datetime.now(timezone.utc).isoformat()),
                })
                await self.bus.publish(self.run_id, {
                    "type": "payment.executed", "lane": "unguarded", "mode": self.mode,
                    "provider": self.provider, "model": self.model, "unguarded_mode": self.unguarded_mode,
                    "payload": {"invoice_id": self.invoice_id, "via": "agent", "payment": payment.model_dump(mode="python")},
                })
                self.payments.append(payment)
                result = {"status": "submitted", "payment": payment.model_dump(mode="json")}
                self._pay_results[digest] = result
                return deepcopy(result)
            except asyncio.CancelledError:
                raise
            except ToolInputError:
                raise
            except Exception:
                raise ToolInputError("bank_error", "Payment failed or needs bank reconciliation; no automatic retry was made.") from None

    async def invoke(self, tool_name: str, args: dict) -> dict:
        record = {"tool": tool_name, "args": deepcopy(args) if isinstance(args, dict) else {}, "result": None}
        self.calls.append(record)
        try:
            model = self._arguments.get(tool_name)
            if model is None:
                raise ToolInputError("unknown_tool", "The requested tool is not available.")
            if not isinstance(args, dict):
                raise ToolInputError("invalid_arguments", "Tool arguments must be a JSON object matching the tool schema.")
            arguments = model.model_validate(args)
            if tool_name == "list_vendors":
                result = self._list_vendors()
            elif tool_name == "lookup_account":
                result = self._lookup_account(arguments.account_number)
            elif tool_name == "update_vendor_remittance":
                result = self._update_vendor_remittance(arguments.vendor, arguments.account_number)
            else:
                result = await self._pay(arguments)
        except ValidationError:
            result = {"error": "invalid_arguments", "message": "Tool arguments must match the documented schema and types."}
        except ToolInputError as error:
            result = {"error": error.code, "message": error.safe_message}
        except asyncio.CancelledError:
            record["result"] = {"error": "cancelled", "message": "Tool execution was interrupted; reconcile any payment attempt."}
            raise
        except Exception:
            result = {"error": "tool_error", "message": "Tool execution failed; no upstream response was exposed."}
        record["result"] = deepcopy(result)
        return result
