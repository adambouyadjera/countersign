"""Configuration checks that never load developer credentials or call services."""

import os
from pathlib import Path

import pytest
from pydantic import SecretStr

from app.config import BACKEND_DIR, Settings


@pytest.fixture(autouse=True)
def isolate_settings_environment(monkeypatch):
    # Inspect names only: BaseSettings accepts any casing of environment keys.
    field_names = {name.casefold() for name in Settings.model_fields}
    for name in tuple(os.environ):
        if name.casefold() in field_names:
            monkeypatch.delenv(name, raising=False)


def test_every_example_environment_variable_has_a_settings_field():
    example = Path(BACKEND_DIR) / ".env.example"
    variable_names = {
        line.split("=", 1)[0].strip().casefold()
        for line in example.read_text().splitlines()
        if line.strip() and not line.lstrip().startswith("#") and "=" in line
    }
    assert variable_names
    assert variable_names <= Settings.model_fields.keys()


def test_missing_credentials_do_not_prevent_settings_startup():
    settings = Settings(_env_file=None)
    for name in (
        "nessie_api_key", "elastic_api_key", "anthropic_api_key",
        "gemini_api_key", "bitdeer_api_key",
    ):
        assert isinstance(getattr(settings, name), SecretStr)
        assert getattr(settings, name).get_secret_value() == ""
    assert settings.nessie_mode == "live"
    assert settings.elastic_mode == "live"
    assert settings.cors_origins == ["http://localhost:5173"]


@pytest.mark.parametrize(
    "origins",
    [
        " http://localhost:5173, https://example.invalid , ",
        '["http://localhost:5173", "https://example.invalid"]',
    ],
)
def test_cors_origins_accept_comma_separated_and_json_environment_values(monkeypatch, origins):
    monkeypatch.setenv("CORS_ORIGINS", origins)
    settings = Settings(_env_file=None)
    assert settings.cors_origins == ["http://localhost:5173", "https://example.invalid"]


def test_configured_credentials_are_redacted_in_settings_repr():
    credential_fields = {
        "nessie_api_key", "elastic_api_key", "anthropic_api_key",
        "gemini_api_key", "bitdeer_api_key",
    }
    synthetic_secret = "synthetic-secret-for-redaction-check"
    settings = Settings(_env_file=None, **{
        name: synthetic_secret for name in credential_fields
    })
    assert synthetic_secret not in repr(settings)
    assert "**********" in repr(settings)
    assert all(
        getattr(settings, name).get_secret_value() == synthetic_secret
        for name in credential_fields
    )
