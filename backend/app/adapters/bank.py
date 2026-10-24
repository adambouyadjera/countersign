"""Nessie transport and an explicitly offline, in-memory banking ledger.

No payment policy lives here. Live balances and statuses always come from Nessie;
local transfer records retain the precise session timestamp absent from its dates.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Literal
from uuid import uuid4

import httpx
from pydantic import BaseModel, ConfigDict, SecretStr, StrictInt

from app.contract_models import AccountBalance, Balances, Payment, ServiceHealth, SessionTransfer
from app.adapters.payment_models import BankPayment


class BankError(RuntimeError):
    """Safe service error: never includes response bodies or credential URLs."""


class BankBalance(BaseModel):
    model_config = ConfigDict(extra="forbid")
    account_id: str
    balance_cents: StrictInt
    mode: Literal["live", "offline"]


class BankTransfer(BaseModel):
    model_config = ConfigDict(extra="forbid")
    transfer_id: str
    from_account_id: str
    to_account_id: str
    amount_cents: StrictInt
    status: str
    description: str
    ts: datetime
    mode: Literal["live", "offline"]


def _cents(value: Any) -> int:
    try:
        amount = Decimal(str(value)) * 100
        if not amount.is_finite() or amount != amount.to_integral_value():
            raise ValueError
        return int(amount)
    except (InvalidOperation, ValueError, TypeError, OverflowError):
        raise BankError("Nessie returned an invalid dollar amount.") from None


def _ids(from_id, to_id, from_account_id, to_account_id):
    if from_id is not None and from_account_id is not None and from_id != from_account_id:
        raise ValueError("Conflicting source account arguments")
    if to_id is not None and to_account_id is not None and to_id != to_account_id:
        raise ValueError("Conflicting destination account arguments")
    source, destination = from_id or from_account_id, to_id or to_account_id
    if not source or not destination or source == destination:
        raise ValueError("Two different account ids are required")
    return source, destination


class _BankOperations:
    def __init__(self, store=None):
        self._store = store

    def _directory(self):
        if self._store is None:
            return []
        return [row for row in self._store.list_records("bank_directory") if row["mode"] == self.mode]

    def _payment(self, record: BankTransfer, *, detail: dict | None = None) -> Payment:
        destination = next((a for a in self._directory() if a["account_id"] == record.to_account_id), None)
        # Directory membership resolves bank routing only. The vendor master is
        # the independent, trusted authority for accounts on file.
        vendors = self._store.list_records("vendors") if self._store else []
        on_file = bool(destination and any(
            vendor.get("account_on_file_display", vendor.get("remit_account_number")) == destination["display_number"]
            for vendor in vendors
        ))
        fields = dict(
            transfer_id=record.transfer_id, transfer_status=record.status,
            from_account_id=record.from_account_id, to_account_id=record.to_account_id,
            to_display_number=destination["display_number"] if destination else "",
            payee_label=destination["label"] if destination else record.to_account_id,
            payee_on_file=on_file,
            amount_cents=record.amount_cents, bank_mode=self.mode,
        )
        return BankPayment(**fields, detail=detail) if detail is not None else Payment(**fields)

    def _save_transfer(self, record: BankTransfer):
        if self._store is not None:
            previous = self._store.get_record("transfers", record.transfer_id) or {}
            self._store.save_record("transfers", record.transfer_id, {**previous, **record.model_dump(mode="json")})

    async def get_balances(self, session_started_at: datetime) -> Balances:
        directory = self._directory()
        if not directory:
            raise BankError("The bank world has not been seeded for this mode.")
        # Sync statuses for locally submitted transfers without assigning precise
        # session times to old remote records that only have a calendar date.
        for account in directory:
            await self.list_transfers(account["account_id"])
        records = self._store.list_records("transfers") if self._store else []
        labels = {a["account_id"]: a["label"] for a in directory}
        accounts = []
        for account in directory:
            balance = await self.get_balance(account["account_id"])
            received = []
            for raw in records:
                if raw.get("mode") != self.mode or raw.get("to_account_id") != account["account_id"]:
                    continue
                record = BankTransfer.model_validate({k: raw[k] for k in BankTransfer.model_fields})
                if record.ts < session_started_at or record.status.lower() not in {"completed", "executed", "success", "succeeded"}:
                    continue
                received.append(SessionTransfer(
                    transfer_id=record.transfer_id, amount_cents=record.amount_cents,
                    from_label=labels.get(record.from_account_id, record.from_account_id), ts=record.ts,
                ))
            received.sort(key=lambda item: (item.ts, item.transfer_id))
            accounts.append(AccountBalance(
                role=account["role"], label=account["label"], account_id=account["account_id"],
                display_number=account["display_number"], balance_cents=balance.balance_cents,
                received_this_session_cents=sum(t.amount_cents for t in received),
                transfers_this_session=received,
            ))
        return Balances(session_started_at=session_started_at, bank_mode=self.mode, accounts=accounts)


class LiveBankAdapter(_BankOperations):
    mode: Literal["live"] = "live"

    def __init__(self, *, key: SecretStr, base_url: str | None = None, store=None,
                 transport: httpx.AsyncBaseTransport | None = None,
                 url: str = "", key_name="NESSIE_API_KEY", auth="nessie",
                 payment_method: Literal["native_transfer", "paired_withdrawal_deposit"] = "native_transfer"):
        super().__init__(store)
        self._key = key
        self._base_url = (base_url if base_url is not None else url.removesuffix("/customers")).rstrip("/")
        self._transport = transport
        self.payment_method = payment_method

    async def _request(self, method, path, *, payload=None, empty_collection_account_id=None):
        if not self._key.get_secret_value():
            raise BankError("NESSIE_API_KEY is not configured.")
        if not self._base_url:
            raise BankError("Nessie base URL is not configured.")
        try:
            async with asyncio.timeout(15):
                async with httpx.AsyncClient(transport=self._transport, timeout=15, follow_redirects=False) as client:
                    response = await client.request(method, self._base_url + path,
                        params={"key": self._key.get_secret_value()}, json=payload)
        except (httpx.TimeoutException, TimeoutError):
            raise BankError("request timed out after 15 s (nessie)") from None
        except Exception:
            raise BankError("Nessie request failed.") from None
        if not response.is_success:
            if (response.status_code == 404 and method == "GET"
                    and empty_collection_account_id is not None
                    and path == f"/accounts/{empty_collection_account_id}/transfers"):
                try:
                    empty_message = response.json()
                except ValueError:
                    empty_message = None
                if empty_message == "No transfers found for this account":
                    # Observed Nessie empty-collection response. Check account
                    # existence so an unknown account cannot masquerade as empty.
                    await self.get_balance(empty_collection_account_id)
                    return []
            if response.status_code == 400 and method == "POST" and path.endswith("/transfers"):
                try:
                    validation = response.json()
                except ValueError:
                    validation = None
                if isinstance(validation, str) and all(token in validation for token in (
                    "TransferCreate", "payee_id", "extra fields not permitted",
                )):
                    # Current deployments have been observed to expose an
                    # account-local transaction schema with no routing field.
                    # Never drop the destination or label that as a payment.
                    raise BankError(
                        "Nessie rejected the documented transfer routing field; "
                        "this deployment's TransferCreate schema is incompatible. "
                        "No transfer was recorded or retried."
                    )
            raise BankError(f"Nessie returned HTTP {response.status_code}.")
        try:
            return response.json()
        except ValueError:
            raise BankError("Nessie returned invalid JSON.") from None

    async def health_check(self):
        if not self._key.get_secret_value():
            return ServiceHealth(status="missing_key", mode=self.mode, detail="NESSIE_API_KEY is not configured.")
        try:
            await self.list_customers()
        except BankError as error:
            return ServiceHealth(status="error", mode=self.mode, detail=str(error))
        return ServiceHealth(status="ok", mode=self.mode,
                             detail=f"Read-only bank health probe succeeded; payment method: {self.payment_method}.")

    @staticmethod
    def _objects(body):
        if not isinstance(body, list) or not all(isinstance(row, dict) for row in body):
            raise BankError("Nessie returned an unexpected list response.")
        return body

    @staticmethod
    def _created(body):
        result = body.get("objectCreated", body) if isinstance(body, dict) else None
        if not isinstance(result, dict) or not isinstance(result.get("_id"), str) or not result["_id"]:
            raise BankError("Nessie did not return the created resource id; do not retry blindly.")
        return result

    async def list_customers(self):
        return self._objects(await self._request("GET", "/customers"))

    async def create_customer(self, payload):
        return self._created(await self._request("POST", "/customers", payload=payload))

    async def list_accounts(self, customer_id):
        return self._objects(await self._request("GET", f"/customers/{customer_id}/accounts"))

    async def create_account(self, customer_id, payload):
        return self._created(await self._request("POST", f"/customers/{customer_id}/accounts", payload=payload))

    async def get_balance(self, account_id):
        body = await self._request("GET", f"/accounts/{account_id}")
        if not isinstance(body, dict) or body.get("_id") != account_id or "balance" not in body:
            raise BankError("Nessie returned an unexpected account response.")
        return BankBalance(account_id=account_id, balance_cents=_cents(body["balance"]), mode=self.mode)

    def _normalize(self, raw, *, fallback=None):
        fallback = fallback or {}
        previous = self._store.get_record("transfers", raw.get("_id", "")) if self._store else None
        previous = previous or {}
        try:
            timestamp = previous.get("ts") or raw.get("transaction_date")
            if timestamp is None:
                timestamp = fallback.get("ts")
            if isinstance(timestamp, str):
                timestamp = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
            if timestamp.tzinfo is None:
                timestamp = timestamp.replace(tzinfo=timezone.utc)
            timestamp = timestamp.astimezone(timezone.utc)
            values = {
                "transfer_id": raw["_id"],
                "from_account_id": raw.get("payer_id") or fallback.get("from_account_id") or previous["from_account_id"],
                "to_account_id": raw.get("payee_id") or fallback.get("to_account_id") or previous["to_account_id"],
                "amount_cents": _cents(raw["amount"]) if "amount" in raw else fallback.get("amount_cents", previous.get("amount_cents")),
                "status": raw["status"], "description": raw.get("description", fallback.get("description", "")),
                "ts": timestamp, "mode": self.mode,
            }
            return BankTransfer.model_validate(values)
        except (KeyError, TypeError, AttributeError, ValueError):
            raise BankError("Nessie returned an incomplete transfer record.") from None

    async def list_transfers(self, account_id):
        if self.payment_method == "paired_withdrawal_deposit":
            from app.adapters.paired_payments import refresh_paired_payments

            if self._store is None:
                raise BankError("Paired payments require persistent attempt storage.")
            records = []
            for raw in await refresh_paired_payments(self, account_id):
                record = BankTransfer.model_validate({field: raw[field] for field in BankTransfer.model_fields})
                self._save_transfer(record)
                self._store.save_record("transfers", record.transfer_id, {
                    **self._store.get_record("transfers", record.transfer_id), "detail": raw["detail"],
                })
                records.append(record)
            return records
        rows = self._objects(await self._request("GET", f"/accounts/{account_id}/transfers",
                                                empty_collection_account_id=account_id))
        records = [self._normalize(row) for row in rows]
        for record in records:
            self._save_transfer(record)
        return records

    async def transfer(self, from_id=None, to_id=None, amount_cents=None, description="", *,
                       from_account_id=None, to_account_id=None):
        source, destination = _ids(from_id, to_id, from_account_id, to_account_id)
        if type(amount_cents) is not int or amount_cents <= 0:
            raise ValueError("amount_cents must be a positive integer")
        if self.payment_method == "paired_withdrawal_deposit":
            from app.adapters.paired_payments import execute_paired_payment

            raw = await execute_paired_payment(self, source, destination, amount_cents, description)
            record = BankTransfer.model_validate({field: raw[field] for field in BankTransfer.model_fields})
            self._save_transfer(record)
            self._store.save_record("transfers", record.transfer_id, {
                **self._store.get_record("transfers", record.transfer_id), "detail": raw["detail"],
            })
            return self._payment(record, detail=raw["detail"])
        timestamp = datetime.now(timezone.utc)
        body = self._created(await self._request("POST", f"/accounts/{source}/transfers", payload={
            "medium": "balance", "payee_id": destination, "amount": float(Decimal(amount_cents) / 100),
            "transaction_date": timestamp.date().isoformat(), "description": description,
        }))
        if "status" not in body:
            body = await self._request("GET", f"/transfers/{body['_id']}")
        record = self._normalize(body, fallback={"from_account_id": source, "to_account_id": destination,
            "amount_cents": amount_cents, "description": description, "ts": timestamp})
        # Nessie calendar dates cannot distinguish transfers before/after reset.
        record.ts = timestamp
        self._save_transfer(record)
        if (record.from_account_id, record.to_account_id, record.amount_cents) != (source, destination, amount_cents):
            raise BankError("Nessie returned transfer details that differ from the request; inspect the recorded transfer.")
        return self._payment(record)


class OfflineBankAdapter(_BankOperations):
    mode: Literal["offline"] = "offline"

    def __init__(self, *, store=None, detail="NESSIE_MODE=offline; in-memory bank ledger."):
        super().__init__(store)
        self._detail = detail
        self._customers = {}
        self._accounts = {}
        self._transfers = {}
        self._lock = asyncio.Lock()

    async def health_check(self):
        return ServiceHealth(status="offline", mode=self.mode, detail=self._detail)

    def _load_directory(self):
        stored = self._store.list_records("transfers") if self._store else []
        for raw in stored:
            if raw.get("mode") == "offline":
                record = BankTransfer.model_validate({k: raw[k] for k in BankTransfer.model_fields})
                self._transfers.setdefault(record.transfer_id, record)
        for row in self._directory():
            if row["account_id"] in self._accounts:
                continue
            # Rebuild only when loading a previously seeded account into a new
            # process. Existing in-memory balances already include these moves.
            balance_cents = row.get("initial_balance_cents", 0)
            for record in self._transfers.values():
                if record.status != "completed":
                    continue
                if record.from_account_id == row["account_id"]:
                    balance_cents -= record.amount_cents
                if record.to_account_id == row["account_id"]:
                    balance_cents += record.amount_cents
            self._accounts[row["account_id"]] = {
                "_id": row["account_id"], "customer_id": row["customer_id"],
                "balance": balance_cents / 100, "mode": self.mode,
            }

    async def list_customers(self):
        return [dict(row) for row in self._customers.values()]

    async def create_customer(self, payload):
        customer = {**payload, "_id": "offline-customer-" + uuid4().hex, "mode": self.mode}
        self._customers[customer["_id"]] = customer
        return dict(customer)

    async def list_accounts(self, customer_id):
        self._load_directory()
        return [dict(row) for row in self._accounts.values() if row["customer_id"] == customer_id]

    async def create_account(self, customer_id, payload):
        account = {**payload, "_id": "offline-account-" + uuid4().hex,
                   "customer_id": customer_id, "mode": self.mode}
        self._accounts[account["_id"]] = account
        return dict(account)

    async def get_balance(self, account_id):
        self._load_directory()
        if account_id not in self._accounts:
            raise BankError("Unknown offline bank account.")
        return BankBalance(account_id=account_id, balance_cents=_cents(self._accounts[account_id]["balance"]), mode=self.mode)

    async def list_transfers(self, account_id):
        await self.get_balance(account_id)
        return [record.model_copy(deep=True) for record in self._transfers.values()
                if account_id in {record.from_account_id, record.to_account_id}]

    async def transfer(self, from_id=None, to_id=None, amount_cents=None, description="", *,
                       from_account_id=None, to_account_id=None):
        source, destination = _ids(from_id, to_id, from_account_id, to_account_id)
        if type(amount_cents) is not int or amount_cents <= 0:
            raise ValueError("amount_cents must be a positive integer")
        async with self._lock:
            before = await self.get_balance(source)
            target = await self.get_balance(destination)
            if before.balance_cents < amount_cents:
                raise BankError("Insufficient offline funds.")
            self._accounts[source]["balance"] = (before.balance_cents - amount_cents) / 100
            self._accounts[destination]["balance"] = (target.balance_cents + amount_cents) / 100
            record = BankTransfer(transfer_id="offline-transfer-" + uuid4().hex,
                from_account_id=source, to_account_id=destination, amount_cents=amount_cents,
                status="completed", description=description, ts=datetime.now(timezone.utc), mode=self.mode)
            self._transfers[record.transfer_id] = record
            self._save_transfer(record)
            return self._payment(record)
