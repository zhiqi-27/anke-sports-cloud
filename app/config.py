from functools import lru_cache
from pathlib import Path
import os
from typing import Literal

from cryptography.fernet import Fernet
from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="ANKE_SPORTS_", env_file=".env", extra="ignore", populate_by_name=True
    )
    env: Literal["local", "staging", "production"] = "local"
    storage_backend: Literal["sql", "cosmos", "documents-local"] = "sql"
    database_url: str = "sqlite:///./data/anke-sports.db"
    document_local_path: str = "data/documents-local.db"
    cosmos_endpoint: str = ""
    cosmos_database: str = "anke-sports"
    cosmos_state_container: str = "state"
    cosmos_index_container: str = "indexes"
    cosmos_auth: Literal["managed_identity", "azure_cli"] = "managed_identity"
    cosmos_client_id: str = ""
    cosmos_tenant_id: str = ""
    cosmos_subscription_id: str = ""
    public_url: str = "http://localhost:8787"
    web_url: str = "http://localhost:3000"
    local_preview: bool = False
    firebase_project_id: str = ""
    firebase_credentials_json: SecretStr = Field(default=SecretStr(""), repr=False)
    encryption_key: str = ""
    maintainer_ids: list[str] = []
    broadcast_checks_enabled: bool = False
    public_feed_source_keys: list[str] = []
    enabled_sports_providers: list[Literal["jolpica", "balldontlie", "football-data"]] = Field(
        default_factory=list
    )
    balldontlie_api_key: SecretStr = Field(
        default=SecretStr(""), validation_alias="BALLDONTLIE_API_KEY", repr=False
    )
    football_data_api_key: SecretStr = Field(
        default=SecretStr(""), validation_alias="FOOTBALL_DATA_API_KEY", repr=False
    )

    def cipher(self) -> Fernet:
        key = self.encryption_key
        if not key:
            if self.env != "local":
                raise RuntimeError("ANKE_SPORTS_ENCRYPTION_KEY is required outside local mode")
            path = Path("data/local-encryption.key")
            path.parent.mkdir(exist_ok=True)
            if not path.exists():
                path.write_bytes(Fernet.generate_key())
                path.chmod(0o600)
            key = path.read_text().strip()
        return Fernet(key.encode())


@lru_cache
def settings() -> Settings:
    result = Settings()
    if result.env != "local" and (result.local_preview or result.storage_backend == "documents-local"):
        raise RuntimeError("Deployed mode forbids local authentication and document adapters")
    if result.env != "local" and result.storage_backend == "sql" and result.database_url.startswith("sqlite"):
        raise RuntimeError("Deployed SQL mode requires Azure MySQL; Cosmos must be selected explicitly")
    if result.storage_backend == "cosmos":
        if not result.cosmos_endpoint or not result.cosmos_database:
            raise RuntimeError("Cosmos requires an explicit independent endpoint and database")
        if result.env != "local" and (
            result.cosmos_auth != "managed_identity" or not result.cosmos_client_id
        ):
            raise RuntimeError("Deployed Cosmos requires the dedicated managed identity")
    if os.getenv("WEBSITE_INSTANCE_ID") and result.env == "local":
        raise RuntimeError("Azure Functions requires an explicit staging or production environment")
    if result.env != "local":
        if not result.firebase_project_id or not result.encryption_key:
            raise RuntimeError("Deployed mode requires independent Firebase configuration and encryption key")
        if not result.public_url.startswith("https://") or not result.web_url.startswith("https://"):
            raise RuntimeError("Deployed public and Web URLs must use HTTPS")
    return result
