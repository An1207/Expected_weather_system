from datetime import date, datetime

from pydantic import BaseModel, ConfigDict


class HourlyObservation(BaseModel):
    observed_at: datetime
    temperature: float | None = None
    precipitation: float | None = None
    humidity: float | None = None
    wind_speed: float | None = None
    wind_direction: float | None = None
    local_pressure: float | None = None
    sea_level_pressure: float | None = None
    sunshine: float | None = None
    solar_radiation: float | None = None
    cloud_amount: float | None = None
    ground_temperature: float | None = None
    raw: dict[str, str | float | int | None]


class TodayWeather(BaseModel):
    station_id: str
    station_name: str
    observed_at: datetime | None
    temperature: float | None
    min_temperature: float | None
    max_temperature: float | None
    humidity: float | None
    precipitation: float | None
    wind_speed: float | None
    local_pressure: float | None
    cloud_amount: float | None
    source: str
    variables: dict[str, str | float | int | None]
    hourly: list[HourlyObservation]


class PredictionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    station_id: str
    observation_date: date
    predicted_for_date: date
    observed_avg_temperature: float
    predicted_avg_temperature: float
    model_version: str
    model_test_mae: float | None = None
    generated_at: datetime


class PredictionHistoryItem(BaseModel):
    predicted_for_date: date
    predicted_avg_temperature: float
    actual_avg_temperature: float | None = None
    model_version: str
    created_at: datetime


class DashboardResponse(BaseModel):
    station_name: str
    timezone: str = "Asia/Seoul"
    today: TodayWeather
    tomorrow: PredictionResponse
    recent_predictions: list[PredictionHistoryItem]


class HealthResponse(BaseModel):
    status: str
    database: str
    model: str
    model_version: str | None = None

