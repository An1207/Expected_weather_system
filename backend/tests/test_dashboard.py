"""Contract checks using real V2 inference and mocked KMA responses (no API key needed)."""
import unittest
from dataclasses import replace
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient
from sqlalchemy import select, func
from sqlalchemy.orm import Session

from app import main
from app.services.features import API_TO_KOREAN


class DashboardTests(unittest.TestCase):
    def setUp(self):
        daily_only = patch.object(main.hourly_corrector, "ready", False)
        daily_only.start()
        self.addCleanup(daily_only.stop)
        self.today = datetime.now(main.KST).date()
        self.daily = []
        for offset in range(45, 0, -1):
            row = {key: "1.0" for key in API_TO_KOREAN}
            row.update(tm=str(self.today - timedelta(days=offset)), avgTa="20.0", minTa="15", maxTa="25", avgRhm="60", avgPa="1000", avgPs="1010")
            self.daily.append(row)
        self.hourly = [dict(tm=f"{self.today} 02:00", ta="21", hm="62"), dict(tm=f"{self.today} 01:00", ta="19", hm="60")]

    def test_three_days_real_model_and_sorted_hourly(self):
        self.assertTrue(main.predictor.ready, main.predictor.error)
        with patch.object(main.kma, "daily", AsyncMock(return_value=self.daily[-1:])), patch.object(main.kma, "hourly", AsyncMock(return_value=self.hourly)), patch.object(main.kma, "recent_daily_history", AsyncMock(return_value=self.daily)), patch.object(main, "persist_prediction", return_value=SimpleNamespace(updated_at=datetime.utcnow())):
            with TestClient(main.app) as client:
                payload = client.get("/api/v1/dashboard").json()
                self.assertEqual(payload["errors"], {})
                self.assertEqual(payload["yesterday"]["temperature"], 20)
                self.assertEqual(payload["today"]["temperature"], 21)
                self.assertLess(payload["today"]["hourly"][0]["observed_at"], payload["today"]["hourly"][1]["observed_at"])
                self.assertEqual(payload["tomorrow"]["predicted_for_date"], str(self.today + timedelta(days=1)))
                self.assertIsInstance(payload["tomorrow"]["predicted_avg_temperature"], float)
                calculation = payload["tomorrow"]["calculation"]
                self.assertEqual(len(calculation["base_inputs"]), 44)
                combined = calculation["catboost_weight"] * calculation["catboost_prediction"] + calculation["lightgbm_weight"] * calculation["lightgbm_prediction"]
                self.assertAlmostEqual(combined, calculation["unrounded_prediction"], places=8)
                self.assertEqual(round(combined, 2), payload["tomorrow"]["predicted_avg_temperature"])
                self.assertEqual(payload["model_evidence"]["model_input_count"], len(main.predictor.preprocessor.output_columns_))

    def test_hourly_failure_preserves_yesterday(self):
        with patch.object(main.kma, "daily", AsyncMock(return_value=self.daily[-1:])), patch.object(main.kma, "hourly", AsyncMock(side_effect=RuntimeError("hourly unavailable"))), patch.object(main.kma, "recent_daily_history", AsyncMock(side_effect=RuntimeError("daily unavailable"))):
            with TestClient(main.app) as client:
                response = client.get("/api/v1/dashboard")
                self.assertEqual(response.status_code, 200)
                payload = response.json()
                self.assertIsNotNone(payload["yesterday"])
                self.assertIsNone(payload["today"])
                self.assertEqual(payload["errors"]["today"], "hourly unavailable")
                self.assertIsNotNone(payload["model_evidence"])

    def test_input_evidence_matches_model_values_and_ffill(self):
        self.daily[-1]["avgCm5Te"] = None
        result = main.predictor.predict(self.daily)
        inputs = result.input_snapshot["calculation"]["base_inputs"]
        missing = next(row for row in inputs if row["source_key"] == "avgCm5Te")
        self.assertIsNone(missing["raw_value"])
        self.assertEqual(missing["model_value"], 1.0)
        self.assertEqual(missing["treatment"], "결측 → 과거값 ffill")

    def test_model_evidence_does_not_expose_config_urls_or_keys(self):
        metadata = {**main.predictor.metadata, "config": {**main.predictor.metadata.get("config", {}), "api_url": "https://example.com/?authKey=PRIVATE_TEST_SECRET", "api_key": "PRIVATE_TEST_SECRET"}}
        with patch.object(main.predictor, "metadata", metadata):
            evidence_json = main.model_evidence().model_dump_json()
        self.assertNotIn("PRIVATE_TEST_SECRET", evidence_json)
        self.assertNotIn("api_url", evidence_json)

    def test_stale_daily_does_not_get_labelled_tomorrow(self):
        with patch.object(main.kma, "recent_daily_history", AsyncMock(return_value=self.daily[:-1])), patch.object(main, "persist_prediction") as save:
            with TestClient(main.app) as client:
                response = client.post("/api/v1/predictions/run")
                self.assertEqual(response.status_code, 503)
                save.assert_not_called()

    def test_mysql_upsert_without_leaving_test_data(self):
        result = main.predictor.predict(self.daily)
        with main.engine.connect() as connection:
            transaction = connection.begin()
            try:
                with Session(bind=connection) as db:
                    first = main.persist_prediction(db, result)
                    first_id = first.id
                    second = main.persist_prediction(db, result)
                    self.assertEqual(first_id, second.id)
                    count = db.scalar(select(func.count()).select_from(main.TemperaturePrediction).where(
                        main.TemperaturePrediction.station_id == result.station_id,
                        main.TemperaturePrediction.predicted_for_date == result.predicted_for_date,
                        main.TemperaturePrediction.model_version == result.model_version,
                    ))
                    self.assertEqual(count, 1)
            finally:
                transaction.rollback()

    def test_corrected_mysql_version_preserves_base_record(self):
        base = main.predictor.predict(self.daily)
        corrected = replace(base, model_version="ensemble-asos-daily-hourly-v3-unit-db",
                            predicted_avg_temperature=round(base.predicted_avg_temperature-.5,2),
                            input_snapshot={**base.input_snapshot, "hourly_correction": {
                                "status": "applied", "base_prediction": base.predicted_avg_temperature,
                                "correction": -.5, "final_prediction": base.predicted_avg_temperature-.5}})
        with main.engine.connect() as connection:
            transaction = connection.begin()
            try:
                with Session(bind=connection) as db:
                    basic = main.persist_prediction(db, base)
                    extra = main.persist_prediction(db, corrected)
                    self.assertNotEqual(basic.id, extra.id)
                    self.assertEqual(extra.input_snapshot["hourly_correction"]["correction"], -.5)
                    self.assertEqual(extra.source, "KMA_DAILY_HOURLY_CORRECTED")
                    self.assertEqual(basic.model_version, base.model_version)
                    self.assertEqual(extra.model_version, "ensemble-asos-daily-hourly-v3-unit-db")
                    self.assertEqual(main.persist_prediction(db, corrected).id, extra.id)
            finally:
                transaction.rollback()

    def test_v3_pipeline_is_separate_from_base_model_in_public_contract(self):
        version = "ensemble-asos-daily-hourly-v3-unit"
        metadata = {**main.hourly_corrector.metadata, "pipeline_model_version": version}
        with patch.object(main.hourly_corrector, "ready", True), patch.object(main.hourly_corrector, "metadata", metadata):
            with TestClient(main.app) as client:
                health = client.get("/health").json()
            evidence = main.model_evidence().model_dump()
        for payload in (health, evidence):
            self.assertEqual(payload["model_version"], main.predictor.model_version)
            self.assertEqual(payload["pipeline_model_version"], version)

    def test_correction_fetch_failure_keeps_prediction_available(self):
        fixed_now = datetime.combine(self.today, datetime.min.time(), tzinfo=main.KST).replace(hour=21)
        with patch.object(main.hourly_corrector,"ready",True), patch("app.main.datetime", wraps=datetime) as clock, patch.object(main.kma,"recent_daily_history",AsyncMock(return_value=self.daily)), patch.object(main.kma,"correction_history",AsyncMock(side_effect=RuntimeError("upstream unavailable"))), patch.object(main,"persist_prediction",return_value=SimpleNamespace(updated_at=datetime.utcnow())):
            clock.now.return_value = fixed_now
            with TestClient(main.app) as client:
                response = client.post("/api/v1/predictions/run")
                self.assertEqual(response.status_code,200)
                correction = response.json()["calculation"]["hourly_correction"]
                self.assertEqual(correction["correction"],0)
                self.assertEqual(correction["status"],"fallback")
                self.assertIn("수집 실패", correction["reason"])


if __name__ == "__main__":
    unittest.main()
