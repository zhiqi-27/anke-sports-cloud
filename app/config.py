from functools import lru_cache
from pathlib import Path
import os
from typing import Literal

from cryptography.fernet import Fernet
from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="ANKE_SPORTS_", env_file=".env", extra="ignore")
    env: Literal["local", "staging", "production"] = "local"
    database_url: str = "sqlite:///./data/anke-sports.db"
    public_url: str = "http://localhost:8787"
    web_url: str = "http://localhost:3000"
    local_preview: bool = False
    firebase_project_id: str = ""
    encryption_key: str = ""
    youtube_websub_enabled: bool = False
    maintainer_ids: list[str] = []
    broadcast_checks_enabled: bool = False
    balldontlie_api_key: SecretStr = Field(
        default=SecretStr(""), validation_alias="BALLDONTLIE_API_KEY", repr=False
    )
    football_data_api_key: SecretStr = Field(
        default=SecretStr(""), validation_alias="FOOTBALL_DATA_API_KEY", repr=False
    )
    youtube_api_key: SecretStr = Field(default=SecretStr(""), validation_alias="YOUTUBE_API_KEY", repr=False)

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
    if result.env != "local" and (result.local_preview or result.database_url.startswith("sqlite")):
        raise RuntimeError("Deployed mode requires production authentication and Azure MySQL")
    if os.getenv("WEBSITE_INSTANCE_ID") and result.env == "local":
        raise RuntimeError("Azure Functions requires an explicit staging or production environment")
    if result.env != "local":
        if not result.firebase_project_id or not result.encryption_key:
            raise RuntimeError("Deployed mode requires independent Firebase configuration and encryption key")
        if not result.public_url.startswith("https://") or not result.web_url.startswith("https://"):
            raise RuntimeError("Deployed public and Web URLs must use HTTPS")
    return result
