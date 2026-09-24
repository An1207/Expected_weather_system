from __future__ import annotations

from datetime import date, datetime, timedelta
from time import monotonic
from urllib.parse import unquote
from zoneinfo import ZoneInfo

import httpx

from ..config import Settings
from ..schemas import HourlyObservation, TodayWeather

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
        self._cache: dict[str, tuple[float, list[dict]]] = {}

    def _require_key(self) -> None:
        if not self._api_key:
            raise RuntimeError("KMA_API_KEY가 설정되지 않았습니다.")

    async def _request(self, url: str, params: dict[str, object]) -> list[dict]:
        self._require_key()
        cache_key = f"{url}:{sorted(params.items())}"
        cached = self._cache.get(cache_key)
        if cached and cached[0] > monotonic():
            return cached[1]
        request_params = {
            "serviceKey": self._api_key,
            "pageNo": 1,
            "numOfRows": 999,
            "dataType": "JSON",
            "dataCd": "ASOS",
            **params,
        }
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(url, params=request_params)
            response.raise_for_status()
        try:
            payload = response.json()
        except ValueError as exc:
            raise RuntimeError(f"기상청 API가 JSON이 아닌 응답을 반환했습니다: {response.text[:200]}") from exc
        envelope = payload.get("response", {})
        header = envelope.get("header", {})
        if header.get("resultCode") not in (None, "00", "0"):
            raise RuntimeError(f"기상청 API 오류: {header.get('resultCode')} {header.get('resultMsg')}")
        items = (envelope.get("body", {}).get("items") or {}).get("item") or []
        result = [items] if isinstance(items, dict) else items
        self._cache[cache_key] = (monotonic() + self.settings.cache_ttl_seconds, result)
        return result

    async def daily(self, start: date, end: date) -> list[dict]:
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
        return await self._request(
            self.settings.kma_hourly_url,
            {
                "dateCd": "HR",
                "startDt": target_date.strftime("%Y%m%d"),
                "startHh": "00",
                "endDt": target_date.strftime("%Y%m%d"),
                "endHh": f"{end_hour:02d}",
                "stnIds": self.settings.kma_station_id,
            },
        )

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
        today = datetime.now(KST).date()
        hourly_items = await self.hourly(today)
        hourly = [self.normalize_hourly(item) for item in hourly_items if item.get("tm")]
        latest = hourly[-1] if hourly else None
        temperatures = [item.temperature for item in hourly if item.temperature is not None]
        precipitation = sum(item.precipitation or 0.0 for item in hourly)
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
            source="KMA_ASOS_HOURLY",
            variables=latest.raw if latest else {},
            hourly=hourly,
        )

    async def recent_daily_history(self, days: int = 45) -> list[dict]:
        end = datetime.now(KST).date() - timedelta(days=1)
        return await self.daily(end - timedelta(days=days), end)
