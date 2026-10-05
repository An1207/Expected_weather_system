import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from datetime import date
import httpx

from app.services.apihub import ApiHubClient, numeric, parse_text


class HubTests(unittest.IsolatedAsyncioTestCase):
    def test_parse_and_missing_codes(self):
        rows = parse_text('  # 1. TM : date\n  # 2. STN : station\n  # 3. TA : temp\n202610060000,108,-9.0,\n#END7777')
        self.assertEqual(rows[0]['STN'], '108')
        self.assertEqual(numeric('-9', temperature=True), -9)
        self.assertIsNone(numeric('-99', temperature=True))
        self.assertIsNone(numeric('-9'))

    def test_errors_do_not_echo_body(self):
        with self.assertRaisesRegex(RuntimeError, '응답 형식') as error:
            parse_text('authKey=SECRET_ACCESS_KEY')
        self.assertNotIn('SECRET_ACCESS_KEY', str(error.exception))

    async def test_reject_foreign_host(self):
        c = ApiHubClient(SimpleNamespace())
        with self.assertRaisesRegex(RuntimeError, '허용'):
            await c.request('https://example.com/?authKey=SECRET', {})

    async def test_network_error_redacts_credentials(self):
        c = ApiHubClient(SimpleNamespace(kma_apihub_auth_key='SECRET', kma_station_id='108'))
        with patch('httpx.AsyncClient.get', AsyncMock(side_effect=httpx.ConnectError('authKey=SECRET'))):
            with self.assertRaises(RuntimeError) as error:
                await c.request('https://apihub.kma.go.kr/api/typ01/url/kma_sfcdd.php', {})
        self.assertNotIn('SECRET', str(error.exception))

    async def test_daily_units_and_absent_fields(self):
        c = ApiHubClient(SimpleNamespace(kma_apihub_daily_file_url='unused'))
        with patch.object(c, 'request', AsyncMock(return_value=[{'TM':'20261005','STN':'108','TA_AVG':'16.2','WR_DAY':'1200'}])):
            row = (await c.daily(date(2026,10,5)))[0]
        self.assertEqual(row['avgTa'],16.2)
        self.assertEqual(row['hr24SumRws'],12)
        self.assertIsNone(row['avgCm5Te'])


if __name__ == '__main__':
    unittest.main()
