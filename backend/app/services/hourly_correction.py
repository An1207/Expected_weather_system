"""V3 combines frozen V2 with a fail-closed hourly residual layer."""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path

import joblib
import numpy as np
import pandas as pd

from .predictor import KST, PredictionResult, finite_number

BASE_FILES = ("catboost_residual_v2.cbm", "lightgbm_residual_v2.joblib",
              "preprocessor_v2.joblib", "feature_columns_v2.json", "model_metadata_v2.json")
FEATURE_SCHEMA = "hourly-residual-v1"
ISSUE_HOUR = 21
AVAILABILITY_LAG_HOURS = 1
MAX_CORRECTION = 6.0  # Predetermined safety limit, not selected using test labels.


def base_fingerprint(directory: Path) -> dict[str, str]:
    return {name: hashlib.sha256((directory / name).read_bytes()).hexdigest() for name in BASE_FILES}


def hourly_features(items: list[dict], issued_at: datetime, base_prediction: float) -> tuple[dict, dict]:
    issued_at = issued_at.replace(tzinfo=KST) if issued_at.tzinfo is None else issued_at.astimezone(KST)
    cutoff = issued_at - timedelta(hours=AVAILABILITY_LAG_HOURS)
    start = cutoff - timedelta(hours=71)
    index = pd.date_range(start, cutoff, freq="h")
    columns = ["ta", "hm", "pa", "ps", "ws", "td", "dc10Tca", "wd_degrees"]
    frame = pd.DataFrame(index=index, columns=columns, dtype=float)
    seen = set()
    for item in items:
        try:
            stamp = pd.Timestamp(item["tm"])
            stamp = stamp.tz_localize(KST) if stamp.tzinfo is None else stamp.tz_convert(KST)
        except (KeyError, ValueError, TypeError):
            continue
        if stamp not in frame.index or stamp in seen:
            continue
        seen.add(stamp)
        for name in columns:
            value = finite_number(item.get(name))
            if value is not None and value <= -99:
                value = None
            if value == -9 and name not in ("ta", "td"):
                value = None
            bounds = {"ta": (-60, 60), "td": (-80, 60), "hm": (0, 100), "pa": (800, 1100),
                      "ps": (800, 1100), "ws": (0, 100), "dc10Tca": (0, 10), "wd_degrees": (0, 360)}
            if value is not None and not bounds[name][0] <= value <= bounds[name][1]:
                value = None
            frame.loc[stamp, name] = np.nan if value is None else value
    valid = frame["ta"].dropna()
    age = (cutoff - valid.index[-1]).total_seconds() / 3600 if len(valid) else None
    coverage = len(valid) / 72
    usable = len(frame["ta"].tail(24).dropna()) >= 20 and coverage >= .8 and age is not None and age <= 1
    # No forward-fill/back-fill: missing hours stay missing, including pressure/humidity.
    features = {"base_prediction": base_prediction}
    for hours in (3, 6, 24, 72):
        window = frame.tail(hours)
        for name in ("ta", "hm", "pa", "ps", "ws", "td", "dc10Tca"):
            features[f"{name}_mean_{hours}h"] = finite_number(window[name].mean())
            features[f"{name}_coverage_{hours}h"] = window[name].notna().sum() / hours
        features[f"ta_min_{hours}h"] = finite_number(window["ta"].min())
        features[f"ta_max_{hours}h"] = finite_number(window["ta"].max())
        features[f"ta_std_{hours}h"] = finite_number(window["ta"].std(ddof=0))
        direction = np.deg2rad(window["wd_degrees"])
        features[f"wind_u_{hours}h"] = finite_number((-window["ws"] * np.sin(direction)).mean())
        features[f"wind_v_{hours}h"] = finite_number((-window["ws"] * np.cos(direction)).mean())
    for name in ("ta", "hm", "pa", "ps", "ws", "td", "dc10Tca"):
        # Exact cutoff observation and exact lag: never substitute a future value.
        features[f"{name}_latest"] = finite_number(frame[name].iloc[-1])
    for name in ("ta", "pa", "ps"):
        for lag in (3, 6, 12, 24):
            features[f"{name}_delta_{lag}h"] = finite_number(frame[name].iloc[-1] - frame[name].iloc[-1-lag])
    features["ta_latest_minus_base"] = finite_number(frame["ta"].iloc[-1] - base_prediction)
    features["ta_mean24_minus_base"] = finite_number(frame["ta"].tail(24).mean() - base_prediction)
    features["dewpoint_depression"] = finite_number(frame["ta"].iloc[-1] - frame["td"].iloc[-1])
    features["last_temperature_age_hours"] = age
    evidence = {"issued_at": issued_at.isoformat(), "cutoff_at": cutoff.isoformat(),
                "window_start": start.isoformat(), "observed_hours": len(valid),
                "coverage": coverage, "latest_age_hours": age, "usable": bool(usable),
                "features": features}
    return features, evidence


class HourlyCorrector:
    def __init__(self, directory: str, predictor):
        self.directory = Path(directory)
        self.predictor = predictor
        self.ready = False
        self.metadata = {}
        self.reason = "검증된 시간자료 보정 모델이 없습니다. 기존 일자료 예측을 유지합니다."
        try:
            path = self.directory / "metadata.json"
            if not path.is_file():
                return
            metadata = json.loads(path.read_text(encoding="utf-8"))
            if (metadata.get("schema") != FEATURE_SCHEMA or not metadata.get("promotion_approved")
                    or metadata.get("base_model_version") != predictor.model_version
                    or metadata.get("station_id") != str(predictor.metadata["station_id"])
                    or metadata.get("base_fingerprint") != base_fingerprint(predictor.model_dir)
                    or metadata.get("issue_hour") != ISSUE_HOUR
                    or metadata.get("availability_lag_hours") != AVAILABILITY_LAG_HOURS
                    or metadata.get("max_correction") != MAX_CORRECTION):
                self.reason = "보정 모델의 승인·기존 모델 일치 조건이 충족되지 않았습니다."
                return
            model_path = self.directory / "model.joblib"
            if hashlib.sha256(model_path.read_bytes()).hexdigest() != metadata.get("model_sha256"):
                self.reason = "시간자료 보정 모델의 무결성 검사에 실패했습니다."
                return
            columns = list(hourly_features([], datetime(2025, 1, 1, ISSUE_HOUR, tzinfo=KST), 0)[0])
            if metadata.get("feature_columns") != columns or not 0 < metadata.get("alpha", 0) <= 1:
                return
            self.model = joblib.load(model_path)
            self.metadata = metadata
            self.ready = True
            self.reason = "검증된 시간자료 보정 모델 준비 완료"
        except Exception:
            self.reason = "시간자료 보정 모델을 읽을 수 없습니다. 기존 예측을 유지합니다."

    @property
    def pipeline_model_version(self) -> str | None:
        if not self.ready:
            return None
        # Keep older correction artifacts compatible without rewriting DB history.
        return self.metadata.get("pipeline_model_version") or f"{self.predictor.model_version}+h:{self.metadata['model_version']}"

    def combine(self, result: PredictionResult, items: list[dict], now: datetime,
                unavailable_reason: str | None = None) -> PredictionResult:
        now = now.replace(tzinfo=KST) if now.tzinfo is None else now.astimezone(KST)
        issued = now.replace(hour=ISSUE_HOUR, minute=0, second=0, microsecond=0)
        base = float(result.input_snapshot["calculation"]["unrounded_prediction"])
        record = {"status": "fallback", "reason": self.reason, "base_prediction": base,
                  "correction": 0.0, "final_prediction": base, "correction_model_version": None,
                  "issue_hour": ISSUE_HOUR, "availability_lag_hours": AVAILABILITY_LAG_HOURS,
                  "issued_at": None, "cutoff_at": None, "coverage": None, "observed_hours": 0,
                  "feature_count": 0, "inputs": []}
        if not self.ready:
            pass
        elif now < issued:
            record["reason"] = "21시 이전: 기존 일자료 예측 유지 (보정은 20시까지의 관측 사용)"
        elif unavailable_reason:
            record["reason"] = unavailable_reason
        elif result.predicted_for_date != now.date() + timedelta(days=1):
            record["reason"] = "보정 대상 날짜 불일치"
        elif self.ready:
            features, evidence = hourly_features(items, issued, base)
            record.update(issued_at=evidence["issued_at"], cutoff_at=evidence["cutoff_at"],
                          coverage=evidence["coverage"], observed_hours=evidence["observed_hours"],
                          feature_count=len(features),
                          inputs=[{"name": name, "model_value": value, "treatment": "관측 마감 이전 시간자료 요약 · 결측은 NaN 유지"}
                                  for name, value in features.items()])
            if not evidence["usable"]:
                record["reason"] = "최근 72시간 기온 관측률·최신성 조건 미충족: 기존 예측 유지"
            else:
                try:
                    frame = pd.DataFrame([features], columns=self.metadata["feature_columns"]).astype(float)
                    raw = float(self.model.predict(frame)[0])
                    if not math.isfinite(raw):
                        raise ValueError("non-finite correction")
                    correction = float(np.clip(raw, -MAX_CORRECTION, MAX_CORRECTION) * self.metadata["alpha"])
                    record.update(status="applied", reason="시간자료 잔차 보정 적용", correction=correction,
                                  final_prediction=base + correction, raw_correction=raw,
                                  alpha=self.metadata["alpha"], correction_model_version=self.metadata["model_version"])
                except Exception:
                    record["reason"] = "시간자료 보정 추론 실패: 기존 예측 유지"
        snapshot = {**result.input_snapshot, "hourly_correction": record,
                    "calculation": {**result.input_snapshot["calculation"], "hourly_correction": record}}
        if record["status"] != "applied":
            return replace(result, input_snapshot=snapshot)
        return replace(result, input_snapshot=snapshot,
                       predicted_avg_temperature=round(record["final_prediction"], 2),
                       model_version=self.pipeline_model_version,
                       model_test_mae=float(self.metadata["test"]["corrected"]["mae"]))

    def public_audit(self) -> dict | None:
        path = self.directory / "audit.json"
        if not path.is_file():
            return None
        try:
            audit = json.loads(path.read_text(encoding="utf-8"))
            names = ("model_version", "pipeline_model_version", "base_model_version", "train_rows", "valid_rows", "test_rows",
                     "train_start", "train_end", "valid_start", "valid_end", "test_start", "test_end",
                     "validation_base_mae", "validation_corrected_mae", "test_base_mae", "test_corrected_mae",
                     "test_base_rmse", "test_corrected_rmse", "test_eligible_rows", "alpha",
                     "promotion_approved", "base_unchanged", "issue_hour", "availability_lag_hours")
            return {name: audit[name] for name in names if name in audit}
        except (ValueError, OSError):
            return None
