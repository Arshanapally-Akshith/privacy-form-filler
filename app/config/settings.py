from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="APP_", extra="forbid")

    app_name: str = "privacy-form-filler"
    environment: str = "development"
    log_level: str = "INFO"
