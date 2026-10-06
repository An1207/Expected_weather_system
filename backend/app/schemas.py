from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field


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


class InputEvidence(BaseModel):
    name: str
    source_key: str | None = None
    raw_value: float | None = None
    model_value: float | None = None
    treatment: str


class HourlyCorrectionEvidence(BaseModel):
    status: str
    reason: str
    base_prediction: float
    correction: float
    final_prediction: float
    correction_model_version: str | None = None
    issue_hour: int
    availability_lag_hours: int
    issued_at: datetime | None = None
    cutoff_at: datetime | None = None
    coverage: float | None = None
    observed_hours: int = 0
    feature_count: int = 0
    raw_correction: float | None = None
    alpha: float | None = None
    inputs: list[InputEvidence] = Field(default_factory=list)


class PredictionCalculation(BaseModel):
    history_start: date
    history_end: date
    source_row_count: int
    current_avg_temperature: float
    catboost_residual: float
    lightgbm_residual: float
    catboost_prediction: float
    lightgbm_prediction: float
    catboost_weight: float
    lightgbm_weight: float
    unrounded_prediction: float
    base_inputs: list[InputEvidence]
    derived_inputs: list[InputEvidence]
    hourly_correction: HourlyCorrectionEvidence | None = None


class ModelScore(BaseModel):
    model: str
    mae: float
    rmse: float
    r2: float


class TrainingAudit(BaseModel):
    candidate_version: str
    compared_model_version: str
    candidate_mae: float
    incumbent_mae: float
    persistence_mae: float
    train_rows: int
    valid_rows: int
    test_rows: int
    test_start: str
    test_end: str
    promotion_approved: bool


class HourlyTrainingAudit(BaseModel):
    model_version: str
    pipeline_model_version: str | None = None
    base_model_version: str
    train_rows: int
    valid_rows: int
    test_rows: int
    train_start: str
    train_end: str
    valid_start: str
    valid_end: str
    test_start: str
    test_end: str
    validation_base_mae: float
    validation_corrected_mae: float
    test_base_mae: float
    test_corrected_mae: float
    test_base_rmse: float
    test_corrected_rmse: float
    test_eligible_rows: int
    alpha: float
    promotion_approved: bool
    base_unchanged: bool
    issue_hour: int
    availability_lag_hours: int


class ModelEvidence(BaseModel):
    model_version: str | None
    pipeline_model_version: str | None = None
    base_feature_count: int
    engineered_feature_count: int
    model_input_count: int
    forecast_offset_days: int
    data_start: str | None = None
    data_end: str | None = None
    train_end: str | None = None
    valid_end: str | None = None
    daily_source: str
    hourly_source: str
    cache_ttl_seconds: int
    scores: list[ModelScore]
    latest_training: TrainingAudit | None = None
    hourly_correction_ready: bool = False
    hourly_correction_audit: HourlyTrainingAudit | None = None


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
    calculation: PredictionCalculation | None = None


class PredictionHistoryItem(BaseModel):
    predicted_for_date: date
    predicted_avg_temperature: float
    actual_avg_temperature: float | None = None
    model_version: str
    created_at: datetime


class DashboardResponse(BaseModel):
    station_name: str
    timezone: str = "Asia/Seoul"
    server_time: datetime
    yesterday: TodayWeather | None = None
    today: TodayWeather | None = None
    tomorrow: PredictionResponse | None = None
    errors: dict[str, str] = Field(default_factory=dict)
    recent_predictions: list[PredictionHistoryItem]
    model_evidence: ModelEvidence | None = None


class HealthResponse(BaseModel):
    status: str
    database: str
    model: str
    model_version: str | None = None
    pipeline_model_version: str | None = None
    hourly_correction: str = "not_loaded"

