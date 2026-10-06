"""Separate ASOS hourly credentials without changing daily or live Hub sources."""
import unittest
from datetime import date
from unittest.mock import AsyncMock, patch

import httpx

from app.config import Settings
from app.services.kma import KmaClient


class AsosHourlyConfigTests(unittest.IsolatedAsyncioTestCase):
    def client(self, **kwargs):
        return KmaClient(Settings(_env_file=None, kma_api_key="DAILY_TEST_KEY",
                                  kma_asos_hourly_api_key="HOURLY%2BTEST_KEY", **kwargs))

    async def test_separate_hourly_key_does_not_change_daily_key(self):
        client = self.client()
        payload = {"response": {"header": {"resultCode": "00"},
                                "body": {"items": {"item": [{"tm": "2023-01-01 01:00", "wd": "270"}]}}}}
        response = httpx.Response(200, json=payload)
        with patch("httpx.AsyncClient.get", AsyncMock(return_value=response)) as request:
            await client.historical_hourly(date(2023, 1, 1), date(2023, 1, 1))
            self.assertEqual(request.call_args.kwargs["params"]["serviceKey"], "HOURLY+TEST_KEY")
            await client.daily(date(2023, 1, 1), date(2023, 1, 2))
            self.assertEqual(request.call_args.kwargs["params"]["serviceKey"], "DAILY_TEST_KEY")

    async def test_blank_hourly_key_uses_existing_portal_key(self):
        client = KmaClient(Settings(_env_file=None, kma_api_key="PORTAL_KEY", kma_asos_hourly_api_key=""))
        with patch.object(client, "_request", AsyncMock(return_value=[])) as request:
            await client.historical_hourly(date(2023, 1, 1), date(2023, 1, 1))
            self.assertEqual(request.call_args.kwargs["api_key"], "PORTAL_KEY")

    async def test_hourly_key_can_be_used_without_daily_key(self):
        client = KmaClient(Settings(_env_file=None, kma_api_key="", kma_asos_hourly_api_key="HOURLY_ONLY"))
        payload = {"response": {"header": {"resultCode": "00"}, "body": {"items": {"item": []}}}}
        with patch("httpx.AsyncClient.get", AsyncMock(return_value=httpx.Response(200, json=payload))) as request:
            await client.historical_hourly(date(2023, 1, 1), date(2023, 1, 1))
            self.assertEqual(request.call_args.kwargs["params"]["serviceKey"], "HOURLY_ONLY")
            with self.assertRaisesRegex(RuntimeError, "인증키"):
                await client.daily(date(2023, 1, 1), date(2023, 1, 2))

    async def test_live_hub_priority_is_unchanged(self):
        client = self.client(kma_apihub_hourly_file_url="configured")
        with patch.object(client.hub, "hourly", AsyncMock(return_value=[])) as hub, \
                patch.object(client, "_request", AsyncMock()) as portal:
            await client.hourly(date(2023, 1, 1))
            hub.assert_awaited_once()
            portal.assert_not_awaited()

    async def test_hourly_without_hub_uses_separate_key(self):
        client = self.client(kma_apihub_hourly_file_url="")
        with patch.object(client, "_request", AsyncMock(return_value=[])) as request:
            await client.hourly(date(2023, 1, 1))
            self.assertEqual(request.call_args.kwargs["api_key"], "HOURLY+TEST_KEY")

    async def test_unsafe_hourly_url_is_rejected_before_outbound_request(self):
        official = "https://apis.data.go.kr/1360000/AsosHourlyInfoService/getWthrDataList"
        for url in ("https://foreign.example/hourly", official + "?serviceKey=PRIVATE_TEST_VALUE",
                    official.replace("https:", "http:"), official + "#PRIVATE_TEST_VALUE"):
            with self.subTest(url=url):
                client = self.client(kma_hourly_url=url)
                with patch("httpx.AsyncClient.get", AsyncMock()) as request:
                    with self.assertRaises(RuntimeError) as error:
                        await client.historical_hourly(date(2023, 1, 1), date(2023, 1, 1))
                    self.assertNotIn("PRIVATE_TEST_VALUE", str(error.exception))
                    request.assert_not_awaited()

    async def test_connection_error_does_not_expose_separate_key(self):
        client = self.client()
        with patch("httpx.AsyncClient.get", AsyncMock(side_effect=httpx.ConnectError("HOURLY+TEST_KEY"))):
            with self.assertRaises(RuntimeError) as error:
                await client.historical_hourly(date(2023, 1, 1), date(2023, 1, 1))
        self.assertNotIn("HOURLY+TEST_KEY", str(error.exception))


if __name__ == "__main__":
    unittest.main()
