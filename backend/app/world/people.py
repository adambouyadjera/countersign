"""Trusted demo actors for the contract's countersign authorization boundary."""

from app.contract_models import Person


_PEOPLE = (
    ("person-ap-agent", "Fogline AP Agent", "ap_agent", 2500000),
    ("person-ap-manager", "Avery Chen", "ap_manager", 5000000),
    ("person-controller", "Jordan Patel", "controller", None),
    ("person-vendor-admin", "Morgan Diaz", "vendor_admin", 0),
)


def seed_people(store, *, autonomy_limit_cents: int | None = None) -> None:
    if autonomy_limit_cents is not None and (type(autonomy_limit_cents) is not int or autonomy_limit_cents < 0):
        raise ValueError("The AP agent autonomy limit must be nonnegative integer cents")
    for identifier, name, role, limit in _PEOPLE:
        if identifier == "person-ap-agent" and autonomy_limit_cents is not None:
            limit = autonomy_limit_cents
        existing = store.get_record("people", identifier)
        if existing is None:
            store.save_record("people", identifier, Person(
                person_id=identifier, name=name, role=role, approval_limit_cents=limit,
            ))
        elif identifier == "person-controller" and "approval_limit_cents" not in existing:
            store.save_record("people", identifier, {**existing, "approval_limit_cents": None})
        elif ((identifier == "person-ap-agent" and (autonomy_limit_cents is not None
                                                   or existing.get("approval_limit_cents") == 150000))
              or (identifier == "person-ap-manager" and existing.get("approval_limit_cents") == 200000)):
            # Explicit configured agent limits are authoritative; absent that,
            # migrate only the legacy demo defaults and retain custom limits.
            store.save_record("people", identifier, {**existing, "approval_limit_cents": limit})
