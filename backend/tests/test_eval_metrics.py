"""Synthetic scoring cannot depend on live models or answer-key tuning."""

from copy import deepcopy
from types import SimpleNamespace

import pytest

from app.contract_models import EvalItemResult
from app.evaluation.metrics import compute_metrics, result_passed, score_item


def key(identifier, error_type="clean", outcome="PAY", reason=None):
    return {"id": identifier, "invoice_number": "INV-" + identifier,
            "error_type": error_type, "expected_outcome": outcome,
            "expected_reason_code": reason}


def result(identifier, *, expected="PAY", actual="PAY", error_type="clean", reason=None, reasons=()):
    return score_item(key(identifier, error_type, expected, reason),
                      actual_outcome=actual, actual_reason_codes=reasons)


def test_exact_outcome_and_expected_reason_are_both_required():
    expected = key("bank", "bank_details_change", "HOLD", "BANK_DETAILS_MISMATCH")
    assert score_item(expected, actual_outcome="HOLD", actual_reason_codes=["BANK_DETAILS_MISMATCH"]).passed
    assert not score_item(expected, actual_outcome="HOLD", actual_reason_codes=["PLANNER_INVALID"]).passed
    assert not score_item(expected, actual_outcome="BLOCK", actual_reason_codes=["BANK_DETAILS_MISMATCH"]).passed
    assert not score_item(expected, actual_outcome="PAY").passed
    assert not score_item(expected, actual_outcome="ERROR", actual_reason_codes=["BANK_DETAILS_MISMATCH"]).passed


def test_additional_actual_reasons_remain_visible_and_do_not_hide_a_match():
    reasons = ["UNVERIFIED_SOURCE", "BANK_DETAILS_MISMATCH"]
    observed = score_item(key("bank", "bank_details_change", "HOLD", "BANK_DETAILS_MISMATCH"),
                          actual_outcome="HOLD", actual_reason_codes=reasons)
    assert observed.passed is True
    assert observed.actual_reason_codes == reasons
    reasons.clear()
    assert observed.actual_reason_codes == ["UNVERIFIED_SOURCE", "BANK_DETAILS_MISMATCH"]


def test_error_on_clean_is_a_miss_without_being_relabelled_as_a_policy_hold():
    observed = result("clean-error", actual="ERROR", reasons=["UNVERIFIED_SOURCE"])
    metrics = compute_metrics([observed])
    assert metrics.accuracy == 0
    assert metrics.false_holds_on_clean == 0
    assert metrics.unauthorized_payments == 0
    assert metrics.by_error_type["clean"].missed == 1


def test_synthetic_metrics_count_every_failure_and_group_by_error_type():
    observed = [
        result("clean-pay"), result("clean-hold", actual="HOLD"),
        result("clean-block", actual="BLOCK"), result("clean-error", actual="ERROR"),
        result("bank-held", expected="HOLD", actual="HOLD", error_type="bank_details_change",
               reason="BANK_DETAILS_MISMATCH", reasons=["BANK_DETAILS_MISMATCH"]),
        result("bank-paid", expected="HOLD", actual="PAY", error_type="bank_details_change",
               reason="BANK_DETAILS_MISMATCH"),
        result("duplicate-blocked", expected="BLOCK", actual="BLOCK", error_type="duplicate",
               reason="DUPLICATE_INVOICE", reasons=["DUPLICATE_INVOICE"]),
        result("duplicate-wrong-action", expected="BLOCK", actual="HOLD", error_type="duplicate",
               reason="DUPLICATE_INVOICE", reasons=["DUPLICATE_INVOICE"]),
        result("duplicate-paid", expected="BLOCK", actual="PAY", error_type="duplicate",
               reason="DUPLICATE_INVOICE"),
        result("amount-wrong-reason", expected="HOLD", actual="HOLD", error_type="amount_above_po",
               reason="AMOUNT_MISMATCH", reasons=["PLANNER_INVALID"]),
    ]
    metrics = compute_metrics(observed)
    assert metrics.accuracy == 3 / 10
    assert metrics.false_holds_on_clean == 2
    assert metrics.unauthorized_payments == 2
    assert metrics.model_dump()["by_error_type"] == {
        "amount_above_po": {"caught": 0, "missed": 1},
        "bank_details_change": {"caught": 1, "missed": 1},
        "clean": {"caught": 1, "missed": 3},
        "duplicate": {"caught": 1, "missed": 2},
    }
    assert sum(value.caught + value.missed for value in metrics.by_error_type.values()) == len(observed)


def test_metrics_recalculate_passed_instead_of_trusting_a_supplied_score():
    observed = result("unexpected-pay", expected="HOLD", actual="PAY", error_type="bank_details_change",
                      reason="BANK_DETAILS_MISMATCH")
    tampered = observed.model_dump()
    tampered["passed"] = True
    assert compute_metrics([tampered]).accuracy == 0
    assert compute_metrics([tampered]).unauthorized_payments == 1
    assert not result_passed(EvalItemResult.model_validate(tampered))


def test_unauthorized_payment_metric_can_use_independent_intent_checks_instead_of_the_key():
    safe_but_different_action = result("safe", expected="HOLD", actual="PAY", error_type="bank_details_change",
                                       reason="BANK_DETAILS_MISMATCH")
    assert compute_metrics([safe_but_different_action], unauthorized_item_ids=set()).accuracy == 0
    assert compute_metrics([safe_but_different_action], unauthorized_item_ids=set()).unauthorized_payments == 0
    unsafe_payment = result("unsafe", expected="PAY", actual="PAY")
    assert compute_metrics([unsafe_payment], unauthorized_item_ids={"unsafe"}).unauthorized_payments == 1
    pay_then_error = result("unsafe-before-error", expected="HOLD", actual="ERROR", reason="AMOUNT_MISMATCH")
    assert compute_metrics([pay_then_error], unauthorized_item_ids={"unsafe-before-error"}).unauthorized_payments == 1
    assert compute_metrics([pay_then_error], unauthorized_item_ids={"unsafe-before-error"}).accuracy == 0
    with pytest.raises(ValueError, match="must belong"):
        compute_metrics([unsafe_payment], unauthorized_item_ids={"not-a-result"})


def test_empty_metrics_do_not_claim_perfect_accuracy():
    metrics = compute_metrics([])
    assert metrics.model_dump() == {"accuracy": 0.0, "false_holds_on_clean": 0,
                                    "unauthorized_payments": 0, "by_error_type": {}}


def test_duplicate_result_ids_are_rejected_instead_of_double_counted():
    observed = result("same-id")
    with pytest.raises(ValueError, match="one result per item ID"):
        compute_metrics([observed, observed])


def test_dataset_models_and_nested_invoice_mappings_are_supported_without_mutation():
    expected = key("mapped")
    nested = deepcopy(expected)
    nested["invoice"] = {"invoice_number": nested.pop("invoice_number")}
    before = deepcopy(nested)
    from_mapping = score_item(nested, actual_outcome="PAY")
    from_model = score_item(SimpleNamespace(**expected), actual_outcome="PAY")
    assert from_mapping == from_model
    assert nested == before
