"""Source verification uses original characters, never extractor assertions."""

import pytest

from app.contract_models import DocumentText
from app.pipeline.extraction_models import RawSpan
from app.pipeline.spans import verify_span


@pytest.fixture
def document():
    return DocumentText(
        document_id="document-1", invoice_id="invoice-1", pages=[
            {"page": 1, "lines": [
                {"line": 1, "text": "Account number: 100100001111"},
                {"line": 2, "text": "  Harbor\t Light   Rentals  "},
            ]},
            {"page": 2, "lines": [{"line": 1, "text": "TOTAL (USD) $1,250.00"}]},
        ],
    )


def _assert_original_slice(document, span):
    original = next(line.text for page in document.pages if page.page == span.page for line in page.lines if line.line == span.line)
    assert original[span.char_start:span.char_end] == span.quote


def test_exact_match_computes_original_character_offsets(document):
    span = verify_span(document, RawSpan(quote="100100001111", page=1, line=1))
    assert span.span_verified and span.document_id == document.document_id
    assert (span.char_start, span.char_end) == (16, 28)
    _assert_original_slice(document, span)


def test_whitespace_normalization_preserves_original_quote_and_offsets(document):
    span = verify_span(document, RawSpan(quote="Harbor Light Rentals", page=1, line=2))
    assert span.span_verified
    assert span.quote == "Harbor\t Light   Rentals"
    assert (span.char_start, span.char_end) == (2, 25)
    _assert_original_slice(document, span)


def test_incorrect_page_and_line_fall_back_to_other_document_lines(document):
    span = verify_span(document, RawSpan(quote="$1,250.00", page=9, line=8))
    assert span.span_verified and (span.page, span.line) == (2, 1)
    _assert_original_slice(document, span)


def test_stated_line_is_preferred_over_an_earlier_duplicate(document):
    document.pages[1].lines[0].text = "Another account 100100001111"
    span = verify_span(document, RawSpan(quote="100100001111", page=2, line=1))
    assert span.span_verified and span.page == 2


@pytest.mark.parametrize("quote", ["fabricated account 999900009999", "", " \t "])
def test_unfindable_or_empty_quote_is_not_verified(document, quote):
    span = verify_span(document, RawSpan(quote=quote, page=1, line=1))
    assert not span.span_verified
    assert span.quote == quote and span.char_start == span.char_end == 0


def test_a_caller_cannot_self_assert_verification(document):
    span = verify_span(document, {
        "quote": "fabricated", "page": 1, "line": 1, "span_verified": True,
        "char_start": 0, "char_end": 10, "document_id": "other-document",
    })
    assert not span.span_verified and span.document_id == document.document_id
    assert not verify_span(document, span).span_verified


def test_cross_line_claim_is_not_certified_as_a_single_line_span(document):
    span = verify_span(document, RawSpan(quote="100100001111 Harbor Light", page=1, line=1))
    assert not span.span_verified
