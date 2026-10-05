"""Contract checks using real V2 inference and mocked KMA responses (no API key needed)."""
import unittest
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

    def test_hourly_failure_preserves_yesterday(self):
        with patch.object(main.kma, "daily", AsyncMock(return_value=self.daily[-1:])), patch.object(main.kma, "hourly", AsyncMock(side_effect=RuntimeError("hourly unavailable"))), patch.object(main.kma, "recent_daily_history", AsyncMock(side_effect=RuntimeError("daily unavailable"))):
            with TestClient(main.app) as client:
                response = client.get("/api/v1/dashboard")
                self.assertEqual(response.status_code, 200)
                payload = response.json()
                self.assertIsNotNone(payload["yesterday"])
                self.assertIsNone(payload["today"])
                self.assertEqual(payload["errors"]["today"], "hourly unavailable")

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


if __name__ == "__main__":
    unittest.main()
