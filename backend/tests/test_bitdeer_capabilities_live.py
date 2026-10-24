"""Read-only catalog plus harmless, live function-calling capability probe."""

from datetime import datetime, timezone
import json
from pathlib import Path

import pytest

from app.adapters.unguarded import LiveAgentLLM
from app.config import Settings


@pytest.mark.live
@pytest.mark.asyncio
async def test_bitdeer_catalog_and_native_tool_capability():
    settings = Settings()
    if not settings.bitdeer_api_key.get_secret_value():
        pytest.skip("BITDEER_API_KEY is not configured")
    adapter = LiveAgentLLM(key=settings.bitdeer_api_key, base_url=settings.bitdeer_base_url,
                           model=settings.naive_model)
    try:
        result = await adapter.ensure_capabilities()
    except Exception:
        result = adapter.capability_report or {"chosen_model": settings.naive_model,
                    "protocol": None, "tool_calling_supported": None, "error": "capability_probe_failed"}
        result["checked_at"] = datetime.now(timezone.utc).isoformat()
        Path("docs/bitdeer_capabilities.json").write_text(json.dumps(result, indent=2))
        print(json.dumps(result))
        raise
    result["checked_at"] = datetime.now(timezone.utc).isoformat()
    Path("docs/bitdeer_capabilities.json").write_text(json.dumps(result, indent=2))
    print(json.dumps(result))
    assert settings.naive_model in result["available_models"]
    assert result["protocol"] in {"tools", "json_actions"}
    if result["protocol"] == "tools":
        assert result["tool_calling_supported"]
        assert any(probe["model"] == settings.naive_model and probe["tool_calling"] for probe in result["probes"])
    else:
        assert all(not probe["tool_calling"] for probe in result["probes"])
