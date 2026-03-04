from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "Ralphite API"
    api_prefix: str = "/api/v1"
    secret_key: str = "dev-secret-change-me"
    access_token_ttl_seconds: int = 3600
    refresh_token_ttl_seconds: int = 604800
    database_url: str = "sqlite:///./runtime/ralphite.db"
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    model_config = SettingsConfigDict(env_file=".env", env_prefix="RALPHITE_")


settings = Settings()
