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

from .features import API_TO_KOREAN, BASE_FEATURES, ZERO_FILL_FEATURES, DATE_COLUMN, build_v2_features, clean_raw_daily


def finite_number(value):
    try:
        number = float(value)
        return number if pd.notna(number) and abs(number) != float("inf") else None
    except (TypeError, ValueError):
        return None

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
        raw_latest = next(row for row in reversed(daily_items) if pd.Timestamp(row["tm"]).date() == observation_date)
        base_inputs = []
        for source, name in API_TO_KOREAN.items():
            if source == "tm":
                continue
            raw_value = finite_number(raw_latest.get(source))
            clean_value = finite_number(latest[name].iloc[0])
            treatment = "관측값 사용"
            if raw_value is None:
                treatment = "결측 → 0 대체" if name in ZERO_FILL_FEATURES else ("결측 → 과거값 ffill" if clean_value is not None else "결측 → 학습 구간 중앙값")
            base_inputs.append({"name": name, "source_key": source, "raw_value": raw_value,
                                "model_value": finite_number(model_input[name].iloc[0]), "treatment": treatment})
        derived_names = ["연중일_sin", "연중일_cos", "일교차(°C)", "기온_이슬점차(°C)",
                         "평균기온(°C)__lag1", "평균기온(°C)__lag7", "평균기온(°C)__mean3",
                         "평균기온(°C)__mean7", "평균기온(°C)__mean14", "평균기온(°C)__delta1"]
        calculation = {
            "history_start": str(clean[DATE_COLUMN].min().date()), "history_end": str(observation_date),
            "source_row_count": len(daily_items), "current_avg_temperature": current,
            "catboost_residual": cat_prediction-current, "lightgbm_residual": lgb_prediction-current,
            "catboost_prediction": cat_prediction, "lightgbm_prediction": lgb_prediction,
            "catboost_weight": cat_weight, "lightgbm_weight": 1-cat_weight,
            "unrounded_prediction": float(prediction), "base_inputs": base_inputs,
            "derived_inputs": [{"name": name, "model_value": finite_number(model_input[name].iloc[0]),
                                "treatment": "현재 및 과거 일자료에서 계산"} for name in derived_names if name in model_input],
        }
        snapshot = {
            "current_avg_temperature": current,
            "catboost_prediction": cat_prediction,
            "lightgbm_prediction": lgb_prediction,
            "catboost_weight": cat_weight,
            "source_row_count": len(daily_items),
            "latest_missing_base_fields": [item["source_key"] for item in base_inputs if item["raw_value"] is None],
            "calculation": calculation,
        }
        return PredictionResult(
            station_id=str(self.metadata["station_id"]),
            observation_date=observation_date,
            predicted_for_date=observation_date + timedelta(days=int(self.metadata.get("forecast_offset_days", 2))),
            observed_avg_temperature=current,
            predicted_avg_temperature=round(prediction, 2),
            model_version=str(self.metadata["model_version"]),
            model_test_mae=self.test_mae,
            input_snapshot=snapshot,
        )

