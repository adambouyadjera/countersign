"""Trusted demo actors for the contract's countersign authorization boundary."""

from app.contract_models import Person


_PEOPLE = (
    ("person-ap-agent", "Fogline AP Agent", "ap_agent", 150000),
    ("person-ap-manager", "Avery Chen", "ap_manager", 200000),
    ("person-controller", "Jordan Patel", "controller", None),
    ("person-vendor-admin", "Morgan Diaz", "vendor_admin", 0),
)


def seed_people(store) -> None:
    for identifier, name, role, limit in _PEOPLE:
        existing = store.get_record("people", identifier)
        if existing is None:
            store.save_record("people", identifier, Person(
                person_id=identifier, name=name, role=role, approval_limit_cents=limit,
            ))
        elif identifier == "person-controller" and "approval_limit_cents" not in existing:
            store.save_record("people", identifier, {**existing, "approval_limit_cents": None})
