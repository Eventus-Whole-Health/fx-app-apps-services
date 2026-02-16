"""Runtime configuration for base app services."""
from __future__ import annotations

import functools
from typing import Optional

from pydantic import Field, HttpUrl
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    sql_executor_url: HttpUrl = Field(..., env="SQL_EXECUTOR_URL")
    sql_executor_scope: str = Field(..., env="SQL_EXECUTOR_SCOPE")
    sql_executor_server: str = Field("pipeline", env="SQL_EXECUTOR_SERVER")
    # Email endpoint (generic). Keep legacy Logic App fields for compatibility.
    logic_app_email_url: HttpUrl = Field(..., env="LOGIC_APP_EMAIL_URL")
    logic_app_timeout_seconds: int = Field(30, env="LOGIC_APP_TIMEOUT_SECONDS")
    email_api_url: Optional[HttpUrl] = Field(default=None, env="EMAIL_API_URL")
    email_api_timeout_seconds: Optional[int] = Field(default=None, env="EMAIL_API_TIMEOUT_SECONDS")
    application_insights_connection_string: Optional[str] = Field(
        default=None, env="APPLICATION_INSIGHTS_CONNECTION_STRING"
    )
    azure_storage_connection_string: str = Field(
        ..., env="AZURE_STORAGE_CONNECTION_STRING"
    )
    azure_storage_blob_container: str = Field(
        "app-data", env="AZURE_STORAGE_BLOB_CONTAINER"
    )

    class Config:
        case_sensitive = False


@functools.lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return cached settings instance."""
    return Settings()
