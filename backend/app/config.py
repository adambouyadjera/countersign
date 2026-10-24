"""Environment configuration; credentials are optional and never shown in reprs."""

from functools import lru_cache
from dataclasses import dataclass, field
from pathlib import Path
from typing import Annotated, Literal
from urllib.parse import urlsplit
import json

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


BACKEND_DIR = Path(__file__).resolve().parents[1]
GEMINI_OPENAI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"


def provider_name(base_url: str) -> str:
    """Label configured routing; never select a different endpoint at runtime."""
    host = (urlsplit(base_url).hostname or "").lower()
    if host == "generativelanguage.googleapis.com":
        return "gemini"
    if host == "bitdeer.ai" or host.endswith(".bitdeer.ai"):
        return "bitdeer"
    return "custom"


@dataclass(frozen=True)
class ProviderConfig:
    provider: str
    base_url: str
    model: str
    key: SecretStr = field(repr=False)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BACKEND_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
        hide_input_in_errors=True,
    )

    app_env: str = "development"
    cors_origins: Annotated[list[str], NoDecode] = ["http://localhost:5173"]
    anthropic_api_key: SecretStr = SecretStr("")
    planner_model: str = "claude-sonnet-5-5"
    planner_mode: Literal["live", "offline"] = "live"
    policy_amount_tolerance_cents: int = Field(default=0, ge=0)
    gemini_api_key: SecretStr = SecretStr("")
    extractor_model: str = "gemini-3.5-flash-lite"
    extractor_mode: Literal["live", "offline"] = "live"
    extractor_fallback_model: str = "claude-haiku-5-5"
    extractor_cache: Literal["on", "off"] = "on"
    bitdeer_api_key: SecretStr = SecretStr("")
    bitdeer_base_url: str = "https://api-inference.bitdeer.ai/v1"
    naive_base_url: str = ""
    naive_api_key: SecretStr = SecretStr("")
    naive_model: str = "Qwen/Qwen3.8-27B"
    agent_mode: Literal["live", "offline"] = "live"
    agent_max_steps: int = Field(default=12, ge=1, le=50)
    naive_request_interval_seconds: float = Field(default=0, ge=0, allow_inf_nan=False)
    unguarded_dry_run: bool = False
    eval_alt_model: str = ""
    mutator_model: str = "zai-org/GLM-5.3"
    mutator_base_url: str = ""
    mutator_api_key: SecretStr = SecretStr("")
    mutator_mode: Literal["live", "offline"] = "live"
    nessie_api_key: SecretStr = SecretStr("")
    nessie_base_url: str = "https://prod-api.nessieisreal.com"
    nessie_mode: Literal["live", "offline"] = "live"
    nessie_payment_method: Literal["native_transfer", "paired_withdrawal_deposit"] = "paired_withdrawal_deposit"
    elastic_url: str = ""
    elastic_api_key: SecretStr = SecretStr("")
    kibana_url: str = ""
    elastic_agent_id: str = "countersign-audit-analyst"
    elastic_mode: Literal["live", "offline"] = "live"

    @field_validator("naive_model", "mutator_model", mode="before")
    @classmethod
    def default_blank_model(cls, value, info):
        # The example file lists names only; an empty copied entry retains the
        # same documented Bitdeer default rather than selecting another model.
        if isinstance(value, str) and not value.strip():
            return "Qwen/Qwen3.8-27B" if info.field_name == "naive_model" else "zai-org/GLM-5.3"
        return value

    @field_validator("extractor_fallback_model", "extractor_cache", mode="before")
    @classmethod
    def default_blank_extraction_option(cls, value, info):
        if isinstance(value, str) and not value.strip():
            return "claude-haiku-5-5" if info.field_name == "extractor_fallback_model" else "on"
        return value

    @field_validator("naive_request_interval_seconds", mode="before")
    @classmethod
    def default_blank_interval(cls, value):
        return 0 if isinstance(value, str) and not value.strip() else value

    @field_validator("mutator_mode", mode="before")
    @classmethod
    def default_blank_mutator_mode(cls, value):
        return "live" if isinstance(value, str) and not value.strip() else value

    def provider_config(self, *, base_url: str, key: SecretStr, model: str) -> ProviderConfig:
        selected_url = base_url.strip() or self.bitdeer_base_url
        provider = provider_name(selected_url)
        legacy_bitdeer = selected_url.rstrip("/") == self.bitdeer_base_url.rstrip("/")
        if legacy_bitdeer and provider == "custom":
            provider = "bitdeer"
        selected_key = key
        if not selected_key.get_secret_value():
            if provider == "gemini":
                selected_key = self.gemini_api_key
            elif provider == "bitdeer":
                selected_key = self.bitdeer_api_key
        return ProviderConfig(provider, selected_url, model, selected_key)

    @property
    def naive_provider_config(self) -> ProviderConfig:
        return self.provider_config(base_url=self.naive_base_url, key=self.naive_api_key, model=self.naive_model)

    @property
    def mutator_provider_config(self) -> ProviderConfig:
        return self.provider_config(base_url=self.mutator_base_url, key=self.mutator_api_key, model=self.mutator_model)

    @field_validator("cors_origins", mode="before")
    @classmethod
    def parse_origins(cls, value: object) -> object:
        if isinstance(value, str):
            if value.lstrip().startswith("["):
                return json.loads(value)
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()
