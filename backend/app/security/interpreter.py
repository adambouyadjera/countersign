"""A small, validated program interpreter with no authority to move money."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from difflib import SequenceMatcher
import re
from typing import Any, get_args

from pydantic import TypeAdapter, ValidationError

from app.contract_models import PlanArg, PlanStep, ReasonCode
from app.security.handles import HandleTable
from app.security.labels import Labeled, Source, TrustedRecord, sources_of


OP_ARGUMENTS = {
    "resolve_vendor": {"name_handle"},
    "match_po": {"invoice", "vendor"},
    "due_within": {"date_handle", "days"},
    "pay": {"invoice", "vendor", "amount", "to_account", "memo"},
    "hold": {"invoice", "reason"},
    "skip": {"invoice", "reason"},
}
_OUTPUT_TYPES = {"resolve_vendor": "vendor", "match_po": "purchase_order", "due_within": "boolean"}
_REASONS = frozenset(get_args(ReasonCode))
_STEPS = TypeAdapter(list[PlanStep])
_STEP_ID = re.compile(r"[A-Za-z][A-Za-z0-9_-]{0,63}\Z")


class PlanValidationError(ValueError):
    """A static validation explanation safe to include in a retry prompt."""


def validate_plan(
    steps: Sequence[PlanStep | dict], table: HandleTable,
    vendors: Sequence[Mapping[str, Any]], purchase_orders: Sequence[Mapping[str, Any]],
) -> list[PlanStep]:
    """Validate the whole program before executing any instruction.

    No exception contains a rejected reference or value. In particular, service
    validation errors must never echo a generated/raw value into a retry prompt.
    """
    try:
        validated = _STEPS.validate_python(steps)
    except ValidationError:
        raise PlanValidationError("Plan must be an array of contract PlanStep objects") from None
    if len(validated) > max(1, len(table.invoices)) * 20:
        raise PlanValidationError("Plan exceeds the maximum number of steps")
    vendor_ids = {vendor["vendor_id"] for vendor in vendors}
    handles = {handle.name: handle for handle in table.handles}
    previous: dict[str, PlanStep] = {}
    terminals: set[str] = set()

    def require_ref(arg: PlanArg) -> str:
        if not arg.ref or "value" in arg.model_fields_set:
            raise PlanValidationError("Reference arguments require ref and must omit value")
        return arg.ref

    def require_handle(arg: PlanArg, invoice: str, kind: str) -> None:
        if arg.kind != "handle":
            raise PlanValidationError("This argument requires a handle")
        ref = require_ref(arg)
        handle = handles.get(ref)
        if handle is None or not ref.startswith(f"${invoice}."):
            raise PlanValidationError("Handle must belong to this invoice")
        if handle.type != kind:
            raise PlanValidationError("Handle has the wrong type for this argument")

    def require_vendor(arg: PlanArg, invoice: str) -> None:
        ref = require_ref(arg)
        if arg.kind == "trusted" and ref in vendor_ids:
            return
        if arg.kind == "step" and ref in previous:
            source = previous[ref]
            if source.invoice_id == invoice and _OUTPUT_TYPES.get(source.op) == "vendor":
                return
        raise PlanValidationError("Vendor must reference a trusted vendor or an earlier vendor result for this invoice")

    def require_literal(arg: PlanArg) -> Any:
        if arg.kind != "literal" or "value" not in arg.model_fields_set or "ref" in arg.model_fields_set:
            raise PlanValidationError("Literal arguments require value and must omit ref")
        return arg.value

    for step in validated:
        if not _STEP_ID.fullmatch(step.id) or step.id in previous:
            raise PlanValidationError("Step ids must be unique safe identifiers")
        alias = step.invoice_id
        if alias not in table.invoices:
            raise PlanValidationError("Step invoice_id must be a server-assigned document label")
        if alias in terminals:
            raise PlanValidationError("An invoice cannot have steps after a terminal action")
        if set(step.args) != OP_ARGUMENTS[step.op]:
            raise PlanValidationError("Operation arguments do not match the documented signature")
        args = step.args
        if "invoice" in args:
            invoice_arg = args["invoice"]
            if invoice_arg.kind != "trusted" or require_ref(invoice_arg) != alias:
                raise PlanValidationError("Invoice argument must reference this server-assigned document label")
        if step.op == "resolve_vendor":
            require_handle(args["name_handle"], alias, "string")
        elif step.op == "match_po":
            require_vendor(args["vendor"], alias)
            if f"${alias}.po_number" not in handles:
                raise PlanValidationError("Purchase order matching requires the invoice PO handle")
        elif step.op == "due_within":
            require_handle(args["date_handle"], alias, "date")
            days = require_literal(args["days"])
            if type(days) is not int or not 0 <= days <= 3650:
                raise PlanValidationError("Due window must be an integer from zero to 3650 days")
        elif step.op == "pay":
            require_vendor(args["vendor"], alias)
            require_handle(args["amount"], alias, "money")
            require_handle(args["to_account"], alias, "account_number")
            if args["memo"].kind == "handle":
                require_handle(args["memo"], alias, "string")
            else:
                memo = require_literal(args["memo"])
                if not isinstance(memo, str) or len(memo) > 256:
                    raise PlanValidationError("Memo must be a string handle or a literal of at most 256 characters")
            terminals.add(alias)
        else:
            reason = require_literal(args["reason"])
            if not isinstance(reason, str) or reason not in _REASONS:
                raise PlanValidationError("Terminal reason must be a contract reason code")
            terminals.add(alias)
        previous[step.id] = step
    if terminals != set(table.invoices):
        raise PlanValidationError("Plan requires exactly one terminal action for every invoice")
    return validated


@dataclass(frozen=True)
class VendorRef:
    vendor_id: str
    name: str


@dataclass(frozen=True)
class PurchaseOrderRef:
    purchase_order_id: str
    vendor_id: str
    amount_cents: int
    currency: str


@dataclass(frozen=True)
class PaymentIntent:
    invoice_id: str
    invoice: Labeled[str]
    vendor: Labeled[VendorRef | None]
    amount: Labeled
    to_account: Labeled
    memo: Labeled
    prerequisites: tuple[Labeled, ...] = ()

    @property
    def sources(self) -> frozenset[Source]:
        return sources_of(self.invoice, self.vendor, self.amount, self.to_account, self.memo, *self.prerequisites)


@dataclass
class ExecutionResult:
    intents: list[PaymentIntent] = field(default_factory=list)
    outcomes: dict[str, list[str]] = field(default_factory=dict)
    outputs: dict[str, Labeled] = field(default_factory=dict)


def _normalized(value: Any) -> str:
    return " ".join(value.split()).casefold() if isinstance(value, str) else ""


class Interpreter:
    def __init__(
        self, table: HandleTable, vendors: Sequence[Mapping[str, Any]],
        purchase_orders: Sequence[Mapping[str, Any]], *, today: date | None = None,
    ) -> None:
        self.table = table
        self.vendors = [dict(vendor) for vendor in vendors]
        self.purchase_orders = [dict(order) for order in purchase_orders]
        self.today = today or datetime.now(timezone.utc).date()

    def resolve_vendor(self, name: Labeled) -> Labeled[VendorRef | None]:
        """Exact normalized matching first; fuzzy ties/weak matches are unresolved."""
        normalized = _normalized(name.value)
        exact = [vendor for vendor in self.vendors if normalized and _normalized(vendor["name"]) == normalized]
        if len(exact) == 1:
            vendor = exact[0]
            return Labeled(VendorRef(vendor["vendor_id"], vendor["name"]),
                           name.sources | {TrustedRecord("vendor", vendor["vendor_id"])})
        consulted = frozenset(TrustedRecord("vendor", vendor["vendor_id"]) for vendor in self.vendors)
        if exact or not normalized:
            return Labeled(None, name.sources | consulted)
        ranked = sorted(
            ((SequenceMatcher(None, normalized, _normalized(vendor["name"])).ratio(), vendor)
             for vendor in self.vendors),
            key=lambda item: (-item[0], item[1]["vendor_id"]),
        )
        # A deterministic minimum score and separation prevent arbitrary tie
        # breaking from selecting a recipient. Fuzzy identity is still untrusted.
        if not ranked or ranked[0][0] < 0.88 or (len(ranked) > 1 and ranked[0][0] - ranked[1][0] < 0.05):
            return Labeled(None, name.sources | consulted)
        vendor = ranked[0][1]
        return Labeled(VendorRef(vendor["vendor_id"], vendor["name"]), name.sources | consulted)

    def match_po(self, alias: str, vendor: Labeled[VendorRef | None]) -> Labeled[PurchaseOrderRef | None]:
        po_number = self.table.values[f"${alias}.po_number"]
        sources = sources_of(po_number, vendor)
        candidates = [order for order in self.purchase_orders
                      if vendor.value is not None and order["vendor_id"] == vendor.value.vendor_id]
        sources |= frozenset(TrustedRecord("purchase_order", order["purchase_order_id"]) for order in candidates)
        matches = [order for order in candidates if order["status"] == "open"
                   and _normalized(order["po_number"]) == _normalized(po_number.value)]
        if len(matches) != 1:
            return Labeled(None, sources)
        order = matches[0]
        return Labeled(PurchaseOrderRef(order["purchase_order_id"], order["vendor_id"],
                                        order["amount_cents"], order["currency"]), sources)

    def due_within(self, due: Labeled, days: Labeled[int]) -> Labeled[bool]:
        def due_by_window(value, window):
            try:
                return date.fromisoformat(value) <= self.today + timedelta(days=window)
            except (ValueError, TypeError):
                return False
        return Labeled.combine(due_by_window, due, days)

    def execute(self, steps: Sequence[PlanStep | dict]) -> ExecutionResult:
        steps = validate_plan(steps, self.table, self.vendors, self.purchase_orders)
        result = ExecutionResult()
        dependencies: dict[str, list[Labeled]] = {alias: [] for alias in self.table.invoices}

        def resolve(arg: PlanArg) -> Labeled:
            if arg.kind == "handle":
                return self.table.values[arg.ref]
            if arg.kind == "step":
                return result.outputs[arg.ref]
            if arg.kind == "literal":
                return Labeled(arg.value)
            vendor = next(vendor for vendor in self.vendors if vendor["vendor_id"] == arg.ref)
            return Labeled(VendorRef(vendor["vendor_id"], vendor["name"]),
                           frozenset({TrustedRecord("vendor", vendor["vendor_id"])}))

        for step in steps:
            args, alias = step.args, step.invoice_id
            invoice_id = self.table.invoices[alias]
            if step.op == "resolve_vendor":
                output = self.resolve_vendor(resolve(args["name_handle"]))
            elif step.op == "match_po":
                output = self.match_po(alias, resolve(args["vendor"]))
            elif step.op == "due_within":
                output = self.due_within(resolve(args["date_handle"]), resolve(args["days"]))
            elif step.op == "pay":
                intent = PaymentIntent(
                    invoice_id=invoice_id,
                    invoice=Labeled(invoice_id, frozenset({TrustedRecord("invoice", invoice_id)})),
                    vendor=resolve(args["vendor"]),
                    amount=resolve(args["amount"]), to_account=resolve(args["to_account"]),
                    memo=resolve(args["memo"]), prerequisites=tuple(dependencies[alias]),
                )
                result.intents.append(intent)
                # An interpreted pay operation is only a request. The runner
                # must pass this labeled intent through the deterministic gate.
                output = Labeled(intent, intent.sources)
            else:
                reason = resolve(args["reason"])
                result.outcomes[invoice_id] = [reason.value]
                output = Labeled(reason.value, reason.sources | sources_of(*dependencies[alias]))
            result.outputs[step.id] = output
            dependencies[alias].append(output)
        return result
