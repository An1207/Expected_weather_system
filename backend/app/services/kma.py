from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta
from time import monotonic
from urllib.parse import unquote, urlsplit
from zoneinfo import ZoneInfo

import httpx

from ..config import Settings
from ..schemas import HourlyObservation, TodayWeather
from .apihub import ApiHubClient

KST = ZoneInfo("Asia/Seoul")


def _number(value: object) -> float | None:
    if value in (None, "", " "):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


class KmaClient:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._api_key = unquote(settings.kma_api_key.strip())
        self._hourly_api_key = unquote(settings.kma_asos_hourly_api_key.strip()) or self._api_key
        self._cache: dict[str, tuple[float, list[dict]]] = {}
        self.hub = ApiHubClient(settings)
        self._hourly_period_denied = False

    async def _hub_cached(self, key, fetch):
        cached = self._cache.get(key)
        if cached and cached[0] > monotonic():
            return cached[1]
        rows = await fetch()
        self._cache[key] = (monotonic() + self.settings.cache_ttl_seconds, rows)
        return rows

    def _asos_hourly_url(self) -> str:
        # Never send a credential to an arbitrary URL or preserve query-string keys.
        try:
            parsed = urlsplit(self.settings.kma_hourly_url)
            allowed = (parsed.scheme == "https" and parsed.hostname == "apis.data.go.kr"
                       and parsed.port in (None, 443) and not parsed.username and not parsed.password
                       and parsed.path == "/1360000/AsosHourlyInfoService/getWthrDataList"
                       and not parsed.query and not parsed.fragment)
        except ValueError:
            allowed = False
        if not allowed:
            raise RuntimeError("ASOS 시간자료 URL은 인증키·쿼리가 없는 공식 HTTPS 조회 주소여야 합니다.")
        return self.settings.kma_hourly_url

    async def _request(self, url: str, params: dict[str, object], *, api_key: str | None = None) -> list[dict]:
        selected_key = self._api_key if api_key is None else api_key
        if not selected_key:
            setting_name = "KMA_API_KEY" if api_key is None else "KMA_ASOS_HOURLY_API_KEY 또는 KMA_API_KEY"
            raise RuntimeError(f"공공데이터포털 인증키가 없습니다. {setting_name}를 설정하세요.")
        cache_key = f"{url}:{sorted(params.items())}"
        cached = self._cache.get(cache_key)
        if cached and cached[0] > monotonic():
            return cached[1]
        request_params = {
            "serviceKey": selected_key,
            "pageNo": 1,
            "numOfRows": 999,
            "dataType": "JSON",
            "dataCd": "ASOS",
            **params,
        }
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(url, params=request_params)
                if response.is_error:
                    raise RuntimeError(f"기상청 API HTTP 오류: {response.status_code}")
        except httpx.HTTPError:
            raise RuntimeError("기상청 API 연결 실패 또는 시간 초과") from None
        try:
            payload = response.json()
        except ValueError as exc:
            raise RuntimeError("기상청 API가 JSON이 아닌 응답을 반환했습니다.") from None
        envelope = payload.get("response", {})
        header = envelope.get("header", {})
        if header.get("resultCode") not in (None, "00", "0"):
            raise RuntimeError(f"기상청 API 오류 코드: {header.get('resultCode')}")
        items = (envelope.get("body", {}).get("items") or {}).get("item") or []
        result = [items] if isinstance(items, dict) else items
        self._cache[cache_key] = (monotonic() + self.settings.cache_ttl_seconds, result)
        return result

    async def daily(self, start: date, end: date) -> list[dict]:
        if start == end and self.settings.kma_apihub_daily_file_url:
            return await self._hub_cached(f"hub-day:{start}", lambda: self.hub.daily(start))
        return await self._request(
            self.settings.kma_daily_url,
            {
                "dateCd": "DAY",
                "startDt": start.strftime("%Y%m%d"),
                "endDt": end.strftime("%Y%m%d"),
                "stnIds": self.settings.kma_station_id,
            },
        )

    async def hourly(self, target_date: date) -> list[dict]:
        now = datetime.now(KST)
        end_hour = now.hour if target_date == now.date() else 23
        if self.settings.kma_apihub_hourly_file_url:
            return await self._hub_cached(f"hub-hour:{target_date}:{end_hour}", lambda: self.hub.hourly(target_date, end_hour))
        return await self._request(
            self._asos_hourly_url(),
            {
                "dateCd": "HR",
                "startDt": target_date.strftime("%Y%m%d"),
                "startHh": "00",
                "endDt": target_date.strftime("%Y%m%d"),
                "endHh": f"{end_hour:02d}",
                "stnIds": self.settings.kma_station_id,
            },
            api_key=self._hourly_api_key,
        )

    async def correction_history(self, start: datetime, end: datetime) -> list[dict]:
        if not self.settings.kma_apihub_hourly_file_url:
            raise RuntimeError("시간자료 보정에는 API Hub 설정이 필요합니다.")
        async def fetch():
            if not self._hourly_period_denied:
                try:
                    return await self.hub.hourly_range(start, end)
                except RuntimeError as exc:
                    if "HTTP 오류: 403" not in str(exc):
                        raise
                    self._hourly_period_denied = True
            # Period endpoint needs its own approval. The already approved point
            # endpoint supplies identical observations; no new credential is needed.
            days = [start.date()+timedelta(days=offset) for offset in range((end.date()-start.date()).days+1)]
            batches = await asyncio.gather(*(self.hourly(day) for day in days))
            return [row for batch in batches for row in batch
                    if start.replace(tzinfo=None) <= datetime.fromisoformat(str(row["tm"])) <= end.replace(tzinfo=None)]
        return await self._hub_cached(f"correction-hour:{start.isoformat()}:{end.isoformat()}", fetch)

    async def historical_hourly(self, start: date, end: date) -> list[dict]:
        if end < start or (end-start).days >= 31:
            raise RuntimeError("과거 시간자료 조회는 31일 이내여야 합니다.")
        rows = await self._request(self._asos_hourly_url(), {
            "dateCd": "HR", "startDt": start.strftime("%Y%m%d"), "startHh": "00",
            "endDt": end.strftime("%Y%m%d"), "endHh": "23", "stnIds": self.settings.kma_station_id},
            api_key=self._hourly_api_key)
        normalized = []
        for row in rows:
            item = {key: row.get(key) for key in ("tm", "ta", "hm", "pa", "ps", "ws", "td", "dc10Tca")}
            item["wd_degrees"] = _number(row.get("wd"))
            normalized.append(item)
        return normalized

    def normalize_hourly(self, item: dict) -> HourlyObservation:
        return HourlyObservation(
            observed_at=datetime.strptime(str(item["tm"]), "%Y-%m-%d %H:%M").replace(tzinfo=KST),
            temperature=_number(item.get("ta")),
            precipitation=_number(item.get("rn")),
            humidity=_number(item.get("hm")),
            wind_speed=_number(item.get("ws")),
            wind_direction=_number(item.get("wd")),
            local_pressure=_number(item.get("pa")),
            sea_level_pressure=_number(item.get("ps")),
            sunshine=_number(item.get("ss")),
            solar_radiation=_number(item.get("icsr")),
            cloud_amount=_number(item.get("dc10Tca")),
            ground_temperature=_number(item.get("ts")),
            raw={key: value for key, value in item.items()},
        )

    async def today_weather(self) -> TodayWeather:
        return await self.weather_for_date(datetime.now(KST).date())

    async def weather_for_date(self, target_date: date) -> TodayWeather:
        hourly_items = await self.hourly(target_date)
        hourly = sorted(
            [self.normalize_hourly(item) for item in hourly_items if item.get("tm")],
            key=lambda item: item.observed_at,
        )
        latest = hourly[-1] if hourly else None
        temperatures = [item.temperature for item in hourly if item.temperature is not None]
        precipitation = sum(item.precipitation or 0.0 for item in hourly)
        if self.settings.kma_apihub_hourly_file_url and latest:
            precipitation = _number(latest.raw.get("rn_day"))
        return TodayWeather(
            station_id=self.settings.kma_station_id,
            station_name=self.settings.kma_station_name,
            observed_at=latest.observed_at if latest else None,
            temperature=latest.temperature if latest else None,
            min_temperature=min(temperatures) if temperatures else None,
            max_temperature=max(temperatures) if temperatures else None,
            humidity=latest.humidity if latest else None,
            precipitation=precipitation if hourly else None,
            wind_speed=latest.wind_speed if latest else None,
            local_pressure=latest.local_pressure if latest else None,
            cloud_amount=latest.cloud_amount if latest else None,
            source="KMA_APIHUB_ASOS_HOURLY" if self.settings.kma_apihub_hourly_file_url else "KMA_ASOS_HOURLY",
            variables=latest.raw if latest else {},
            hourly=hourly,
        )

    async def yesterday_weather(self) -> TodayWeather:
        target_date = datetime.now(KST).date() - timedelta(days=1)
        items = await self.daily(target_date, target_date)
        if not items:
            raise RuntimeError("어제의 확정 일자료가 아직 제공되지 않았습니다.")
        item = items[-1]
        return TodayWeather(
            station_id=self.settings.kma_station_id,
            station_name=self.settings.kma_station_name,
            observed_at=datetime.combine(target_date, datetime.min.time(), tzinfo=KST),
            temperature=_number(item.get("avgTa")),
            min_temperature=_number(item.get("minTa")),
            max_temperature=_number(item.get("maxTa")),
            humidity=_number(item.get("avgRhm")),
            precipitation=_number(item.get("sumRn")) or 0.0,
            wind_speed=_number(item.get("avgWs")),
            local_pressure=_number(item.get("avgPa")),
            cloud_amount=_number(item.get("avgTca")),
            source="KMA_APIHUB_ASOS_DAILY" if self.settings.kma_apihub_daily_file_url else "KMA_ASOS_DAILY", variables=item, hourly=[],
        )

    async def recent_daily_history(self, days: int = 45) -> list[dict]:
        end = datetime.now(KST).date() - timedelta(days=1)
        rows = await self.daily(end - timedelta(days=days), end)
        if self.settings.kma_apihub_daily_file_url:
            latest = await self.daily(end, end)
            by_date = {row["tm"]: row for row in rows}
            for item in latest:
                base = by_date.get(item["tm"], {}).copy()
                base.update({key: val for key, val in item.items() if val is not None})
                for key in item:
                    base.setdefault(key, None)
                by_date[item["tm"]] = base
            rows = [by_date[key] for key in sorted(by_date)]
        return rows
