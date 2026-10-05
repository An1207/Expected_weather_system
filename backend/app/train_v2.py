"""Local, chronological V2 retraining with an explicit promotion gate.

Run inside the backend image. /workspace is a local project bind mount.
Only sanitized observations and model artifacts are saved; never configuration URLs.
"""
from __future__ import annotations

import asyncio
import json
from datetime import date, datetime, timedelta
from pathlib import Path

import joblib
import lightgbm as lgb
import numpy as np
import pandas as pd
from catboost import CatBoostRegressor
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score

from .config import get_settings
from .services.features import BASE_FEATURES, DATE_COLUMN, build_v2_features, clean_raw_daily
from .services.kma import KmaClient, KST
from .services.predictor import MedianPreprocessor, TemperaturePredictor


def metrics(actual, pred):
    return {"mae": float(mean_absolute_error(actual, pred)),
            "rmse": float(mean_squared_error(actual, pred) ** .5), "r2": float(r2_score(actual, pred))}


async def collect(root, settings):
    client = KmaClient(settings)
    directory = root / "data/local/daily"
    directory.mkdir(parents=True, exist_ok=True)
    today = datetime.now(KST).date()
    records = []
    for year in range(2000, today.year + 1):
        end = min(date(year, 12, 31), today - timedelta(days=1))
        path = directory / f"asos-{settings.kma_station_id}-{year}.json"
        if path.exists() and year < today.year:
            rows = json.loads(path.read_text(encoding="utf-8"))
        else:
            # Year-sized ASOS queries preserve the original full 44-field contract.
            rows = await client.daily(date(year, 1, 1), end)
            path.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
        records.extend(rows)
        print(f"collected year={year} rows={len(rows)}", flush=True)
    # Hub is independently collected for live use; do not inject its reduced
    # field contract into the historical training distribution.
    for label, fetch in (("hub-yesterday", lambda: client.hub.daily(today-timedelta(days=1))),
                         ("hub-today-hourly", lambda: client.hourly(today))):
        try:
            rows = await fetch()
            (root / "data/local" / f"{label}.json").write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
            print(f"{label} rows={len(rows)}", flush=True)
        except RuntimeError as exc:
            print(f"{label}: {exc}", flush=True)
    return records


def train(root, settings, rows):
    clean = clean_raw_daily(rows)
    features = build_v2_features(clean)
    # Use the real observed target (not a forward-filled label).
    targets = pd.DataFrame(rows)[["tm", "avgTa"]].drop_duplicates("tm")
    targets[DATE_COLUMN] = pd.to_datetime(targets["tm"]) - pd.Timedelta(days=2)
    targets["target"] = pd.to_numeric(targets["avgTa"], errors="coerce")
    data = features.merge(targets[[DATE_COLUMN, "target"]], on=DATE_COLUMN).dropna(subset=["target"])
    data = data.iloc[14:].copy()
    columns = [c for c in features.columns if c != DATE_COLUMN]
    # Fixed chronological boundaries and two-day purge prevent labels crossing
    # into validation/test windows. No shuffled split or test-tuned weights.
    train_df = data[data[DATE_COLUMN] <= "2022-12-29"]
    valid = data[(data[DATE_COLUMN] >= "2023-01-01") & (data[DATE_COLUMN] <= "2024-12-29")]
    test = data[data[DATE_COLUMN] >= "2025-01-01"]
    if min(len(train_df), len(valid), len(test)) < 180:
        raise RuntimeError("Not enough history for chronological validation; existing model unchanged")
    pre = MedianPreprocessor(columns).fit(train_df)
    xtrain, xvalid, xtest = (pre.transform(part) for part in (train_df, valid, test))
    ytrain = train_df["target"] - train_df["평균기온(°C)"]
    yvalid = valid["target"] - valid["평균기온(°C)"]
    cat = CatBoostRegressor(loss_function="MAE", iterations=2000, depth=7, learning_rate=.025,
                           l2_leaf_reg=7, random_seed=42, thread_count=4, allow_writing_files=False, verbose=200)
    cat.fit(xtrain, ytrain, eval_set=(xvalid, yvalid), early_stopping_rounds=150, use_best_model=True)
    light = lgb.LGBMRegressor(objective="regression_l1", n_estimators=2500, learning_rate=.025,
                             num_leaves=31, min_child_samples=30, colsample_bytree=.8,
                             reg_alpha=.1, reg_lambda=1, random_state=42, n_jobs=4, verbosity=-1)
    light.fit(xtrain, ytrain, eval_set=[(xvalid, yvalid)], eval_metric="mae", callbacks=[lgb.early_stopping(150, verbose=False)])
    cv = cat.predict(xvalid); lv = light.predict(xvalid)
    weights = np.linspace(0, 1, 101)
    weight = float(min(weights, key=lambda w: mean_absolute_error(yvalid, w*cv+(1-w)*lv)))
    current = test["평균기온(°C)"].to_numpy()
    prediction = current + weight*cat.predict(xtest)+(1-weight)*light.predict(xtest)
    incumbent = TemperaturePredictor(str(root / "artifacts/v2"))
    if not incumbent.ready:
        raise RuntimeError("Existing model cannot be loaded; promotion disabled")
    old_x = incumbent.preprocessor.transform(test)
    old_weight = float(incumbent.metadata["catboost_weight"])
    old_pred = current+old_weight*incumbent.catboost.predict(old_x)+(1-old_weight)*incumbent.lightgbm.predict(old_x)
    new_score = metrics(test["target"], prediction)
    old_score = metrics(test["target"], old_pred)
    baseline = metrics(test["target"], current)
    accepted = new_score["mae"] < min(old_score["mae"], baseline["mae"])
    version = "ensemble-asos-daily-v2-local-"+datetime.now(KST).strftime("%Y%m%d-%H%M%S")
    output = root / "artifacts/candidates" / version
    output.mkdir(parents=True, exist_ok=False)
    cat.save_model(output / "catboost_residual_v2.cbm")
    joblib.dump(light, output / "lightgbm_residual_v2.joblib")
    joblib.dump(pre, output / "preprocessor_v2.joblib")
    (output / "feature_columns_v2.json").write_text(json.dumps(columns, ensure_ascii=False, indent=2), encoding="utf-8")
    metadata = {"model_version": version, "station_id": settings.kma_station_id,
                "forecast_offset_days": 2, "base_feature_count": len(BASE_FEATURES),
                "engineered_feature_count": len(columns), "model_input_count": xtrain.shape[1],
                "catboost_weight": weight, "lightgbm_weight": 1-weight,
                "scores": [{"model": "V2 Ensemble local", **new_score}],
                "evaluation": {"incumbent": old_score, "persistence": baseline,
                               "train_rows": len(train_df), "valid_rows": len(valid), "test_rows": len(test),
                               "test_start": str(test[DATE_COLUMN].min().date()),
                               "test_end": str(test[DATE_COLUMN].max().date()), "promotion_approved": accepted},
                "config": {"start_date": str(clean[DATE_COLUMN].min().date()),
                           "end_date": str(clean[DATE_COLUMN].max().date()), "train_end": "2022-12-29", "valid_end": "2024-12-29"}}
    (output / "model_metadata_v2.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    pd.DataFrame({"observation_date":test[DATE_COLUMN],"actual":test["target"],"prediction":prediction}).to_csv(output/"test_predictions_v2.csv",index=False)
    print(json.dumps({"candidate": version, "new": new_score, "existing": old_score,
                      "persistence": baseline, "promotion_approved": accepted}, ensure_ascii=False), flush=True)
    # Deployment is a separate explicit operation after the candidate loads successfully.
    return output


if __name__ == "__main__":
    project = Path("/workspace")
    config = get_settings()
    observations = asyncio.run(collect(project, config))
    train(project, config, observations)
