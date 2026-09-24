from __future__ import annotations

import __main__
import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import joblib
import pandas as pd
from catboost import CatBoostRegressor

from .features import DATE_COLUMN, build_v2_features, clean_raw_daily

KST = ZoneInfo("Asia/Seoul")


class MedianPreprocessor:
    """Compatibility class for the preprocessor serialized by the Colab notebook."""

    def __init__(self, feature_columns):
        self.feature_columns = list(feature_columns)

    def fit(self, frame):
        numeric = frame[self.feature_columns].apply(pd.to_numeric, errors="coerce")
        self.medians_ = numeric.median().fillna(0.0)
        self.missing_features_ = [column for column in self.feature_columns if numeric[column].isna().any()]
        self.output_columns_ = self.feature_columns + [f"{column}__missing" for column in self.missing_features_]
        return self

    def transform(self, frame):
        numeric = frame[self.feature_columns].apply(pd.to_numeric, errors="coerce")
        indicators = {
            f"{column}__missing": numeric[column].isna().astype("int8")
            for column in self.missing_features_
        }
        filled = numeric.fillna(self.medians_)
        if indicators:
            filled = pd.concat([filled, pd.DataFrame(indicators, index=filled.index)], axis=1)
        return filled[self.output_columns_].astype("float32")


@dataclass(frozen=True)
class PredictionResult:
    station_id: str
    observation_date: date
    predicted_for_date: date
    observed_avg_temperature: float
    predicted_avg_temperature: float
    model_version: str
    model_test_mae: float | None
    input_snapshot: dict


class TemperaturePredictor:
    def __init__(self, model_dir: str):
        self.model_dir = Path(model_dir)
        self.ready = False
        self.error: str | None = None
        try:
            self._load()
            self.ready = True
        except Exception as exc:  # surfaced by /health and prediction endpoint
            self.error = str(exc)

    def _load(self) -> None:
        metadata_path = self.model_dir / "model_metadata_v2.json"
        self.metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        self.feature_columns = json.loads(
            (self.model_dir / "feature_columns_v2.json").read_text(encoding="utf-8")
        )
        self.catboost = CatBoostRegressor()
        self.catboost.load_model(self.model_dir / "catboost_residual_v2.cbm")
        self.lightgbm = joblib.load(self.model_dir / "lightgbm_residual_v2.joblib")
        setattr(__main__, "MedianPreprocessor", MedianPreprocessor)
        self.preprocessor = joblib.load(self.model_dir / "preprocessor_v2.joblib")
        if list(self.preprocessor.feature_columns) != self.feature_columns:
            raise RuntimeError("모델과 전처리기의 변수 순서가 일치하지 않습니다.")

    @property
    def model_version(self) -> str | None:
        return self.metadata.get("model_version") if self.ready else None

    @property
    def test_mae(self) -> float | None:
        if not self.ready:
            return None
        ensemble = next(
            (row for row in self.metadata.get("scores", []) if row.get("model", "").startswith("V2 Ensemble")),
            None,
        )
        return float(ensemble["mae"]) if ensemble else None

    def predict(self, daily_items: list[dict]) -> PredictionResult:
        if not self.ready:
            raise RuntimeError(self.error or "AI 모델이 준비되지 않았습니다.")
        clean = clean_raw_daily(daily_items)
        features = build_v2_features(clean)
        if len(features) < 15:
            raise RuntimeError("lag 변수 생성을 위해 최소 15일 이상의 일자료가 필요합니다.")
        latest = features.iloc[[-1]]
        model_input = self.preprocessor.transform(latest)
        current = float(latest["평균기온(°C)"].iloc[0])
        cat_prediction = current + float(self.catboost.predict(model_input)[0])
        lgb_prediction = current + float(self.lightgbm.predict(model_input)[0])
        cat_weight = float(self.metadata["catboost_weight"])
        prediction = cat_weight * cat_prediction + (1.0 - cat_weight) * lgb_prediction
        observation_date = pd.Timestamp(latest[DATE_COLUMN].iloc[0]).date()
        snapshot = {
            "current_avg_temperature": current,
            "catboost_prediction": round(cat_prediction, 4),
            "lightgbm_prediction": round(lgb_prediction, 4),
            "catboost_weight": cat_weight,
            "source_row_count": len(daily_items),
        }
        return PredictionResult(
            station_id=str(self.metadata["station_id"]),
            observation_date=observation_date,
            predicted_for_date=observation_date + timedelta(days=2),
            observed_avg_temperature=current,
            predicted_avg_temperature=round(prediction, 2),
            model_version=str(self.metadata["model_version"]),
            model_test_mae=self.test_mae,
            input_snapshot=snapshot,
        )

