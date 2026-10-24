"""Immutable values whose derivations retain every contributing source.

Operations must use ``map``/``combine`` (or the provided arithmetic operators),
never unwrap a value and silently relabel its result. The interpreter also
retains control dependencies separately when an earlier result affects an action.
"""

from __future__ import annotations

from dataclasses import dataclass
import operator
from typing import Callable, Generic, TypeVar


T = TypeVar("T")
U = TypeVar("U")


@dataclass(frozen=True)
class TrustedRecord:
    kind: str
    id: str


@dataclass(frozen=True)
class DocumentSpan:
    document_id: str
    page: int
    line: int
    char_start: int
    char_end: int
    span_verified: bool


Source = TrustedRecord | DocumentSpan


@dataclass(frozen=True, eq=False)
class Labeled(Generic[T]):
    value: T
    sources: frozenset[Source] = frozenset()
    __hash__ = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "sources", frozenset(self.sources))
        if any(not isinstance(source, (TrustedRecord, DocumentSpan)) for source in self.sources):
            raise TypeError("Labels must contain known provenance sources")

    def map(self, operation: Callable[[T], U]) -> Labeled[U]:
        return Labeled(operation(self.value), self.sources)

    @staticmethod
    def combine(operation: Callable[..., U], *values: Labeled) -> Labeled[U]:
        """Apply an operation, unioning labels even for a false/empty result."""
        if any(not isinstance(value, Labeled) for value in values):
            raise TypeError("combine requires labeled arguments")
        return Labeled(operation(*(value.value for value in values)), sources_of(*values))

    def _binary(self, other, operation):
        other = other if isinstance(other, Labeled) else Labeled(other)
        return self.combine(operation, self, other)

    def __add__(self, other):
        return self._binary(other, operator.add)

    def __radd__(self, other):
        return self._binary(other, lambda left, right: right + left)

    def __sub__(self, other):
        return self._binary(other, operator.sub)

    def __rsub__(self, other):
        return self._binary(other, lambda left, right: right - left)

    def __mul__(self, other):
        return self._binary(other, operator.mul)

    def __rmul__(self, other):
        return self._binary(other, lambda left, right: right * left)

    def __truediv__(self, other):
        return self._binary(other, operator.truediv)

    def __rtruediv__(self, other):
        return self._binary(other, lambda left, right: right / left)

    def __eq__(self, other):
        return self._binary(other, operator.eq)

    def __ne__(self, other):
        return self._binary(other, operator.ne)

    def __lt__(self, other):
        return self._binary(other, operator.lt)

    def __le__(self, other):
        return self._binary(other, operator.le)

    def __gt__(self, other):
        return self._binary(other, operator.gt)

    def __ge__(self, other):
        return self._binary(other, operator.ge)

    def __and__(self, other):
        return self._binary(other, operator.and_)

    def __or__(self, other):
        return self._binary(other, operator.or_)

    def __getitem__(self, key):
        return self._binary(key, operator.getitem)

    def __bool__(self):
        raise TypeError("Explicitly retain labeled control dependencies before branching")


def sources_of(*values: Labeled) -> frozenset[Source]:
    return frozenset(source for value in values for source in value.sources)
