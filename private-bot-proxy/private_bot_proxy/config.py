from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="PBP_", case_sensitive=False)

    tenant_id: str = Field(min_length=36, max_length=36)
    app_id: str = Field(min_length=36, max_length=36)
    managed_identity_client_id: str = Field(min_length=36, max_length=36)
    oauth_connection_name: str = "teams-sso"
    fixed_response: str = "Private bot proxy proof: authenticated Teams turn received."
    graph_scope: str = "https://graph.microsoft.com/User.Read"


@lru_cache
def get_settings() -> Settings:
    return Settings()
