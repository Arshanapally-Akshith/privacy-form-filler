from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # extra="ignore": the project-root .env is a shared file and legitimately holds
    # non-APP_ keys this class doesn't declare -- e.g. OPENAI_API_KEY/GEMINI_API_KEY,
    # read directly from the process environment by their respective SDKs, never by this
    # class. pydantic-settings' dotenv source does not filter those out by env_prefix
    # before extra-field validation (verified empirically), unlike real OS environment
    # variables, which it does filter correctly.
    model_config = SettingsConfigDict(env_file=".env", env_prefix="APP_", extra="ignore")

    app_name: str = "privacy-form-filler"
    environment: str = "development"
    log_level: str = "INFO"
    enable_debug_endpoints: bool = True
