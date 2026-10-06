import hashlib
import json
import tempfile
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import joblib
import numpy as np
import pandas as pd
from sklearn.dummy import DummyRegressor

from app.services.hourly_correction import (AVAILABILITY_LAG_HOURS, BASE_FILES, FEATURE_SCHEMA,
                                           ISSUE_HOUR, MAX_CORRECTION, HourlyCorrector,
                                           base_fingerprint, hourly_features)
from app.services.kma import KmaClient, KST
from app.services.predictor import PredictionResult
from app.config import Settings


class HourlyCorrectionTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 10, 6, 21, 30, tzinfo=KST)
        cutoff = self.now.replace(hour=20, minute=0)
        self.items = [{"tm": (cutoff-timedelta(hours=i)).strftime("%Y-%m-%d %H:%M"),
                       "ta": 20-i*.02, "hm": 60, "pa": 1000, "ps": 1010,
                       "ws": 2, "wd_degrees": 90, "td": 12, "dc10Tca": 3} for i in range(72)]
        self.result = PredictionResult("108", date(2026, 10, 5), date(2026, 10, 7), 20, 18,
                                       "frozen-test", 2.1, {"calculation": {"unrounded_prediction": 18.0}})

    def prepare_model(self, directory, alpha=1):
        base = directory / "base"
        output = directory / "hourly"
        base.mkdir(); output.mkdir()
        for name in BASE_FILES:
            (base / name).write_text("frozen", encoding="utf-8")
        predictor = SimpleNamespace(model_dir=base, model_version="frozen-test", metadata={"station_id": "108"})
        features = hourly_features(self.items, self.now.replace(minute=0), 18)[0]
        frame = pd.DataFrame([features]).astype(float)
        model = DummyRegressor(strategy="constant", constant=-.8).fit(frame, [-.8])
        joblib.dump(model, output / "model.joblib")
        metadata = {"schema": FEATURE_SCHEMA, "promotion_approved": True, "base_model_version": "frozen-test",
                    "station_id": "108", "base_fingerprint": base_fingerprint(base),
                    "feature_columns": list(features), "alpha": alpha, "model_version": "hr-unit",
                    "pipeline_model_version": "ensemble-asos-daily-hourly-v3-unit",
                    "issue_hour": ISSUE_HOUR, "availability_lag_hours": AVAILABILITY_LAG_HOURS,
                    "max_correction": MAX_CORRECTION,
                    "model_sha256": hashlib.sha256((output / "model.joblib").read_bytes()).hexdigest(),
                    "test": {"corrected": {"mae": 1.8}}}
        (output / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
        return HourlyCorrector(str(output), predictor), predictor

    def test_future_observations_are_excluded(self):
        issued = self.now.replace(minute=0)
        features, evidence = hourly_features(self.items, issued, 18)
        future = {**self.items[0], "tm": "2026-10-06 21:00", "ta": 59}
        later, _ = hourly_features(self.items+[future], issued, 18)
        self.assertEqual(features, later)
        self.assertEqual(evidence["cutoff_at"], "2026-10-06T20:00:00+09:00")
        self.assertTrue(evidence["usable"])

    def test_duplicate_hours_do_not_inflate_coverage(self):
        _, evidence = hourly_features(self.items[:2]*40, self.now.replace(minute=0), 18)
        self.assertEqual(evidence["observed_hours"], 2)
        self.assertFalse(evidence["usable"])

    def test_pressure_and_temperature_changes_use_exact_lags(self):
        features, _ = hourly_features(self.items, self.now.replace(minute=0), 18)
        self.assertAlmostEqual(features["ta_delta_6h"], .12)
        self.assertAlmostEqual(features["wind_u_24h"], -2)
        self.assertAlmostEqual(features["wind_v_24h"], 0, places=8)

    def test_out_of_range_is_missing_not_imputed(self):
        rows = [dict(row) for row in self.items]
        rows[0]["ta"] = 999
        rows[0]["hm"] = -9
        features, evidence = hourly_features(rows, self.now.replace(minute=0), 18)
        self.assertIsNone(features["ta_latest"])
        self.assertIsNone(features["hm_latest"])
        self.assertEqual(evidence["observed_hours"], 71)

    def test_additive_correction_without_mutating_base(self):
        with tempfile.TemporaryDirectory() as temp:
            corrector, predictor = self.prepare_model(Path(temp), alpha=.5)
            before = base_fingerprint(predictor.model_dir)
            result = corrector.combine(self.result, self.items, self.now)
            self.assertTrue(corrector.ready)
            self.assertEqual(result.predicted_avg_temperature, 17.6)
            self.assertEqual(result.model_version, "ensemble-asos-daily-hourly-v3-unit")
            self.assertEqual(result.input_snapshot["hourly_correction"]["correction_model_version"], "hr-unit")
            self.assertEqual(result.input_snapshot["hourly_correction"]["status"], "applied")
            self.assertEqual(self.result.predicted_avg_temperature, 18)
            self.assertNotIn("hourly_correction", self.result.input_snapshot)
            self.assertEqual(before, base_fingerprint(predictor.model_dir))

    def test_before_21_missing_stale_and_fetch_failure_fall_back(self):
        with tempfile.TemporaryDirectory() as temp:
            corrector, _ = self.prepare_model(Path(temp))
            for rows, now, reason in ((self.items, self.now.replace(hour=20), None),
                                     ([], self.now, None), (self.items[3:], self.now, None),
                                     (self.items, self.now, "시간자료 수집 실패")):
                result = corrector.combine(self.result, rows, now, reason)
                self.assertEqual(result.predicted_avg_temperature, self.result.predicted_avg_temperature)
                self.assertEqual(result.model_version, self.result.model_version)
                self.assertEqual(result.input_snapshot["hourly_correction"]["status"], "fallback")

    def test_legacy_artifact_keeps_legacy_combined_version(self):
        with tempfile.TemporaryDirectory() as temp:
            corrector, predictor = self.prepare_model(Path(temp))
            metadata = dict(corrector.metadata)
            metadata.pop("pipeline_model_version")
            (corrector.directory / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
            legacy = HourlyCorrector(str(corrector.directory), predictor)
            self.assertTrue(legacy.ready)
            self.assertEqual(legacy.combine(self.result, self.items, self.now).model_version, "frozen-test+h:hr-unit")

    def test_changed_frozen_model_refuses_correction(self):
        with tempfile.TemporaryDirectory() as temp:
            corrector, predictor = self.prepare_model(Path(temp))
            (predictor.model_dir / BASE_FILES[0]).write_text("changed", encoding="utf-8")
            reloaded = HourlyCorrector(str(corrector.directory), predictor)
            self.assertFalse(reloaded.ready)

    def test_changed_correction_artifact_refuses_load(self):
        with tempfile.TemporaryDirectory() as temp:
            corrector, predictor = self.prepare_model(Path(temp))
            with (corrector.directory / "model.joblib").open("ab") as target:
                target.write(b"tampered")
            self.assertFalse(HourlyCorrector(str(corrector.directory), predictor).ready)

    def test_clip_before_validation_weight(self):
        with tempfile.TemporaryDirectory() as temp:
            corrector, _ = self.prepare_model(Path(temp), alpha=.5)
            corrector.model = SimpleNamespace(predict=lambda _: np.array([100]))
            result = corrector.combine(self.result, self.items, self.now)
            self.assertEqual(result.input_snapshot["hourly_correction"]["correction"], 3)


class HourlyCollectionTests(unittest.IsolatedAsyncioTestCase):
    async def test_period_denied_uses_point_api_and_excludes_future(self):
        client = KmaClient(Settings(kma_apihub_hourly_file_url="configured"))
        start = datetime(2026, 10, 6, 19, tzinfo=KST)
        end = start+timedelta(hours=1)
        rows = [{"tm": "2026-10-06 19:00", "ta": 18}, {"tm": "2026-10-06 20:00", "ta": 17},
                {"tm": "2026-10-06 21:00", "ta": 16}]
        with patch.object(client.hub, "hourly_range", AsyncMock(side_effect=RuntimeError("API Hub HTTP 오류: 403"))) as period, patch.object(client, "hourly", AsyncMock(return_value=rows)):
            result = await client.correction_history(start, end)
            self.assertEqual(len(result), 2)
            self.assertTrue(client._hourly_period_denied)
            await client.correction_history(start, end)
            period.assert_awaited_once()

    async def test_asos_historical_wind_is_already_degrees(self):
        client = KmaClient(Settings())
        with patch.object(client, "_request", AsyncMock(return_value=[{"tm": "2023-01-01 01:00", "ta": "-9", "wd": "270"}])):
            row = (await client.historical_hourly(date(2023,1,1), date(2023,1,1)))[0]
        self.assertEqual(row["wd_degrees"], 270)


if __name__ == "__main__":
    unittest.main()
