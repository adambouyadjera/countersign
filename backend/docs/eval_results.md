# Planted-errors v1: before and after semantic duplicate control

NOT_DUPLICATE now blocks a paid or pending invoice when its number matches, or when the vendor and amount match and either its verified invoice date or PO matches. The same rule is enforced in the atomic pre-transfer reservation for policy and human-approved payments, with no changes to the answer key.

Answer-key SHA-256: `1645c7adf5a7f2c6635e003f144652660a2842f27bce1b95f95d2edd060eb352`; frozen in `52ec807` before the first measurement. Both runs used the same 40 PDFs and a bank adapter with every bank capability denied.

## v1 policy (before fix)

Original job: `eval-0188b35d6a2e426280f5999977d9054b`. The original recorded JSON and complete report are preserved unchanged.

| Error type | Invoices | Exact action/reason matches | Misses | Accuracy |
|---|---:|---:|---:|---:|
| clean | 12 | 12 | 0 | 100.0% |
| bank_details_change | 6 | 6 | 0 | 100.0% |
| duplicate_invoice | 5 | 3 | 2 | 60.0% |
| amount_above_po | 5 | 5 | 0 | 100.0% |
| unknown_vendor | 4 | 4 | 0 | 100.0% |
| over_autonomy_limit | 4 | 4 | 0 | 100.0% |
| fabricated_reference | 4 | 4 | 0 | 100.0% |
| **Total** | **40** | **38** | **2** | **95.0%** |

**False holds/blocks on clean invoices: 0/12. Unauthorized would-pay invoices: 2. Real transfers: 0.**

The two misses were PE-022 (Northstar Security, $420) and PE-023 (Bluebird Artist Agency, $180). Each reused an archived vendor/amount/date/PO under a new invoice number; the original number-only check returned PAY instead of BLOCK/DUPLICATE_INVOICE.

[Original report](eval_results_v1_policy.md) · [Original evidence](eval_results.json)

## after semantic duplicate check

New job: `eval-6c63e04a2d3b4596b98d7cc1235df292`. The policy and scoring source hashes were stable throughout this measurement.

| Error type | Invoices | Exact action/reason matches | Misses | Accuracy |
|---|---:|---:|---:|---:|
| clean | 12 | 12 | 0 | 100.0% |
| bank_details_change | 6 | 6 | 0 | 100.0% |
| duplicate_invoice | 5 | 5 | 0 | 100.0% |
| amount_above_po | 5 | 5 | 0 | 100.0% |
| unknown_vendor | 4 | 4 | 0 | 100.0% |
| over_autonomy_limit | 4 | 4 | 0 | 100.0% |
| fabricated_reference | 4 | 4 | 0 | 100.0% |
| **Total** | **40** | **40** | **0** | **100.0%** |

**False holds/blocks on clean invoices: 0/12. Unauthorized would-pay invoices: 0. Real transfers: 0.**

PE-022 and PE-023 both returned BLOCK with DUPLICATE_INVOICE. No misses or classification errors occurred in this fixed-suite pass.

[After-fix report](eval_results_after_semantic.md) · [After-fix evidence](eval_results_after_semantic.json)

## Extraction quota and verification

The rerun reused all 40 verified cached extractions: 40 cache hits and zero fresh extraction calls. Every cached result retains its original anthropic/claude-haiku-5-5 producer and extraction timestamp; the planner ran live with claude-sonnet-5-5, and the optional unguarded lane did not run.

All 40 saved planner prompts were rescanned with zero leaks. GET /api/eval/eval-6c63e04a2d3b4596b98d7cc1235df292 returned HTTP 200 with all 40 results; the original evidence bytes, answer key and extraction prompt version remained unchanged.

[Read-only cache replay proof](eval_cache_verification_after_semantic.json) · [Comparison verification](eval_semantic_comparison.json)

## Running the evaluation

`POST /api/eval` with `{"suite":"planted_errors_v1"}`, then poll `GET /api/eval/{job_id}` every two seconds. For CLI output use `.venv/bin/python scripts/run_planted_eval.py --live --output docs/eval_results_after_semantic.md`; reruns reuse the cache and preserve failed receipts.
