from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "Daily Temperature Forecast API"
    environment: str = "local"
    cors_origins: str = "http://localhost:3000,http://localhost:5173"

    kma_api_key: str = ""
    # File API integration is configured separately from the existing ASOS APIs.
    kma_apihub_auth_key: str = ""
    kma_apihub_hourly_file_url: str = ""
    kma_apihub_daily_file_url: str = ""
    kma_station_id: str = "108"
    kma_station_name: str = "서울"
    kma_daily_url: str = (
        "https://apis.data.go.kr/1360000/AsosDalyInfoService/getWthrDataList"
    )
    kma_hourly_url: str = (
        "https://apis.data.go.kr/1360000/AsosHourlyInfoService/getWthrDataList"
    )

    database_url: str = Field(
        default="mysql+pymysql://weather_app:weather_password@mysql:3306/weather_prediction"
    )
    model_dir: str = "/app/artifacts/v2"
    hourly_correction_dir: str = "/app/artifacts/hourly"
    cache_ttl_seconds: int = 600

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", case_sensitive=False, extra="ignore"
    )

    @property
    def cors_origin_list(self) -> list[str]:
        return [item.strip() for item in self.cors_origins.split(",") if item.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()

