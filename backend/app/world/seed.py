"""Trusted AP records and resumable, explicitly selected bank-world seeding.

Document contents never modify these records. The directory resolves display
numbers to bank accounts; membership there does not make a payee trusted.
"""

from __future__ import annotations

from typing import Any, Protocol

from app.store import Store


COMPANY_ID = "fogline-festival"
COMPANY_DISPLAY_NUMBER = "700700007777"
ATTACKER_DISPLAY_NUMBER = "999900009999"
ATTACKER_NAME = "Northwind Remittance Services"
COMPANY_FUNDING_CENTS = 25_000_000

# Fixture-compatible identifiers and amounts, with all five required services.
_VENDORS = (
    ("VEN-001", "Harbor Light Rentals", "harborlight.example", "100100001111", "Stage rigging", 125_000),
    ("VEN-002", "Seabreeze Sound", "seabreezesound.example", "200200002222", "Sound production", 85_000),
    ("VEN-003", "Golden Gate Sanitation", "goldengatesanitation.example", "300300003333", "Sanitation", 64_000),
    ("VEN-004", "Northstar Security", "northstarsecurity.example", "400400004444", "Security staffing", 42_000),
    ("VEN-005", "Bluebird Artist Agency", "bluebirdartists.example", "500500005555", "Artist agency", 18_000),
)


class SeedBank(Protocol):
    mode: str

    async def list_customers(self) -> list[dict[str, Any]]: ...

    async def create_customer(self, payload: dict[str, Any]) -> dict[str, Any]: ...

    async def list_accounts(self, customer_id: str) -> list[dict[str, Any]]: ...

    async def create_account(self, customer_id: str, payload: dict[str, Any]) -> dict[str, Any]: ...


def seed_trusted_world(store: Store) -> dict[str, Any]:
    """Insert absent trusted records without overwriting later verified changes."""
    company = store.get_record("companies", COMPANY_ID)
    if company is None:
        company = {
            "company_id": COMPANY_ID,
            "name": "Fogline Festival",
            "event_description": "A three-day San Francisco music festival",
            "city": "San Francisco",
            "duration_days": 3,
            "display_number": COMPANY_DISPLAY_NUMBER,
            "trust": "trusted",
        }
        store.save_record("companies", COMPANY_ID, company)

    vendors, purchase_orders = [], []
    for index, (vendor_id, name, domain, account, service, amount) in enumerate(_VENDORS, 1):
        po_id = f"PO-{index:03d}"
        vendor = store.get_record("vendors", vendor_id)
        if vendor is None:
            vendor = {
                "vendor_id": vendor_id,
                "name": name,
                "email_domain": domain,
                "email": f"billing@{domain}",
                "remit_account_number": account,
                "account_on_file_display": account,
                "purchase_order_ids": [po_id],
                "service_description": service,
                "trust": "trusted",
            }
            store.save_record("vendors", vendor_id, vendor)
        po = store.get_record("purchase_orders", po_id)
        if po is None:
            po = {
                "purchase_order_id": po_id,
                "po_number": po_id,
                "vendor_id": vendor_id,
                "amount_cents": amount,
                "currency": "USD",
                "status": "open",
                "description": f"{service} for Fogline Festival, San Francisco (three days)",
                "line_items": [{
                    "description": f"{service} - three-day festival services",
                    "quantity": 1,
                    "unit_price_cents": amount,
                }],
                "trust": "trusted",
            }
            store.save_record("purchase_orders", po_id, po)
        vendors.append(vendor)
        purchase_orders.append(po)
    return {"company": company, "vendors": vendors, "purchase_orders": purchase_orders}


def _customer_payload(label: str) -> dict[str, Any]:
    first, _, last = label.partition(" ")
    return {
        "first_name": first,
        "last_name": last or first,
        "address": {
            "street_number": "1",
            "street_name": "Festival Plaza",
            "city": "San Francisco",
            "state": "CA",
            "zip": "94103",
        },
    }


def _id(record: dict[str, Any], resource: str) -> str:
    value = record.get("_id")
    if not isinstance(value, str) or not value:
        raise ValueError(f"Bank returned no {resource} id; seed was not completed")
    return value


async def seed_world(store: Store, bank: SeedBank) -> dict[str, Any]:
    """Reuse saved IDs, recovering interrupted creates by deterministic names.

    Successful customer and account IDs are committed separately. A retry does
    not fund existing accounts or retry a POST within a failed invocation.
    Run only one seeder against a given bank world at a time: Nessie has no
    documented atomic idempotency key for customer/account creation.
    """
    world = seed_trusted_world(store)
    mode = bank.mode
    if mode not in {"live", "offline"}:
        raise ValueError("World seed requires an explicit live or offline bank")

    entities = [{
        "entity_id": COMPANY_ID,
        "role": "company",
        "label": world["company"]["name"],
        "display_number": world["company"]["display_number"],
        "initial_balance_cents": COMPANY_FUNDING_CENTS,
    }]
    entities.extend({
        "entity_id": vendor["vendor_id"],
        "vendor_id": vendor["vendor_id"],
        "role": "vendor",
        "label": vendor["name"],
        "display_number": vendor["remit_account_number"],
        "initial_balance_cents": 0,
    } for vendor in world["vendors"])
    entities.append({
        "entity_id": "northwind-remittance",
        "role": "attacker",
        "label": ATTACKER_NAME,
        "display_number": ATTACKER_DISPLAY_NUMBER,
        "initial_balance_cents": 0,
    })

    customers: list[dict[str, Any]] | None = None
    accounts = []
    for entity in entities:
        seed_id = f"{mode}:{entity['entity_id']}"
        saved = store.get_record("bank_seeds", seed_id) or {
            "seed_id": seed_id, "mode": mode, "entity_id": entity["entity_id"],
        }
        customer_id = saved.get("customer_id")
        if not customer_id:
            if customers is None:
                customers = await bank.list_customers()
            payload = _customer_payload(entity["label"])
            matches = [item for item in customers if all(
                item.get(field) == payload[field] for field in ("first_name", "last_name")
            )]
            if len(matches) > 1:
                raise ValueError("Multiple matching demo customers; refusing ambiguous seed")
            customer = matches[0] if matches else await bank.create_customer(payload)
            customer_id = _id(customer, "customer")
            if not matches:
                customers.append(customer)
            saved["customer_id"] = customer_id
            store.save_record("bank_seeds", seed_id, saved)

        account_id = saved.get("account_id")
        if not account_id:
            nickname = f"Countersign:{entity['entity_id']}:checking"
            existing = await bank.list_accounts(customer_id)
            matches = [account for account in existing if account.get("nickname") == nickname]
            if len(matches) > 1:
                raise ValueError("Multiple matching demo bank accounts; refusing ambiguous seed")
            account = matches[0] if matches else await bank.create_account(customer_id, {
                "type": "Checking",
                "nickname": nickname,
                "rewards": 0,
                "balance": entity["initial_balance_cents"] / 100,
            })
            account_id = _id(account, "account")
            saved["account_id"] = account_id
            store.save_record("bank_seeds", seed_id, saved)

        directory_entry = {
            **entity,
            "customer_id": customer_id,
            "account_id": account_id,
            "mode": mode,
        }
        # The active mode owns the display mapping. Previously seeded mode IDs
        # remain intact in bank_seeds and can be restored by rerunning its mode.
        store.save_record("bank_directory", entity["display_number"], directory_entry)
        accounts.append(directory_entry)
    return {"mode": mode, "company_id": COMPANY_ID, "accounts": accounts}
