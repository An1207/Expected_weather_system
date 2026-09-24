from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import JSON, Date, DateTime, ForeignKey, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base


class ModelVersion(Base):
    __tablename__ = "model_versions"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    model_version: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    model_type: Mapped[str] = mapped_column(String(100))
    station_id: Mapped[str] = mapped_column(String(10))
    target_name: Mapped[str] = mapped_column(String(100))
    trained_from: Mapped[date | None] = mapped_column(Date, nullable=True)
    trained_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    validation_mae: Mapped[Decimal | None] = mapped_column(Numeric(8, 4), nullable=True)
    test_mae: Mapped[Decimal | None] = mapped_column(Numeric(8, 4), nullable=True)
    artifact_uri: Mapped[str] = mapped_column(String(500))
    metadata_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class TemperaturePrediction(Base):
    __tablename__ = "temperature_predictions"
    __table_args__ = (
        UniqueConstraint(
            "station_id", "predicted_for_date", "model_version", name="uq_prediction_station_date_model"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    station_id: Mapped[str] = mapped_column(String(10), index=True)
    observation_date: Mapped[date] = mapped_column(Date, index=True)
    predicted_for_date: Mapped[date] = mapped_column(Date, index=True)
    observed_avg_temperature: Mapped[Decimal] = mapped_column(Numeric(6, 2))
    predicted_avg_temperature: Mapped[Decimal] = mapped_column(Numeric(6, 2))
    actual_avg_temperature: Mapped[Decimal | None] = mapped_column(Numeric(6, 2), nullable=True)
    model_version: Mapped[str] = mapped_column(
        String(100), ForeignKey("model_versions.model_version")
    )
    source: Mapped[str] = mapped_column(String(30), default="KMA_ASOS_DAILY")
    input_snapshot: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )


class PipelineRun(Base):
    __tablename__ = "pipeline_runs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    run_type: Mapped[str] = mapped_column(String(30))
    status: Mapped[str] = mapped_column(String(30))
    model_version: Mapped[str | None] = mapped_column(String(100), nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    message: Mapped[str | None] = mapped_column(Text, nullable=True)

