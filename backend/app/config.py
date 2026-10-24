"""Environment configuration; credentials are optional and never shown in reprs."""

from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal
import json

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


BACKEND_DIR = Path(__file__).resolve().parents[1]


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
    bitdeer_api_key: SecretStr = SecretStr("")
    bitdeer_base_url: str = "https://api-inference.bitdeer.ai/v1"
    naive_model: str = "Qwen/Qwen3.8-27B"
    agent_mode: Literal["live", "offline"] = "live"
    agent_max_steps: int = Field(default=12, ge=1, le=50)
    unguarded_dry_run: bool = False
    eval_alt_model: str = ""
    mutator_model: str = "zai-org/GLM-5.3"
    nessie_api_key: SecretStr = SecretStr("")
    nessie_base_url: str = "https://prod-api.nessieisreal.com"
    nessie_mode: Literal["live", "offline"] = "live"
    nessie_payment_method: Literal["native_transfer", "paired_withdrawal_deposit"] = "paired_withdrawal_deposit"
    elastic_url: str = ""
    elastic_api_key: SecretStr = SecretStr("")
    kibana_url: str = ""
    elastic_agent_id: str = "countersign-audit-analyst"
    elastic_mode: Literal["live", "offline"] = "live"

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
