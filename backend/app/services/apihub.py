"""API Hub text ingestion. Credentials/URLs are never persisted or logged."""
from __future__ import annotations

import asyncio
import re
from datetime import date, datetime, timedelta
from urllib.parse import parse_qsl, urlsplit, urlunsplit

import httpx

from .features import API_TO_KOREAN

DAILY_MAP = {
    "WS_AVG": "avgWs", "WR_DAY": "hr24SumRws", "WD_MAX": "maxWsWd",
    "WS_MAX": "maxWs", "WD_INS": "maxInsWsWd", "WS_INS": "maxInsWs",
    "TA_AVG": "avgTa", "TA_MAX": "maxTa", "TA_MIN": "minTa",
    "TD_AVG": "avgTd", "TS_AVG": "avgTs", "TG_MIN": "minTg",
    "HM_AVG": "avgRhm", "HM_MIN": "minRhm", "PV_AVG": "avgPv",
    "EV_S": "sumSmlEv", "EV_L": "sumLrgEv", "FG_DUR": "sumFogDur",
    "PA_AVG": "avgPa", "PS_AVG": "avgPs", "PS_MAX": "maxPs", "PS_MIN": "minPs",
    "CA_TOT": "avgTca", "SS_DAY": "sumSsHr", "SS_DUR": "ssDur",
    "SI_DAY": "sumGsr", "SI_60M_MAX": "hr1MaxIcsr", "RN_DAY": "sumRn",
    "RN_D99": "n99Rn", "RN_60M_MAX": "hr1MaxRn", "SD_NEW": "ddMefs",
    "SD_MAX": "ddMes", "TE_05": "avgM05Te", "TE_10": "avgM10Te",
    "TE_15": "avgM15Te", "TE_30": "avgM30Te", "TE_50": "avgM50Te",
}
HOURLY_MAP = {"TA": "ta", "HM": "hm", "WS": "ws", "WD": "wd", "PA": "pa",
              "PS": "ps", "TD": "td", "CA_TOT": "dc10Tca", "TS": "ts", "SS": "ss", "SI": "icsr", "RN": "rn"}


def parse_text(body: str) -> list[dict]:
    columns = []
    rows = []
    for line in body.splitlines():
        match = re.match(r"#\s*\d+\.\s+(\w+)\s*:", line.strip())
        if match:
            columns.append(match.group(1))
        elif line.strip() and not line.lstrip().startswith("#"):
            values = line.strip().rstrip(",").split(",") if "," in line else line.split()
            if columns and len(values) == len(columns) and re.fullmatch(r"\d{8,12}", values[0].strip()):
                rows.append(dict(zip(columns, (v.strip() for v in values))))
    if not rows:
        raise RuntimeError("API Hub에 해당 시각 자료가 없거나 응답 형식이 다릅니다.")
    return rows


def numeric(value, temperature=False):
    try:
        n = float(value)
    except (ValueError, TypeError):
        return None
    # Temperature -9 is a valid observation; -99/-999 are missing codes.
    return None if n <= -99 or (not temperature and n == -9) else n


class ApiHubClient:
    def __init__(self, settings):
        self.settings = settings
        self.semaphore = asyncio.Semaphore(3)

    async def request(self, configured_url: str, overrides: dict) -> list[dict]:
        parsed = urlsplit(configured_url)
        if parsed.scheme != "https" or parsed.hostname != "apihub.kma.go.kr" or parsed.port not in (None, 443) or parsed.username:
            raise RuntimeError("API Hub 주소는 https://apihub.kma.go.kr만 허용합니다.")
        if parsed.path not in ("/api/typ01/url/kma_sfctm2.php", "/api/typ01/url/kma_sfcdd.php",
                               "/api/typ01/url/kma_sfctm3.php", "/api/typ01/url/kma_sfcdd3.php"):
            raise RuntimeError("지원하지 않는 API Hub 자료 주소입니다.")
        params = dict(parse_qsl(parsed.query))
        for name in ("tm", "tm1", "tm2"):
            params.pop(name, None)
        key = self.settings.kma_apihub_auth_key.strip() or params.get("authKey", "")
        if not key or key == "YOUR_AUTH_KEY":
            raise RuntimeError("API Hub 인증키를 설정해주세요.")
        params.update(overrides, help="1", disp="0", stn=self.settings.kma_station_id, authKey=key)
        url = urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))
        try:
            async with self.semaphore, httpx.AsyncClient(timeout=40, follow_redirects=False) as client:
                response = await client.get(url, params=params)
        except httpx.HTTPError:
            raise RuntimeError("API Hub 연결 실패 또는 시간 초과") from None
        if response.status_code != 200:
            raise RuntimeError(f"API Hub HTTP 오류: {response.status_code}")
        if len(response.content) > 5_000_000:
            raise RuntimeError("API Hub 응답 크기 제한 초과")
        rows = parse_text(response.content.decode("euc-kr", errors="replace"))
        return [r for r in rows if r.get("STN") == self.settings.kma_station_id]

    @staticmethod
    def range_url(configured_url: str, original: str, replacement: str) -> str:
        parsed = urlsplit(configured_url)
        if parsed.path != f"/api/typ01/url/{original}":
            raise RuntimeError("기간 조회를 위한 원본 API Hub 주소가 올바르지 않습니다.")
        return urlunsplit((parsed.scheme, parsed.netloc, f"/api/typ01/url/{replacement}", parsed.query, ""))

    @staticmethod
    def normalize_daily(row: dict) -> dict:
        target = datetime.strptime(row["TM"], "%Y%m%d").date()
        item = {key: None for key in API_TO_KOREAN}
        item.update(tm=str(target))
        for source, dest in DAILY_MAP.items():
            item[dest] = numeric(row.get(source), dest in ("avgTa", "minTa", "maxTa", "avgTd", "avgTs", "minTg") or dest.startswith("avgM"))
        if item["hr24SumRws"] is not None:
            item["hr24SumRws"] /= 100
        return item

    @staticmethod
    def normalize_hourly(row: dict) -> dict:
        dt = datetime.strptime(row["TM"], "%Y%m%d%H%M")
        item = {"tm": dt.strftime("%Y-%m-%d %H:%M")}
        item.update({dest: numeric(row.get(source), dest in ("ta", "td", "ts")) for source, dest in HOURLY_MAP.items()})
        item["rn_day"] = numeric(row.get("RN_DAY"))
        direction = item.get("wd")
        # Hub WD is a 36-direction code, not ASOS degrees. Base 44 mapping stays frozen.
        item["wd_degrees"] = direction * 10 if direction is not None and 0 < direction <= 36 else None
        return item

    async def hourly_range(self, start: datetime, end: datetime) -> list[dict]:
        if end < start or end - start >= timedelta(days=31):
            raise RuntimeError("시간자료 기간은 31일 이내여야 합니다.")
        url = self.range_url(self.settings.kma_apihub_hourly_file_url, "kma_sfctm2.php", "kma_sfctm3.php")
        rows = await self.request(url, {"tm1": start.strftime("%Y%m%d%H%M"), "tm2": end.strftime("%Y%m%d%H%M")})
        items = [self.normalize_hourly(row) for row in rows]
        return sorted([item for item in items if start.replace(tzinfo=None) <= datetime.fromisoformat(item["tm"]) <= end.replace(tzinfo=None)], key=lambda r: r["tm"])

    async def daily_range(self, start: date, end: date) -> list[dict]:
        if end < start or (end - start).days >= 31:
            raise RuntimeError("일자료 기간은 31일 이내여야 합니다.")
        url = self.range_url(self.settings.kma_apihub_daily_file_url, "kma_sfcdd.php", "kma_sfcdd3.php")
        rows = await self.request(url, {"tm1": start.strftime("%Y%m%d"), "tm2": end.strftime("%Y%m%d"), "mode": "0"})
        return sorted([self.normalize_daily(row) for row in rows if start.strftime("%Y%m%d") <= row["TM"] <= end.strftime("%Y%m%d")], key=lambda r: r["tm"])

    async def daily(self, target: date) -> list[dict]:
        rows = await self.request(self.settings.kma_apihub_daily_file_url, {"tm": target.strftime("%Y%m%d")})
        result = []
        for row in rows:
            if row["TM"] != target.strftime("%Y%m%d"):
                continue
            result.append(self.normalize_daily(row))
        return result

    async def hourly(self, target: date, end_hour: int) -> list[dict]:
        async def hour(h):
            stamp = datetime.combine(target, datetime.min.time()) + timedelta(hours=h)
            try:
                rows = await self.request(self.settings.kma_apihub_hourly_file_url, {"tm": stamp.strftime("%Y%m%d%H%M")})
            except RuntimeError as exc:
                if "해당 시각 자료가 없거나" in str(exc):
                    return []
                raise
            return rows
        batches = await asyncio.gather(*(hour(h) for h in range(end_hour + 1)))
        result = []
        for rows in batches:
            for row in rows:
                dt = datetime.strptime(row["TM"], "%Y%m%d%H%M")
                if dt.date() != target:
                    continue
                result.append(self.normalize_hourly(row))
        if not result:
            raise RuntimeError("API Hub에 오늘 시간자료가 아직 없습니다.")
        return sorted(result, key=lambda r: r["tm"])
