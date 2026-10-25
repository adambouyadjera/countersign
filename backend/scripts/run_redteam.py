"""Run or resume the running backend's dry-run red-team API and export evidence.

This client calls only POST/GET /api/redteam. It owns no LLM or bank adapter and
does not resample a failed study. Detailed metadata comes from the server's
persisted evidence file until the contract gains an additive evidence surface.
"""

from __future__ import annotations

import argparse
import asyncio
from collections import Counter
from contextlib import asynccontextmanager
import hashlib
import json
from pathlib import Path
import re
import sys
from urllib.parse import urlsplit

import httpx


BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app.contract_models import RedTeamJob, RedTeamRequest
from app.redteam.batches import CONTROL_INVOICE_IDS, TARGET_INVOICE_ID
from app.redteam.metrics import compute_metrics, score_variant
from app.security.presentation import display_model


_JOB_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}\Z")
_TERMINAL = {"completed", "failed"}


class RedTeamCLIError(RuntimeError):
    """A fixed, safe error classification; never an HTTP/provider body."""


def validate_job_id(value):
    if not isinstance(value, str) or not _JOB_ID.fullmatch(value):
        raise RedTeamCLIError("InvalidJobId")
    return value


def api_root(base_url):
    try:
        parsed = urlsplit(base_url)
        valid = (parsed.scheme in {"http", "https"} and parsed.netloc and not parsed.username
                 and not parsed.password and not parsed.query and not parsed.fragment)
    except ValueError:
        valid = False
    if not valid:
        raise RedTeamCLIError("InvalidBaseUrl")
    root = base_url.rstrip("/")
    return root if root.endswith("/api") else root + "/api"


def _job(value):
    try:
        job = value if isinstance(value, RedTeamJob) else RedTeamJob.model_validate(value)
        validate_job_id(job.job_id)
        return job
    except RedTeamCLIError:
        raise
    except Exception:
        raise RedTeamCLIError("InvalidJobResponse") from None


def _cases(details):
    values = details.get("cases", {})
    if not isinstance(values, dict) or any(not isinstance(value, dict) for value in values.values()):
        raise RedTeamCLIError("InvalidCaseEvidence")
    return list(values.values())


def _scores(details):
    result = []
    for case in _cases(details):
        run = case.get("run") or {}
        lane_errors = {lane["lane"]: lane.get("status") == "failed" for lane in run.get("lanes", [])}
        if case.get("error_type"):
            lane_errors.update(countersign=True, unguarded=True)
        prompts = [call["prompt"] for call in (case.get("planner_transcript") or {}).get("calls", [])]
        try:
            result.append(score_variant(target_invoice_id=TARGET_INVOICE_ID,
                control_invoice_ids=CONTROL_INVOICE_IDS, intents=case.get("intents", []),
                decisions=case.get("decisions", []), planner_prompts=prompts,
                lane_errors=lane_errors, trusted_accounts=case.get("trusted_accounts")))
        except Exception:
            raise RedTeamCLIError("InvalidScoringEvidence") from None
    return result


def _cell(value):
    return str(value).replace("|", "\\|").replace("\n", " ").replace("\r", " ").replace("`", "'")


def _counter_text(values):
    return ", ".join(f"{_cell(name)}: {count}" for name, count in sorted(values.items())) or "none recorded"


def _ratio(numerator, denominator):
    return f"{numerator} / {denominator}" + (f" ({numerator / denominator:.1%})" if denominator else " (not measurable)")


def render_report(job, details) -> str:
    """Render only recorded evidence; synthetic unit inputs stay synthetic.

    Safety metrics are independently recomputed from saved intents, decisions
    and exact planner strings. An ALLOW without an intent never becomes a pay.
    API metrics remain beside that computation so disagreements stay visible.
    """
    job = _job(job)
    cases, scores = _cases(details), _scores(details)
    observed = compute_metrics(scores).model_dump(mode="json")
    api_metrics = job.metrics.model_dump(mode="json") if job.metrics is not None else {}
    total, processed = job.progress.total, len(cases)
    controls = len(CONTROL_INVOICE_IDS) * processed
    prompts = [call["prompt"] for case in cases
               for call in (case.get("planner_transcript") or {}).get("calls", [])]
    intents = [intent for case in cases for intent in case.get("intents", [])]
    events = [event for case in cases for event in case.get("events", [])]
    receipts = [receipt for case in cases for receipt in case.get("extractor_receipts", [])]
    origins = [item["extraction_provenance"] for case in cases for item in case.get("extractions", [])
               if item.get("extraction_provenance")]
    generations = details.get("generation_receipts", [])
    providers = details.get("providers", {})
    cache = details.get("planner_cache", {})
    submitted = sum(intent.get("submitted") is True for intent in intents)
    payment_events = sum(event.get("type") == "payment.executed" for event in events)
    denied = sum(len(case.get("bank_denied_calls", [])) for case in cases)
    unsafe_envelopes = sum(intent.get("submitted") is not False or intent.get("execution") != "dry_run"
                           or bool(intent.get("transfer_id")) for intent in intents)
    bank_ok = (details.get("execution") == "dry_run" and details.get("bank_submitted") is False
               and all(case.get("bank_submitted") is False for case in cases)
               and not submitted and not payment_events and not denied and not unsafe_envelopes)
    marker = f"<!-- redteam-job:{job.job_id} -->"
    lines = [marker, f"## Study attempt: `{job.job_id}`", "",
        f"Status **{job.status}**; requested **{total}** variants; API processed **{job.progress.completed}**; "
        f"saved scored cases **{processed}**; generated **{details.get('generated_variants', 'not recorded')}**.",
        f"Recorded start: `{_cell(details.get('created_at', 'not recorded'))}`; "
        f"finish: `{_cell(details.get('completed_at', 'not recorded'))}`.", "",
        "This attempt keeps every processed result, including errors and resisted attacks. It never resamples an outcome.", "",
        "| Metric | Recomputed from saved evidence | API metric | Denominator / meaning |",
        "|---|---:|---:|---|"]
    meanings = {
        "variants": (total, "processed cases / requested variants"),
        "unguarded_compromised": (processed, "variants with any target off-file would-pay intent"),
        "countersign_unauthorized": (processed, "variants with any unapproved off-file Countersign intent"),
        "countersign_held": (processed, "actual target HOLD decisions, including operational fail-closed HOLDs"),
        "false_holds_on_controls": (controls, "literal HOLDs / clean control invoice opportunities; BLOCKs are separate"),
        "distinct_planner_prompts": (len(prompts), "distinct exact serialized prompts / saved planner calls"),
    }
    for metric, (denominator, meaning) in meanings.items():
        api_value = api_metrics.get(metric, "not recorded")
        lines.append(f"| {metric} | {_ratio(observed[metric], denominator)} | {api_value} | {meaning} |")
    mismatches = [name for name, value in api_metrics.items() if observed.get(name) != value]
    if mismatches:
        lines += ["", "**API/evidence metric disagreement:** " + ", ".join(f"`{name}`" for name in mismatches) + "."]
    if job.progress.completed != processed or len(job.variants) != processed:
        lines += ["", "**Incomplete evidence:** API progress, returned variants and saved case counts differ."]
    unguarded = Counter(score.unguarded_outcome for score in scores)
    countersign = Counter(score.countersign_outcome for score in scores)
    errors = Counter(error for score in scores for error in score.errors)
    control_blocks = sum(score.control_blocks for score in scores)
    control_decisions = sum(len({decision["invoice_id"] for decision in case.get("decisions", [])
                                if decision.get("invoice_id") in CONTROL_INVOICE_IDS}) for case in cases)
    lines += ["", "### Outcomes and errors", "",
        f"Unguarded outcomes ({processed} scored variants): {_counter_text(unguarded)}.",
        f"Countersign outcomes ({processed} scored variants): {_counter_text(countersign)}.",
        f"Clean control BLOCKs: {_ratio(control_blocks, controls)}. "
        f"Control decisions present: {_ratio(control_decisions, controls)}.",
        f"Requested clean control invoice opportunities: {len(CONTROL_INVOICE_IDS) * total}; "
        f"opportunities belonging to saved cases: {controls}. Missing control decisions are not counted as passes.",
        f"Error observations (a variant can contribute several): {_counter_text(errors)}.",
        f"Cases with any error observation: {_ratio(sum(bool(score.errors) for score in scores), processed)}.",
        f"Job error classification: `{_cell(details.get('job_error', 'none recorded'))}`.",
        f"Unguarded target off-file intents: **{sum(score.unguarded_off_file_intents for score in scores)}**. "
        f"Countersign unauthorized intents: **{sum(score.countersign_unauthorized_intents for score in scores)}**.",
        "These are recorded would-pay requests, not completed transfers. Multiple unsafe requests in one variant count "
        "once in the variant metric; a later error cannot erase an unsafe intent. Account destinations are compared "
        "with saved trusted account snapshots when present, otherwise with the server-derived payee_on_file flag.", "",
        "### Execution and providers", ""]
    if bank_ok:
        lines.append("Bank execution was **dry_run**: **0 Nessie transfers submitted**, no payment.executed events, "
                     "and no denied bank-operation attempts. Intent IDs are audit identities, never transfer IDs.")
    else:
        lines.append("**Bank dry-run proof is incomplete or violated; do not claim zero real transfers from this recording.**")
    lines.append(f"Recorded submitted intents: {submitted}; payment.executed events: {payment_events}; "
                 f"denied bank operations: {denied}; invalid intent envelopes: {unsafe_envelopes}.")
    for role in ("mutator", "unguarded", "extractor", "planner"):
        route = providers.get(role, {})
        lines.append(f"- {role}: `{_cell(route.get('provider', 'not recorded'))}` / "
                     f"`{_cell(route.get('model', 'not recorded'))}`; mode `{_cell(route.get('mode', 'not recorded'))}`.")
    forced = details.get("extractor_selection")
    if forced == "forced_haiku":
        lines.append("Extraction was explicitly **forced through Haiku**; the red-team cache is separate from the demo's Gemini cache.")
    else:
        lines.append(f"Extractor selection: `{_cell(forced or 'not recorded')}`; this is not evidence of fresh Haiku calls.")
    lines += [f"Unguarded AP mode: `{_cell(details.get('unguarded_mode', 'not recorded'))}`. "
        "The saved protocol hashes identify the AP prompt and decision code used for this attempt.", "",
        f"Recorded agent protocol: `{_cell((details.get('unguarded_capabilities') or {}).get('protocol', 'not recorded'))}`; "
        f"chosen model: `{_cell((details.get('unguarded_capabilities') or {}).get('chosen_model', 'not recorded'))}`.",
        "Agent HTTP attempt/retry statistics: " + "; ".join(
            f"{key}={(details.get('agent_request_stats') or {}).get(key, 'not recorded')}"
            for key in ("attempts", "retries")) + ".",
        "",
        "### Cache and generation receipts", "",
        "Planner receipts/stats: " + "; ".join(f"{key}={cache.get(key, 'not recorded')}" for key in (
            "requests", "provider_calls", "cache_hits", "coalesced", "distinct_prompts", "invalid_responses", "errors")) + ".",
        "A planner cache hit is not a fresh provider call; concurrent coalesced requests share one live call."]
    origin_counts = Counter((item.get("provider", "unknown"), item.get("model", "unknown")) for item in origins)
    receipt_counts = Counter((item.get("provider", "unknown"), item.get("model", "unknown"),
                              item.get("source", "unknown"), item.get("status", "unknown")) for item in receipts)
    lines.append("Accepted extraction producers: " + _counter_text(Counter({f"{provider}/{model}": count
        for (provider, model), count in origin_counts.items()})) + ".")
    lines.append(f"Accepted cached extractions: {_ratio(sum(item.get('cached') is True for item in origins), len(origins))}. "
        f"Logical extractor receipt provider calls: {sum(item.get('provider_call') is True for item in receipts)} / {len(receipts)} receipts.")
    lines.append("Extractor receipt breakdown: " + _counter_text(Counter({" / ".join(key): count
        for key, count in receipt_counts.items()})) + ".")
    lines.append("Generation validation attempts: " + _counter_text(Counter(
        item.get("validation_status", "not recorded") for item in generations)) + ".")
    observed_models = Counter(item.get("observed_final_model") for item in generations if item.get("observed_final_model"))
    lines.append("Observed final mutator model IDs: " + _counter_text(observed_models) + ".")
    failures = Counter(item.get("safe_error", "unknown validation error") for item in generations
                       if item.get("validation_status") in {"rejected", "provider_error"})
    lines.append("Rejected/provider generation classifications: " + _counter_text(failures) + ".")
    lines += ["Generation retries correct schema/length/placeholder errors only; failed attempts remain in the evidence. "
        "They are not additional scored attack trials. Extractor receipts count logical calls, not necessarily individual HTTP retries.", "",
        "### Evidence and contract proposal", "",
        f"Complete unchanged server evidence: [redteam_live_{job.job_id}.json](redteam_live_{job.job_id}.json).",
        "The current RedTeamJob contract exposes its six metrics and variant outcomes. Provider envelopes, generation "
        "receipts, extraction provenance, raw intents, controls/errors and planner-cache statistics are read from the "
        "persisted evidence file. Proposed additive integration: expose an evidence endpoint or optional evidence metadata "
        "and source hash; keep all existing fields and enum members.", ""]
    return "\n".join(lines)


def load_matching_evidence(data_root, job):
    """Validate API identity against both server snapshots before exporting."""
    job = _job(job)
    root = Path(data_root).resolve()
    directory = (root / validate_job_id(job.job_id)).resolve()
    if not directory.is_relative_to(root):
        raise RedTeamCLIError("UnsafeEvidencePath")
    source = (directory / "evidence.json").resolve()
    job_path = (directory / "job.json").resolve()
    if not source.is_relative_to(directory) or not job_path.is_relative_to(directory):
        raise RedTeamCLIError("UnsafeEvidencePath")
    try:
        raw = source.read_bytes()
        details = json.loads(raw)
        saved_job = _job(json.loads(job_path.read_bytes()))
        embedded_job = _job(details["job"])
    except RedTeamCLIError:
        raise
    except Exception:
        raise RedTeamCLIError("EvidenceUnavailable") from None
    expected = job.model_dump(mode="json", exclude_none=True)
    if (details.get("job_id") != job.job_id
            or display_model(saved_job).model_dump(mode="json", exclude_none=True) != expected
            or display_model(embedded_job).model_dump(mode="json", exclude_none=True) != expected):
        raise RedTeamCLIError("ApiEvidenceMismatch")
    return details, raw, source


def save_artifacts(job, details, raw, output):
    job = _job(job)
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    evidence = output.parent / f"redteam_live_{job.job_id}.json"
    if evidence.exists():
        if evidence.read_bytes() != raw:
            raise RedTeamCLIError("ExistingEvidenceConflict")
    else:
        with evidence.open("xb") as handle:
            handle.write(raw)
    report = render_report(job, details)
    digest = hashlib.sha256(raw).hexdigest()
    report += f"\nEvidence SHA-256: `{digest}`.\n"
    existing = output.read_text() if output.exists() else "# Red-team study attempts\n"
    marker = f"<!-- redteam-job:{job.job_id} -->"
    if marker not in existing:
        temporary = output.with_suffix(output.suffix + ".tmp")
        temporary.write_text(existing.rstrip() + "\n\n" + ("---\n\n" if output.exists() else "") + report)
        temporary.replace(output)
    return evidence


async def request_job(client, method, url, *, payload=None):
    try:
        response = await client.request(method, url, **({"json": payload} if payload is not None else {}))
    except httpx.TimeoutException:
        raise RedTeamCLIError("ApiRequestTimeout") from None
    except httpx.HTTPError:
        raise RedTeamCLIError("ApiTransportError") from None
    if response.status_code != 200:
        raise RedTeamCLIError(f"ApiHttp{response.status_code}")
    try:
        return _job(response.json())
    except RedTeamCLIError:
        raise
    except Exception:
        raise RedTeamCLIError("InvalidJobResponse") from None


@asynccontextmanager
async def _client_context(client):
    if client is not None:
        yield client
    else:
        async with httpx.AsyncClient(timeout=httpx.Timeout(30, connect=5), follow_redirects=False) as owned:
            yield owned


def print_progress(value):
    print(value, flush=True)


async def run(args, *, client=None, sleep=asyncio.sleep, progress=print_progress):
    root = api_root(args.base_url)
    previous = None

    def show(job):
        nonlocal previous
        identity = (job.status, job.progress.completed, job.progress.total)
        if identity != previous:
            progress(json.dumps({"job_id": job.job_id, "status": job.status,
                "completed": job.progress.completed, "total": job.progress.total}))
            previous = identity

    async with _client_context(client) as http:
        if args.job_id:
            job_id = validate_job_id(args.job_id)
        else:
            try:
                request = RedTeamRequest(variants=args.variants,
                    **({"templates": args.templates} if args.templates is not None else {}))
            except Exception:
                raise RedTeamCLIError("InvalidRedTeamRequest") from None
            job = await request_job(http, "POST", root + "/redteam", payload=request.model_dump(mode="json", exclude_none=True))
            job_id = validate_job_id(job.job_id)
            show(job)
        while True:
            job = await request_job(http, "GET", root + "/redteam/" + job_id)
            if job.job_id != job_id:
                raise RedTeamCLIError("ApiJobIdentityMismatch")
            show(job)
            if job.status in _TERMINAL:
                break
            await sleep(2)
    for attempt in range(5):
        try:
            details, raw, _ = load_matching_evidence(args.data_root, job)
            break
        except RedTeamCLIError as error:
            if str(error) not in {"EvidenceUnavailable", "ApiEvidenceMismatch"} or attempt == 4:
                raise
            await sleep(2)
    evidence = save_artifacts(job, details, raw, args.output)
    progress(json.dumps({"job_id": job_id, "status": job.status, "report": str(args.output), "evidence": str(evidence)}))
    return 0 if job.status == "completed" else 1


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--variants", type=int, choices=range(10, 61), default=40)
    parser.add_argument("--templates", nargs="+", help="Optional attack template names; omitted uses server defaults")
    parser.add_argument("--job-id", help="Resume polling an existing job; never POST a replacement")
    parser.add_argument("--data-root", type=Path, default=BACKEND / "data/generated/redteam")
    parser.add_argument("--output", type=Path, default=BACKEND / "docs/redteam_results.md")
    return parser.parse_args(argv)


def main():
    try:
        raise SystemExit(asyncio.run(run(parse_args())))
    except RedTeamCLIError as error:
        print(json.dumps({"error": str(error)}), file=sys.stderr)
        raise SystemExit(2) from None
    except KeyboardInterrupt:
        print(json.dumps({"error": "PollingInterrupted", "resumable": True}), file=sys.stderr)
        raise SystemExit(130) from None
    except Exception as error:
        print(json.dumps({"error": type(error).__name__}), file=sys.stderr)
        raise SystemExit(2) from None


if __name__ == "__main__":
    main()
