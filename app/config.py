from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql://postgres:postgres@localhost:5432/solar"

    meta_app_secret: str = ""
    meta_verify_token: str = "change-me"
    meta_graph_version: str = "v21.0"
    meta_system_user_token: str = ""
    whatsapp_sales_phone_id: str = ""
    whatsapp_websites_phone_id: str = ""
    whatsapp_sales_number: str = ""  # display number in international format, digits only (for wa.me links)
    fb_page_id: str = ""

    anthropic_api_key: str = ""
    model_fast: str = "claude-haiku-5-5"
    model_main: str = "claude-sonnet-5-5"
    model_hard: str = "claude-opus-5-5"

    timezone: str = "Africa/Harare"

    quote_value_cap: float = 5000
    quote_history_tolerance: float = 0.20
    price_max_age_days: int = 7


@lru_cache
def get_settings() -> Settings:
    return Settings()
