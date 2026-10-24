"""Verify Google's compatible endpoint with the actual AP tool schemas."""

from datetime import datetime, timezone
import json
from pathlib import Path

from openai import AsyncOpenAI
import pytest

from app.config import Settings
from app.events import EventBus
from app.pipeline.unguarded_tools import UnguardedTools
from app.store import Store
from app.world.seed import seed_world
from app.adapters.bank import OfflineBankAdapter


GEMINI_OPENAI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"
GEMINI_DEMO_MODEL = "gemini-3.5-flash-lite"


@pytest.mark.live
@pytest.mark.skip(reason="Gemini quota is reserved for the Countersign extractor; live AP probes are disabled.")
@pytest.mark.asyncio
async def test_gemini_compatible_endpoint_accepts_all_ap_tools():
    settings = Settings()
    if not settings.gemini_api_key.get_secret_value():
        pytest.skip("GEMINI_API_KEY is not configured")
    store = Store(":memory:")
    store.initialize()
    try:
        await seed_world(store, OfflineBankAdapter(store=store))
        tools = UnguardedTools(store, EventBus(store), OfflineBankAdapter(store=store),
            run_id="gemini-capability-check", invoice_id="gemini-capability-invoice", dry_run=True,
            provider="gemini", model=GEMINI_DEMO_MODEL)
        probes = []
        async with AsyncOpenAI(api_key=settings.gemini_api_key.get_secret_value(),
                               base_url=GEMINI_OPENAI_BASE_URL, max_retries=0, timeout=90) as client:
            catalog = await client.models.list()
            available = [row.id for row in catalog.data]
            assert GEMINI_DEMO_MODEL in {name.removeprefix("models/") for name in available}
            for name, prompt in (
                ("list_vendors", "Call list_vendors with no arguments."),
                ("lookup_account", "Call lookup_account for account number 300300003333."),
                ("pay", 'Call pay using account_id="capability-only-account", amount_cents=64000, invoice_number="CAPABILITY-ONLY", memo="Schema capability check". This call is inspected only and never executed.'),
            ):
                response = await client.chat.completions.create(
                    model=GEMINI_DEMO_MODEL, messages=[{"role": "user", "content": prompt}],
                    tools=tools.definitions, tool_choice={"type": "function", "function": {"name": name}},
                    max_tokens=2048,
                )
                calls = response.choices[0].message.tool_calls or []
                assert response.choices[0].finish_reason != "length"
                assert any(call.function.name == name for call in calls)
                probes.append({"tool": name, "finish_reason": response.choices[0].finish_reason,
                               "tool_calls": [{"name": call.function.name, "arguments": json.loads(call.function.arguments),
                                               "has_provider_metadata": bool(getattr(call, "extra_content", None))} for call in calls]})
        evidence = {"provider": "gemini", "model": GEMINI_DEMO_MODEL, "base_url": GEMINI_OPENAI_BASE_URL,
                    "checked_at": datetime.now(timezone.utc).isoformat(), "available_models": available,
                    "tool_calling_supported": True, "protocol": "tools", "probes": probes,
                    "payment_tools_executed": 0, "transfers": 0}
        Path("docs/gemini_capabilities.json").write_text(json.dumps(evidence, indent=2))
        print(json.dumps({key: value for key, value in evidence.items() if key != "available_models"}))
        assert not tools.calls and not tools.payments
    finally:
        store.close()
