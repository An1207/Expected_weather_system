"""Chronological residual experiment against a SHA-256-bound frozen daily model.

python -m app.train_hourly --root /workspace [--promote]
Only the separate hourly artifact may be promoted. No daily fit/save is called.
"""
from __future__ import annotations

import argparse
import asyncio
import calendar
import hashlib
import json
import shutil
from datetime import date, datetime, timedelta
from pathlib import Path

import joblib
import lightgbm as lgb
import numpy as np
import pandas as pd

from .config import get_settings
from .services.hourly_correction import (AVAILABILITY_LAG_HOURS, FEATURE_SCHEMA, ISSUE_HOUR,
                                         MAX_CORRECTION, base_fingerprint, hourly_features)
from .services.kma import KmaClient, KST
from .services.predictor import TemperaturePredictor, finite_number
from .train_v2 import metrics


def save_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    temp.replace(path)


async def collect(root, settings, start):
    client = KmaClient(settings)
    end = datetime.now(KST).date() - timedelta(days=1)
    daily = []
    for year in range(start.year - 1, end.year + 1):
        path = root / "data/local/daily" / f"asos-{settings.kma_station_id}-{year}.json"
        if path.exists() and year < end.year:
            items = json.loads(path.read_text(encoding="utf-8"))
        else:
            items = await client.daily(date(year, 1, 1), min(date(year, 12, 31), end))
            save_json(path, items)
        daily.extend(items)
    if not settings.kma_apihub_hourly_file_url or not settings.kma_apihub_daily_file_url:
        raise RuntimeError("시간자료·일자료 API Hub 주소를 설정해야 합니다. 기존 모델은 변경되지 않습니다.")
    # The prior day's Hub overlay matches the current serving path, including missing fields.
    hourly, hub_daily = [], []
    hourly_period_denied, daily_period_denied = False, False
    month = (start - timedelta(days=3)).replace(day=1)
    while month <= end:
        month_end = min(date(month.year, month.month, calendar.monthrange(month.year, month.month)[1]), end)
        async def fetch_hourly():
            nonlocal hourly_period_denied
            if not hourly_period_denied:
                try:
                    return await client.hub.hourly_range(datetime.combine(month, datetime.min.time()), datetime.combine(month_end, datetime.max.time()).replace(minute=0, second=0, microsecond=0))
                except RuntimeError as exc:
                    if "HTTP 오류: 403" not in str(exc):
                        raise
                    hourly_period_denied = True
                    print("Hub hourly period denied: using registered ASOS hourly history API", flush=True)
            return await client.historical_hourly(month, month_end)

        async def fetch_daily():
            nonlocal daily_period_denied
            if not daily_period_denied:
                try:
                    return await client.hub.daily_range(month, month_end)
                except RuntimeError as exc:
                    if "HTTP 오류: 403" not in str(exc):
                        raise
                    daily_period_denied = True
                    print("Hub daily period denied: using registered point daily API", flush=True)
            async def point(day):
                path = root / "data/local/hourly-correction/points" / f"hub-{settings.kma_station_id}-{day}.json"
                if path.is_file() and day < end:
                    return json.loads(path.read_text(encoding="utf-8"))
                for attempt in range(3):
                    try:
                        rows = await client.hub.daily(day)
                        save_json(path, rows)
                        return rows
                    except RuntimeError as exc:
                        if attempt == 2 or "HTTP 오류: 403" in str(exc):
                            print(f"Hub daily point failed date={day} reason={exc}", flush=True)
                            raise RuntimeError(f"단일 일자료 조회 실패 날짜: {day}") from None
                        await asyncio.sleep(2*(attempt+1))
            days = [month+timedelta(days=i) for i in range((month_end-month).days+1)]
            # Wait for all siblings before retrying: no orphaned requests pile up.
            batches = await asyncio.gather(*(point(day) for day in days), return_exceptions=True)
            failed = [str(day) for day, batch in zip(days,batches) if isinstance(batch, Exception)]
            if failed:
                raise RuntimeError("단일 일자료 미수집 날짜: " + ", ".join(failed))
            return [item for batch in batches for item in batch]

        for label, records, fetch in (("hourly", hourly, fetch_hourly), ("hub-daily", hub_daily, fetch_daily)):
            path = root / "data/local/hourly-correction" / f"{label}-{settings.kma_station_id}-{month:%Y-%m}.json"
            if path.exists() and month_end < end:
                items = json.loads(path.read_text(encoding="utf-8"))
            else:
                for attempt in range(3):
                    try:
                        items = await fetch()
                        break
                    except RuntimeError:
                        if attempt == 2:
                            raise RuntimeError(f"{label} {month:%Y-%m} 수집 실패. 캐시는 유지하며 기존 모델은 변경하지 않습니다.") from None
                        await asyncio.sleep(2 * (attempt + 1))
                save_json(path, items)
            records.extend(items)
            print(f"collected {label} month={month:%Y-%m} rows={len(items)}", flush=True)
        month = (month.replace(day=28) + timedelta(days=4)).replace(day=1)
    # Bulk and single-day Hub contracts must produce the same frozen base inputs.
    probe_date = start
    direct = await client.hub.daily(probe_date)
    bulk = next((item for item in hub_daily if item["tm"] == str(probe_date)), None)
    if not direct or bulk != direct[0]:
        raise RuntimeError("API Hub 기간/단일 일자료 정합성 검사 실패. 실험 중단, 기존 모델 유지.")
    # ASOS uses degrees, Hub uses 36-direction codes. Check numerical agreement
    # at the same historical hour after source-specific unit normalization.
    probe_stamp = datetime.combine(probe_date, datetime.min.time()).replace(hour=20)
    point_rows = await client.hub.request(settings.kma_apihub_hourly_file_url, {"tm": probe_stamp.strftime("%Y%m%d%H%M")})
    old_hour = next((item for item in hourly if item["tm"] == probe_stamp.strftime("%Y-%m-%d %H:%M")), None)
    if not point_rows or old_hour is None:
        raise RuntimeError("과거/실시간 시간자료 정합성 확인 자료 부족")
    point = client.hub.normalize_hourly(point_rows[0])
    for name in ("ta", "hm", "pa", "ps", "ws", "td", "dc10Tca", "wd_degrees"):
        left, right = finite_number(old_hour.get(name)), finite_number(point.get(name))
        if left is not None and right is not None and abs(left-right) > .11:
            raise RuntimeError(f"과거/실시간 시간자료 단위·값 불일치: {name}")
    return daily, hourly, hub_daily


def build_dataset(daily, hourly, hub_daily, predictor, start):
    by_day = {str(row["tm"]): row for row in daily}
    hub = {str(row["tm"]): row for row in hub_daily}
    by_hour_day = {}
    for item in hourly:
        by_hour_day.setdefault(str(item["tm"])[:10], []).append(item)
    end = date.fromisoformat(max(by_day)) - timedelta(days=1)
    records, base_rows = [], []
    issue = start
    while issue <= end:
        observation = issue - timedelta(days=1)
        target_date = issue + timedelta(days=1)
        actual = finite_number(by_day.get(str(target_date), {}).get("avgTa"))
        if actual is None or str(observation) not in hub:
            issue += timedelta(days=1)
            continue
        # Same 46-day window, same existing preprocessor, same latest Hub merge as serving.
        history = [dict(by_day[str(observation-timedelta(days=lag))]) for lag in range(45, -1, -1)
                   if str(observation-timedelta(days=lag)) in by_day]
        if len(history) < 46:
            issue += timedelta(days=1)
            continue
        history[-1].update({key: value for key, value in hub[str(observation)].items() if value is not None})
        base_rows.append((issue, target_date, actual, history))
        issue += timedelta(days=1)
    for idx, (issue, target_date, actual, history) in enumerate(base_rows):
        result = predictor.predict(history)
        if result.predicted_for_date != target_date:
            raise RuntimeError("기존 모델과 보정 목표 날짜가 일치하지 않습니다.")
        base = result.input_snapshot["calculation"]["unrounded_prediction"]
        items = [row for lag in range(3, -1, -1) for row in by_hour_day.get(str(issue-timedelta(days=lag)), [])]
        features, evidence = hourly_features(items, datetime.combine(issue, datetime.min.time(), tzinfo=KST).replace(hour=ISSUE_HOUR), base)
        records.append({"issue_date": str(issue), "target_date": str(target_date), "actual": actual,
                        "base": base, "eligible": evidence["usable"], **features})
        if (idx + 1) % 100 == 0:
            print(f"frozen inference rows={idx+1}/{len(base_rows)}", flush=True)
    return pd.DataFrame(records)


def train(root, settings, data, predictor, fingerprints, promote=False):
    columns = list(hourly_features([], datetime(2025, 1, 1, ISSUE_HOUR, tzinfo=KST), 0)[0])
    # Partition by target dates: no training label extends into validation/test.
    training = data[(data.target_date <= "2024-12-31") & data.eligible]
    validation = data[(data.issue_date >= "2025-01-01") & (data.target_date <= "2025-12-31")]
    test = data[data.issue_date >= "2026-01-01"]
    valid_eligible = validation[validation.eligible]
    if len(training) < 365 or len(valid_eligible) < 180 or len(test) < 180:
        raise RuntimeError("독립 학습·검증·평가 자료 부족. 기존 모델 유지.")
    model = lgb.LGBMRegressor(objective="regression_l1", n_estimators=1000, learning_rate=.03,
                             num_leaves=7, max_depth=3, min_child_samples=35,
                             reg_alpha=.5, reg_lambda=5, random_state=42, n_jobs=4, verbosity=-1)
    model.fit(training[columns].astype(float), training.actual-training.base,
              eval_set=[(valid_eligible[columns].astype(float), valid_eligible.actual-valid_eligible.base)],
              eval_metric="mae", callbacks=[lgb.early_stopping(60, verbose=False)])

    def corrections(part):
        raw = np.clip(model.predict(part[columns].astype(float)), -MAX_CORRECTION, MAX_CORRECTION)
        return np.where(part.eligible, raw, 0.0)

    vc = corrections(validation)
    # Alpha, early stopping, clipping and all feature choices are independent of test labels.
    alpha = float(min(np.linspace(0, 1, 11), key=lambda a: np.abs(validation.actual-(validation.base+a*vc)).mean()))
    valid_base = metrics(validation.actual, validation.base)
    valid_corrected = metrics(validation.actual, validation.base+alpha*vc)
    tc = corrections(test)
    base_score = metrics(test.actual, test.base)
    corrected_score = metrics(test.actual, test.base+alpha*tc)
    unchanged = fingerprints == base_fingerprint(predictor.model_dir)
    approved = bool(unchanged and alpha > 0 and valid_corrected["mae"] < valid_base["mae"] * .95
                    and corrected_score["mae"] < base_score["mae"] * .95
                    and corrected_score["rmse"] <= base_score["rmse"])
    timestamp = datetime.now(KST).strftime("%Y%m%d-%H%M%S")
    version = "hr1-" + timestamp
    pipeline_version = "ensemble-asos-daily-hourly-v3-" + timestamp
    output = root / "artifacts/candidates" / version
    output.mkdir(parents=True, exist_ok=False)
    joblib.dump(model, output / "model.joblib")
    metadata = {"schema": FEATURE_SCHEMA, "model_version": version, "pipeline_model_version": pipeline_version,
                "station_id": settings.kma_station_id,
                "base_model_version": predictor.model_version, "base_fingerprint": fingerprints,
                "feature_columns": columns, "issue_hour": ISSUE_HOUR,
                "availability_lag_hours": AVAILABILITY_LAG_HOURS, "max_correction": MAX_CORRECTION,
                "alpha": alpha, "promotion_approved": approved,
                "model_sha256": hashlib.sha256((output / "model.joblib").read_bytes()).hexdigest(),
                "validation": {"base": valid_base, "corrected": valid_corrected},
                "test": {"base": base_score, "corrected": corrected_score},
                "limitations": ["Historical publication timestamps are unavailable: 1h lag is a simulation assumption.",
                                "2026 historical base-model test data was previously inspected; prospective monitoring is required."]}
    audit = {"model_version": version, "pipeline_model_version": pipeline_version,
             "base_model_version": predictor.model_version,
             "train_rows": len(training), "valid_rows": len(validation), "test_rows": len(test),
             "test_eligible_rows": int(test.eligible.sum()),
             "train_start": str(training.issue_date.min()), "train_end": str(training.issue_date.max()),
             "valid_start": str(validation.issue_date.min()), "valid_end": str(validation.issue_date.max()),
             "test_start": str(test.issue_date.min()), "test_end": str(test.issue_date.max()),
             "validation_base_mae": valid_base["mae"], "validation_corrected_mae": valid_corrected["mae"],
             "test_base_mae": base_score["mae"], "test_corrected_mae": corrected_score["mae"],
             "test_base_rmse": base_score["rmse"], "test_corrected_rmse": corrected_score["rmse"],
             "alpha": alpha, "promotion_approved": approved, "base_unchanged": unchanged,
             "issue_hour": ISSUE_HOUR, "availability_lag_hours": AVAILABILITY_LAG_HOURS}
    save_json(output / "metadata.json", metadata)
    save_json(output / "audit.json", audit)
    evaluation = test[["issue_date", "target_date", "actual", "base", "eligible"]].copy()
    evaluation["correction"] = alpha*tc
    evaluation["corrected"] = evaluation.base+alpha*tc
    evaluation.to_csv(output / "test_predictions.csv", index=False)
    # Reproducible local dataset, never committed and never contains credentials.
    data.to_csv(output / "dataset.csv", index=False)
    deployment = root / "artifacts/hourly"
    deployment.mkdir(parents=True, exist_ok=True)
    save_json(deployment / "audit.json", audit)
    if promote and approved:
        from .services.hourly_correction import HourlyCorrector
        probe = HourlyCorrector(str(output), predictor)
        if not probe.ready:
            raise RuntimeError("보정 후보 로드 검사 실패. 배포하지 않습니다.")
        if fingerprints != base_fingerprint(predictor.model_dir):
            raise RuntimeError("기존 모델 변경 감지. 배포하지 않습니다.")
        for name in ("model.joblib", "metadata.json"):
            destination = deployment / name
            if destination.exists():
                backup = root / "artifacts/backups/hourly" / version
                backup.mkdir(parents=True, exist_ok=True)
                shutil.copy2(destination, backup / name)
            shutil.copy2(output / name, destination)
    print(json.dumps({**audit, "deployed": bool(promote and approved)}, ensure_ascii=False), flush=True)
    return output


async def run(root, promote):
    settings = get_settings()
    predictor = TemperaturePredictor(str(root / "artifacts/v2"))
    if not predictor.ready or predictor.metadata["base_feature_count"] != 44:
        raise RuntimeError("동결할 기존 44변수 모델을 확인할 수 없습니다.")
    if str(predictor.metadata["station_id"]) != settings.kma_station_id:
        raise RuntimeError("기존 모델과 수집 관측소 불일치")
    fingerprints = base_fingerprint(predictor.model_dir)
    # Exclude dates touched by both base fitting and base hyperparameter selection.
    config = predictor.metadata["config"]
    used_until = max(date.fromisoformat(config["train_end"]), date.fromisoformat(config["valid_end"]))
    start = used_until + timedelta(days=int(predictor.metadata["forecast_offset_days"]) + 1)
    daily, hourly, hub = await collect(root, settings, start)
    data = build_dataset(daily, hourly, hub, predictor, start)
    train(root, settings, data, predictor, fingerprints, promote)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path("/workspace"))
    parser.add_argument("--promote", action="store_true", help="Promote only if predetermined improvement gates pass")
    args = parser.parse_args()
    try:
        asyncio.run(run(args.root, args.promote))
    except RuntimeError as exc:
        raise SystemExit(str(exc)) from None
