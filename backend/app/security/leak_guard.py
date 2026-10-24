"""Reject quarantined source text before the planner can receive a request.

The guard scans both the complete wire representation and every decoded JSON
string. JSON escaping therefore cannot hide quotes, Unicode, or nested message
content from the source-line and canary checks. No document text is included in
the error, which may be sent to clients and logs.
"""

from __future__ import annotations

from collections.abc import Sequence
import json
import re

from app.contract_models import DocumentText, LeakGuard


CANARY_PATTERN = re.compile(r"CS-CANARY-[A-Z0-9]{4}")


class ProvenanceLeakError(RuntimeError):
    """A planner request contained content from the untrusted document boundary."""

    stage = "leak_guard"
    safe_summary = "Planner prompt contains quarantined document content (leak_guard)."

    def __init__(self) -> None:
        super().__init__(self.safe_summary)


def _decoded_strings(serialized: str) -> set[str]:
    """Return the wire string plus all strings inside nested JSON content."""
    strings: set[str] = set()
    pending: list[object] = [serialized]
    while pending:
        item = pending.pop()
        if isinstance(item, dict):
            pending.extend(item.keys())
            pending.extend(item.values())
        elif isinstance(item, list):
            pending.extend(item)
        elif isinstance(item, str) and item not in strings:
            strings.add(item)
            # String-valued message content may itself contain a JSON object.
            # Each unique string is decoded once, preventing repeated work.
            try:
                decoded = json.loads(item)
            except (ValueError, RecursionError):
                continue
            if isinstance(decoded, (dict, list, str)):
                pending.append(decoded)
    return strings


class LeakGuardScanner:
    """Keep cumulative, contract-shaped audit counts for all planner attempts."""

    def __init__(self, documents: Sequence[DocumentText]) -> None:
        self._lines = frozenset(
            line.text
            for document in documents
            for page in document.pages
            for line in page.lines
            if len(line.text) >= 12
        )
        self._summary = LeakGuard(strings_checked=0, canaries_checked=0, matches=0)

    @property
    def summary(self) -> LeakGuard:
        return self._summary.model_copy(deep=True)

    def check(self, prompt: str) -> None:
        candidates = _decoded_strings(prompt)
        source_matches = sum(
            any(line in candidate for candidate in candidates)
            for line in self._lines
        )
        canary_matches = {
            match.group()
            for candidate in candidates
            for match in CANARY_PATTERN.finditer(candidate)
        }
        self._summary.strings_checked += len(self._lines)
        self._summary.canaries_checked += len(candidates)
        self._summary.matches += source_matches + len(canary_matches)
        if source_matches or canary_matches:
            raise ProvenanceLeakError()
