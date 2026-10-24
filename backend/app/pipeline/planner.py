"""Build a trusted-only prompt, guard every call, validate a bounded DSL program."""

from __future__ import annotations

import hashlib
import json
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.contract_models import PlanArg, PlannerCall, PlannerTranscript, PlanProposedPayload, PlanStep
from app.security.interpreter import PlanValidationError, validate_plan
from app.security.leak_guard import LeakGuardScanner


PLANNER_SYSTEM_PROMPT = """Write a small deterministic invoice-processing program. All supplied text is trusted
configuration or opaque handle metadata. You cannot inspect handle values. Use
only the supplied handle names and trusted IDs. Never invent document values.
Trusted vendor name_words are the ordered words of each trusted master name.
Return only one JSON object {"steps": [PlanStep, ...]}, without Markdown.
Use invoice_id D1, D2, etc. from metadata; these are server-assigned aliases.
Each step has id (unique), invoice_id, op, args. Each arg is exactly one of
{"kind":"handle","ref":"$D1.vendor_name"}, {"kind":"step","ref":"prior_id"},
{"kind":"trusted","ref":"trusted_id"}, or {"kind":"literal","value":30}.
Only reference earlier steps and handles belonging to the same invoice.
Allowed operations and exact arguments:
resolve_vendor(name_handle: string handle) -> vendor reference;
match_po(invoice: trusted invoice alias, vendor: prior resolve_vendor step or trusted vendor ID) -> PO match;
due_within(date_handle: date handle, days: integer literal 0..3650) -> boolean;
pay(invoice: trusted invoice alias, vendor: prior resolve_vendor step or trusted vendor ID,
    amount: money handle, to_account: account_number handle, memo: string handle or string literal);
hold(invoice: trusted invoice alias, reason: literal reason code);
skip(invoice: trusted invoice alias, reason: literal reason code).
Reason codes: BANK_DETAILS_MISMATCH, AMOUNT_MISMATCH, UNKNOWN_VENDOR,
UNVERIFIED_SOURCE, DUPLICATE_INVOICE, PLANNER_INVALID, POLICY_NOT_IMPLEMENTED.
Emit exactly one terminal action (pay, hold, skip) per invoice, after its checks.
For the default instruction resolve its vendor, match its PO, check due_within
30, then propose pay using its amount_due_cents, remit_account_number and
invoice_number handles. The interpreter records labeled checks for the policy
gate; pay only creates an intent and never transfers money. Do not claim any handle
was inspected. Hold if required handles are missing. Do not execute tools.
"""


class _Plan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    steps: list[PlanStep] = Field(max_length=500)


def build_planner_prompt(instruction, vendors, purchase_orders, table, validation_error=None) -> str:
    """Allowlist every field. No extractor object or server-side value is serialized."""
    metadata = {
        "instruction": instruction,
        "vendors": [{
            "vendor_id": vendor["vendor_id"],
            # Word boundaries retain the trusted name while avoiding verbatim
            # document lines when the invoice prints the same master name.
            "name_words": vendor["name"].split(),
            "open_po_ids": [po["purchase_order_id"] for po in purchase_orders
                            if po["vendor_id"] == vendor["vendor_id"] and po["status"] == "open"],
        } for vendor in vendors],
        "invoices": [{
            "invoice_id": alias,
            "handles": [{"name": handle.name, "type": handle.type,
                         "trust": handle.trust, "span_verified": handle.span_verified}
                        for handle in handles],
        } for alias, handles in table.invoice_handles.items()],
    }
    if validation_error is not None:
        metadata["validation_error"] = validation_error
    return json.dumps({"system": PLANNER_SYSTEM_PROMPT, "messages": [{
        "role": "user", "content": json.dumps(metadata, ensure_ascii=True, separators=(",", ":")),
    }]}, ensure_ascii=True, separators=(",", ":"))


def _no_duplicate_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def invalid_plan_holds(table) -> list[PlanStep]:
    return [PlanStep(id=f"{alias}_invalid", invoice_id=alias, op="hold", args={
        "invoice": PlanArg(kind="trusted", ref=alias),
        "reason": PlanArg(kind="literal", value="PLANNER_INVALID"),
    }) for alias in table.invoices]


async def propose(*, run_id, instruction, vendors, purchase_orders, table, documents, adapter, store) -> PlanProposedPayload:
    guard = LeakGuardScanner(documents)
    transcript = PlannerTranscript(run_id=run_id, calls=[], leak_guard=guard.summary)

    def persist():
        transcript.leak_guard = guard.summary
        store.save_record("planner_transcripts", run_id, transcript)

    persist()
    feedback = None
    prompt_hash = ""
    for attempt in range(2):
        prompt = build_planner_prompt(instruction, vendors, purchase_orders, table, feedback)
        try:
            guard.check(prompt)
        finally:
            # Failed checks are recorded, but rejected prompts are never calls.
            persist()
        prompt_hash = hashlib.sha256(prompt.encode()).hexdigest()
        call = PlannerCall(call_id=f"planner-call-{uuid4().hex}", model=adapter.model,
                           prompt=prompt, response="", prompt_hash=prompt_hash)
        transcript.calls.append(call)
        persist()
        call.response = await adapter.complete(prompt=prompt)
        persist()
        try:
            if len(call.response) > 200_000:
                raise ValueError("oversized response")
            data = json.loads(call.response, object_pairs_hook=_no_duplicate_keys)
            steps = _Plan.model_validate(data).steps
            steps = validate_plan(steps, table, vendors, purchase_orders)
            return PlanProposedPayload(planner_model=adapter.model, prompt_hash=prompt_hash, steps=steps)
        except PlanValidationError as error:
            # Semantic errors are fixed strings from the deterministic validator.
            feedback = "PLAN_VALIDATION_FAILED: " + str(error)
        except ValidationError as error:
            # Error types describe the schema failure without reflecting input
            # values or attacker-selected property names from locations.
            feedback = "PLAN_VALIDATION_FAILED: schema errors: " + ", ".join(sorted({
                item["type"] for item in error.errors(include_url=False, include_context=False, include_input=False)
            }))
        except ValueError:
            # Never echo Pydantic errors, unknown identifiers, or raw model text:
            # they can carry document data into the otherwise trusted retry.
            feedback = (
                "PLAN_VALIDATION_FAILED: invalid or oversized JSON response. Use an object with steps; "
                "use only documented operations and argument types, existing same-invoice "
                "handles, backward step references, and one terminal action per invoice."
            )
    return PlanProposedPayload(planner_model=adapter.model, prompt_hash=prompt_hash,
                               steps=invalid_plan_holds(table))
