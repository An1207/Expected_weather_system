from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select, text
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.orm import Session

from .config import get_settings
from .database import Base, engine, get_db
from .models import ModelVersion, TemperaturePrediction
from .schemas import (
    DashboardResponse,
    HealthResponse,
    HourlyObservation,
    PredictionHistoryItem,
    PredictionResponse,
    TodayWeather,
    ModelEvidence,
    TrainingAudit,
)
from .services.kma import KmaClient
from .services.predictor import PredictionResult, TemperaturePredictor

KST = ZoneInfo("Asia/Seoul")
settings = get_settings()
kma = KmaClient(settings)
predictor = TemperaturePredictor(settings.model_dir)


@asynccontextmanager
async def lifespan(_: FastAPI):
    Base.metadata.create_all(bind=engine)
    yield


app = FastAPI(title=settings.app_name, version="1.0.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


def register_model(db: Session) -> None:
    if not predictor.ready or not predictor.model_version:
        return
    existing = db.scalar(select(ModelVersion).where(ModelVersion.model_version == predictor.model_version))
    if existing:
        return
    score = predictor.test_mae
    metadata = predictor.metadata
    db.add(
        ModelVersion(
            model_version=predictor.model_version,
            model_type="CatBoost-LightGBM residual ensemble",
            station_id=settings.kma_station_id,
            target_name=f"t+{metadata.get('forecast_offset_days', 2)} 평균기온",
            trained_from=None,
            trained_to=None,
            validation_mae=None,
            test_mae=Decimal(str(round(score, 4))) if score is not None else None,
            artifact_uri=settings.model_dir,
            metadata_json=metadata,
        )
    )
    db.commit()


def persist_prediction(db: Session, result: PredictionResult) -> TemperaturePrediction:
    register_model(db)
    statement = mysql_insert(TemperaturePrediction).values(
        station_id=result.station_id,
        observation_date=result.observation_date,
        predicted_for_date=result.predicted_for_date,
        observed_avg_temperature=Decimal(str(result.observed_avg_temperature)),
        predicted_avg_temperature=Decimal(str(result.predicted_avg_temperature)),
        model_version=result.model_version,
        source="KMA_ASOS_DAILY",
        input_snapshot=result.input_snapshot,
    )
    statement = statement.on_duplicate_key_update(
        observation_date=statement.inserted.observation_date,
        observed_avg_temperature=statement.inserted.observed_avg_temperature,
        predicted_avg_temperature=statement.inserted.predicted_avg_temperature,
        input_snapshot=statement.inserted.input_snapshot,
        updated_at=datetime.utcnow(),
    )
    db.execute(statement)
    db.commit()
    return db.scalar(
        select(TemperaturePrediction).where(
            TemperaturePrediction.station_id == result.station_id,
            TemperaturePrediction.predicted_for_date == result.predicted_for_date,
            TemperaturePrediction.model_version == result.model_version,
        )
    )


async def make_prediction(db: Session) -> PredictionResponse:
    try:
        daily_items = await kma.recent_daily_history(days=45)
        result = await asyncio.to_thread(predictor.predict, daily_items)
        if result.predicted_for_date != datetime.now(KST).date() + timedelta(days=1):
            raise RuntimeError("최신 일자료가 아직 공개되지 않아 내일 예측을 생성할 수 없습니다.")
        persisted = persist_prediction(db, result)
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return PredictionResponse(
        station_id=result.station_id,
        observation_date=result.observation_date,
        predicted_for_date=result.predicted_for_date,
        observed_avg_temperature=result.observed_avg_temperature,
        predicted_avg_temperature=result.predicted_avg_temperature,
        model_version=result.model_version,
        model_test_mae=result.model_test_mae,
        generated_at=persisted.updated_at.replace(tzinfo=ZoneInfo("UTC")).astimezone(KST),
        calculation=result.input_snapshot.get("calculation"),
    )


def model_evidence() -> ModelEvidence | None:
    if not predictor.ready:
        return None
    metadata = predictor.metadata
    config = metadata.get("config", {})
    audit = None
    audit_path = predictor.model_dir / "training_audit.json"
    if audit_path.is_file():
        try:
            audit = TrainingAudit.model_validate(json.loads(audit_path.read_text(encoding="utf-8")))
        except (ValueError, OSError):
            pass  # Optional audit does not make prediction unavailable.
    return ModelEvidence(
        model_version=predictor.model_version,
        base_feature_count=int(metadata.get("base_feature_count", 44)),
        engineered_feature_count=len(predictor.feature_columns),
        model_input_count=len(predictor.preprocessor.output_columns_),
        forecast_offset_days=int(metadata.get("forecast_offset_days", 2)),
        data_start=config.get("start_date"), data_end=config.get("end_date"),
        train_end=config.get("train_end"), valid_end=config.get("valid_end"),
        daily_source="ASOS 일자료 + API Hub 최신 일자료" if settings.kma_apihub_daily_file_url else "ASOS 일자료",
        hourly_source="API Hub 시간자료 (화면 표시 전용)" if settings.kma_apihub_hourly_file_url else "ASOS 시간자료 (화면 표시 전용)",
        cache_ttl_seconds=settings.cache_ttl_seconds,
        scores=metadata.get("scores", []), latest_training=audit,
    )


def prediction_history(db: Session, limit: int = 7) -> list[PredictionHistoryItem]:
    rows = db.scalars(
        select(TemperaturePrediction)
        .order_by(TemperaturePrediction.predicted_for_date.desc())
        .limit(limit)
    ).all()
    return [
        PredictionHistoryItem(
            predicted_for_date=row.predicted_for_date,
            predicted_avg_temperature=float(row.predicted_avg_temperature),
            actual_avg_temperature=(float(row.actual_avg_temperature) if row.actual_avg_temperature is not None else None),
            model_version=row.model_version,
            created_at=row.created_at.replace(tzinfo=ZoneInfo("UTC")).astimezone(KST),
        )
        for row in rows
    ]


@app.get("/health", response_model=HealthResponse)
def health(db: Session = Depends(get_db)) -> HealthResponse:
    database_status = "connected"
    try:
        db.execute(text("SELECT 1"))
    except Exception:
        database_status = "unavailable"
    return HealthResponse(
        status="ok" if database_status == "connected" and predictor.ready else "degraded",
        database=database_status,
        model="ready" if predictor.ready else f"unavailable: {predictor.error}",
        model_version=predictor.model_version,
    )


@app.get("/api/v1/weather/today", response_model=TodayWeather)
async def today_weather() -> TodayWeather:
    try:
        return await kma.today_weather()
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.get("/api/v1/weather/hourly", response_model=list[HourlyObservation])
async def hourly_weather():
    try:
        return [item.model_dump() for item in (await kma.today_weather()).hourly]
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.get("/api/v1/weather/yesterday", response_model=TodayWeather)
async def yesterday_weather() -> TodayWeather:
    try:
        return await kma.yesterday_weather()
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.post("/api/v1/predictions/run", response_model=PredictionResponse)
async def run_prediction(db: Session = Depends(get_db)) -> PredictionResponse:
    return await make_prediction(db)


@app.get("/api/v1/predictions/history", response_model=list[PredictionHistoryItem])
def get_prediction_history(
    limit: int = Query(default=7, ge=1, le=100), db: Session = Depends(get_db)
):
    return prediction_history(db, limit)


@app.get("/api/v1/dashboard", response_model=DashboardResponse)
async def dashboard(db: Session = Depends(get_db)) -> DashboardResponse:
    results = await asyncio.gather(
        kma.yesterday_weather(), kma.today_weather(), make_prediction(db),
        return_exceptions=True,
    )
    values = {}
    errors = {}
    for name, result in zip(("yesterday", "today", "tomorrow"), results):
        if isinstance(result, Exception):
            errors[name] = str(result.detail) if isinstance(result, HTTPException) else str(result)
            values[name] = None
        else:
            values[name] = result
    if errors:
        db.rollback()
    return DashboardResponse(
        station_name=settings.kma_station_name,
        server_time=datetime.now(KST),
        **values,
        errors=errors,
        recent_predictions=prediction_history(db),
        model_evidence=model_evidence(),
    )
