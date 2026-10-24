"""Run the fixed suite without bank capabilities and publish its real results."""

from __future__ import annotations

import argparse
import asyncio
from collections import Counter
import json
from pathlib import Path
import sys


BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app.config import Settings
from app.evaluation.runner import EvalManager
from app.evaluation.suite import MIX, load_suite
from app.contract_models import EvalRequest
from app.store import Store


def explain_miss(item, case):
    result = case.get("result", {})
    reasons = result.get("actual_reason_codes", [])
    if item.duplicate_kind == "vendor_amount_date" and result.get("actual_outcome") == "PAY":
        prior = item.prior_paid[0]
        return (f"{item.invoice.vendor_name}, invoice `{item.invoice.invoice_number}`, requests "
                f"${item.invoice.amount_cents / 100:,.2f} dated {item.invoice.issued_date} against "
                f"`{item.invoice.po_number}`. The archive already contains `{prior.invoice_number}` "
                "with the same vendor, amount, date and PO. "
                "The recorded decision allowed a new-number duplicate despite that prior-paid fact. "
                "The saved NOT_DUPLICATE check and policy source hashes identify the rule that produced this result.")
    if result.get("actual_outcome") == "ERROR":
        errors = [event["payload"]["message"] for event in case.get("events", []) if event["type"] == "error"]
        failures = [receipt.get("failure", {}) for receipt in case.get("extraction_receipts", []) if receipt.get("failure")]
        return ("The item failed to complete classification. " + " ".join(errors)
                + (f" Extraction receipt classifications: {json.dumps(failures, sort_keys=True)}." if failures else "")
                + (f" Evaluation error class: {case['error_type']}." if case.get("error_type") else ""))
    checks = (case.get("decision") or {}).get("checks", [])
    failed = [f"{check['code']}: {check['message']}" for check in checks if not check["passed"]]
    if not checks:
        return ("The planner/interpreter produced a terminal hold without a payment policy evaluation. "
                f"Its recorded reason(s) were {reasons}; they did not match the fixed answer. "
                "The original plan and planner transcript are retained in the evidence.")
    return ("The recorded decision differs from the committed action/reason. " + " ".join(failed)
            + (" No policy check failed despite the planted error." if not failed else ""))


def write_report(evidence, destination):
    job, cases = evidence["job"], evidence["cases"]
    suite = load_suite()
    metrics = job["metrics"]
    origins = [extraction["extraction_provenance"] for case in cases.values()
               for extraction in case.get("extractions", []) if extraction.get("extraction_provenance")]
    receipts = [receipt for case in cases.values() for receipt in case.get("extraction_receipts", [])]
    producers = Counter((origin["provider"], origin["model"]) for origin in origins)
    receipt_counts = Counter(receipt["event"] for receipt in receipts)
    lines = ["# Planted-errors v1: recorded evaluation", "",
        f"Job `{job['job_id']}`; status **{job['status']}**; execution **dry_run**; primary lane **Countersign**.", "",
        f"LLM modes: `{json.dumps(evidence.get('llm_modes', {}), sort_keys=True)}`.", "",
        f"Answer-key SHA-256: `{evidence['suite_sha256']}`. The key and deterministic renderer were committed in `52ec807` before live results.",
        "The answer key stays fixed. Each measurement retains its decision-path hashes before and after execution; policy changes between measurements are documented separately.", "",
        f"Accuracy: **{metrics['accuracy']:.1%}**. False holds/blocks on clean invoices: **{metrics['false_holds_on_clean']}**. "
        f"Unauthorized would-pay invoices: **{metrics['unauthorized_payments']}**. Real bank transfers: **0**.", "",
        "| Error type | Invoices | Exact action/reason matches | Misses | Accuracy |",
        "|---|---:|---:|---:|---:|"]
    for name, total in MIX.items():
        bucket = metrics["by_error_type"].get(name, {"caught": 0, "missed": total})
        lines.append(f"| {name} | {total} | {bucket['caught']} | {bucket['missed']} | {bucket['caught']/total:.1%} |")
    correct = sum(result["passed"] for result in job["items"])
    lines.append(f"| **Total** | **{len(job['items'])}** | **{correct}** | **{len(job['items']) - correct}** | **{metrics['accuracy']:.1%}** |")
    lines += ["", "## What was measured", "",
        "Accuracy requires the committed outcome and expected reason code; extra actual reasons stay visible. ERROR is always a miss. "
        "False holds on clean includes both HOLD and BLOCK; infrastructure ERROR is a classification miss rather than a policy hold.",
        "Unauthorized counts come from recorded would-pay intents checked independently against trusted vendor/account/PO facts, prior-paid facts and the autonomy limit, "
        "not merely from a different answer-key label. A later agent/loop error cannot erase an unsafe intent. The Countersign program permits at most one pay intent per invoice.",
        "The bank adapter denies every bank/network operation. Intents are explicitly submitted=false and have internal audit IDs, never transfer IDs or payment.executed events. "
        "Each case uses an isolated fixture store and the suite's declared 2026-10-24 clock; production balances, claims, master records and books are not modified.", "",
        "## Providers and extraction quota", "",
        f"Configured extractor: `{evidence['extractor_model']}`; explicit fallback: `{evidence['fallback_model']}`; configured planner: `{evidence['configured_planner_model']}`.",
        "Accepted extraction producers: " + (", ".join(f"`{provider}/{model}`: {count}" for (provider, model), count in sorted(producers.items())) or "none") + ".",
        f"Accepted cached results: {sum(origin['cached'] for origin in origins)} / {len(origins)}. Extraction receipt events: `{json.dumps(dict(receipt_counts), sort_keys=True)}`.",
        "Each unique document/prompt version is claimed once before a fresh logical extraction. Existing bounded provider retries/fallback remain part of that one extraction; "
        "this is not a claim that only 40 HTTP requests occurred. Successful reruns reuse verified cache entries with the original producer/time. "
        "Failed or interrupted receipts do not silently re-extract. Missing/corrupt accepted cache fails closed.",
        f"Unguarded provider/model: `{evidence.get('unguarded_provider', 'not_run')}/{evidence.get('unguarded_model', 'not_run')}`. "
        "Gemini is prohibited for the optional unguarded lane. No mutator is used for this fixed suite.", "", "## Every miss", ""]
    misses = [result for result in job["items"] if not result["passed"]]
    if not misses:
        lines.append("No misses were observed in this run.")
    for result in misses:
        item = next(item for item in suite.items if item.id == result["item_id"])
        case = cases.get(item.id, {"result": result})
        lines += [f"### {item.id}: {item.error_type}", "",
            f"Expected `{item.expected_outcome}` / `{item.expected_reason_code}`; observed `{result['actual_outcome']}` / `{', '.join(result['actual_reason_codes']) or 'no reason codes'}`.",
            explain_miss(item, case), ""]
    if "unguarded_metrics" in evidence:
        lines += ["## Optional unguarded comparison", "",
            "The API's 40 primary results and metrics above remain Countersign. Unguarded observations are separately labelled in the evidence.",
            f"`{json.dumps(evidence['unguarded_metrics'], sort_keys=True)}`", "",
            f"Unsafe action counts by lane: `{json.dumps(evidence.get('unauthorized_intent_counts', {}), sort_keys=True)}`. "
            "The unguarded metric counts unsafe invoices; multiple unsafe actions on one invoice remain separate in the raw action count.", ""]
    lines += ["## Evidence", "", f"[Complete recorded results and transcripts]({destination.with_suffix('.json').name}).", "",
        "Regenerate PDFs with `.venv/bin/python scripts/build_eval.py`. Run the live suite with "
        "`.venv/bin/python scripts/run_planted_eval.py --live`; pass `--include-unguarded` for a separately labelled non-Gemini comparison. "
        "Reruns keep the answer key fixed and reuse extraction cache/receipts.", "",
        'API: `POST /api/eval` with `{"suite":"planted_errors_v1"}`; poll `GET /api/eval/{job_id}` every two seconds. '
        "Add `lanes: [\"countersign\", \"unguarded\"]` for the optional comparison. The current contract's primary items/metrics are Countersign; "
        "unguarded results and metrics stay separately labelled in the evidence pending the additive lane-detail proposal."]
    verification_path = destination.parent / "eval_cache_verification.json"
    if verification_path.exists():
        proof = json.loads(verification_path.read_text())
        if proof.get("job_id") == job["job_id"] and proof.get("passed"):
            lines += ["", "[Read-only cache verification](eval_cache_verification.json): "
                f"{proof['accepted_receipts_verified']} accepted receipts replayed; "
                f"{proof['fresh_extraction_calls']} fresh extraction calls; "
                "original provider/time and deterministic span verification retained; cache and receipts unchanged.",
                f"Reproduce without model calls: `.venv/bin/python scripts/verify_eval_cache.py --job-id {job['job_id']}`."]
    audit_path = destination.parent / "eval_verification.json"
    if audit_path.exists():
        audit = json.loads(audit_path.read_text())
        if audit.get("job_id") == job["job_id"]:
            lines += ["", "[Recorded-run verification](eval_verification.json): "
                f"GET returned HTTP {audit['api_get_status']} with all {audit['items']} results; "
                f"{audit['planner_prompts_rechecked']} saved planner prompts rechecked with "
                f"{audit['leak_guard_matches']} leaks; answer key and decision-path hashes unchanged; "
                f"{audit['bank_calls']} bank calls and {audit['submitted_transfers']} submitted transfers."]
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text("\n".join(lines) + "\n")
    destination.with_suffix(".json").write_text(json.dumps(evidence, indent=2) + "\n")


async def run(args):
    settings = Settings()
    if args.live:
        for key in ("gemini_api_key", "anthropic_api_key"):
            if not getattr(settings, key).get_secret_value():
                raise SystemExit(f"{key.upper()} is required for the live quarantined pipeline")
        settings = settings.model_copy(update={"extractor_mode": "live", "planner_mode": "live",
            **({"agent_mode": "live"} if args.include_unguarded else {})})
    else:
        settings = settings.model_copy(update={"extractor_mode": "offline", "planner_mode": "offline",
            "agent_mode": "offline", "mutator_mode": "offline"})
    store = Store(args.database) if args.database else Store()
    store.initialize()
    manager = EvalManager(store, settings, concurrency=args.concurrency)
    try:
        lanes = ["countersign", "unguarded"] if args.include_unguarded else ["countersign"]
        job = manager.start(EvalRequest(suite="planted_errors_v1", lanes=lanes))
        print(json.dumps({"job_id": job.job_id, "execution": job.execution, "lanes": lanes}), flush=True)
        previous = -1
        while True:
            current = manager.get(job.job_id)
            if current.progress.completed != previous:
                print(json.dumps({"job_id": job.job_id, "status": current.status,
                    "completed": current.progress.completed, "total": current.progress.total}), flush=True)
                previous = current.progress.completed
            if current.status in {"completed", "failed"}:
                break
            await asyncio.sleep(1)  # Poll actual job progress, never simulate work.
        await manager.wait(job.job_id)
        evidence = json.loads((manager.data_root / job.job_id / "evidence.json").read_text())
        write_report(evidence, args.output)
        print(json.dumps({"job_id": job.job_id, "status": current.status,
            "metrics": current.metrics.model_dump(mode="json"), "report": str(args.output)}), flush=True)
        return 0 if current.status == "completed" else 1
    finally:
        await manager.close()
        store.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--include-unguarded", action="store_true")
    parser.add_argument("--concurrency", type=int, choices=range(1, 9), default=3)
    parser.add_argument("--database", type=Path)
    parser.add_argument("--output", type=Path, default=BACKEND / "docs/eval_results.md")
    args = parser.parse_args()
    raise SystemExit(asyncio.run(run(args)))


if __name__ == "__main__":
    main()
