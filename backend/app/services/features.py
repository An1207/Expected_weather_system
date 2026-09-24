from __future__ import annotations

import numpy as np
import pandas as pd

API_TO_KOREAN = {
    "tm": "일시", "avgTa": "평균기온(°C)", "minTa": "최저기온(°C)", "maxTa": "최고기온(°C)",
    "hr1MaxRn": "1시간 최다강수량(mm)", "sumRn": "일강수량(mm)",
    "maxInsWs": "최대 순간 풍속(m/s)", "maxInsWsWd": "최대 순간 풍속 풍향(16방위)",
    "maxWs": "최대 풍속(m/s)", "maxWsWd": "최대 풍속 풍향(16방위)",
    "avgWs": "평균 풍속(m/s)", "hr24SumRws": "풍정합(100m)", "maxWd": "최다풍향(16방위)",
    "avgTd": "평균 이슬점온도(°C)", "minRhm": "최소 상대습도(%)", "avgRhm": "평균 상대습도(%)",
    "avgPv": "평균 증기압(hPa)", "avgPa": "평균 현지기압(hPa)",
    "maxPs": "최고 해면기압(hPa)", "minPs": "최저 해면기압(hPa)", "avgPs": "평균 해면기압(hPa)",
    "ssDur": "가조시간(hr)", "sumSsHr": "합계 일조시간(hr)",
    "hr1MaxIcsr": "1시간 최다일사량(MJ/m2)", "sumGsr": "합계 일사량(MJ/m2)",
    "ddMefs": "일 최심신적설(cm)", "ddMes": "일 최심적설(cm)", "sumDpthFhsc": "합계 3시간 신적설(cm)",
    "avgTca": "평균 전운량(1/10)", "avgLmac": "평균 중하층운량(1/10)",
    "avgTs": "평균 지면온도(°C)", "minTg": "최저 초상온도(°C)",
    "avgCm5Te": "평균 5cm 지중온도(°C)", "avgCm10Te": "평균 10cm 지중온도(°C)",
    "avgCm20Te": "평균 20cm 지중온도(°C)", "avgCm30Te": "평균 30cm 지중온도(°C)",
    "avgM05Te": "0.5m 지중온도(°C)", "avgM10Te": "1.0m 지중온도(°C)",
    "avgM15Te": "1.5m 지중온도(°C)", "avgM30Te": "3.0m 지중온도(°C)",
    "avgM50Te": "5.0m 지중온도(°C)", "sumLrgEv": "합계 대형증발량(mm)",
    "sumSmlEv": "합계 소형증발량(mm)", "n99Rn": "9-9강수(mm)", "sumFogDur": "안개 계속시간(hr)",
}

DATE_COLUMN = "일시"
BASE_FEATURES = [name for key, name in API_TO_KOREAN.items() if key != "tm"]
ZERO_FILL_FEATURES = [
    column for column in BASE_FEATURES
    if any(token in column for token in ("강수", "적설", "안개", "증발량"))
]
TEMPORAL_FEATURES = [
    "평균기온(°C)", "최저기온(°C)", "최고기온(°C)", "평균 이슬점온도(°C)",
    "평균 상대습도(%)", "평균 현지기압(hPa)", "평균 해면기압(hPa)", "평균 풍속(m/s)",
    "일강수량(mm)", "평균 전운량(1/10)", "평균 지면온도(°C)",
    "평균 5cm 지중온도(°C)", "평균 30cm 지중온도(°C)",
    "합계 일조시간(hr)", "합계 일사량(MJ/m2)",
]
LAGS = (1, 2, 3, 7, 14)
ROLLING_WINDOWS = (3, 7, 14)


def clean_raw_daily(items: list[dict]) -> pd.DataFrame:
    frame = pd.DataFrame(items).rename(columns=API_TO_KOREAN)
    missing = sorted(set([DATE_COLUMN, *BASE_FEATURES]) - set(frame.columns))
    if missing:
        raise ValueError(f"기상청 일자료에 모델 입력 필드가 없습니다: {missing}")
    data = frame[[DATE_COLUMN, *BASE_FEATURES]].copy()
    data[DATE_COLUMN] = pd.to_datetime(data[DATE_COLUMN])
    data = data.sort_values(DATE_COLUMN).drop_duplicates(DATE_COLUMN)
    for column in BASE_FEATURES:
        data[column] = pd.to_numeric(data[column], errors="coerce")
    data[ZERO_FILL_FEATURES] = data[ZERO_FILL_FEATURES].fillna(0.0)
    data[BASE_FEATURES] = data[BASE_FEATURES].ffill()
    return data.reset_index(drop=True)


def build_v2_features(clean: pd.DataFrame) -> pd.DataFrame:
    data = clean.copy()
    day_of_year = data[DATE_COLUMN].dt.dayofyear
    engineered: dict[str, pd.Series] = {
        "연중일_sin": np.sin(2 * np.pi * day_of_year / 365.25),
        "연중일_cos": np.cos(2 * np.pi * day_of_year / 365.25),
        "일교차(°C)": data["최고기온(°C)"] - data["최저기온(°C)"],
        "기온_이슬점차(°C)": data["평균기온(°C)"] - data["평균 이슬점온도(°C)"],
        "해면기압범위(hPa)": data["최고 해면기압(hPa)"] - data["최저 해면기압(hPa)"],
        "지면_대기온도차(°C)": data["평균 지면온도(°C)"] - data["평균기온(°C)"],
        "30cm지중_대기온도차(°C)": data["평균 30cm 지중온도(°C)"] - data["평균기온(°C)"],
        "일조율": (data["합계 일조시간(hr)"] / data["가조시간(hr)"].replace(0, np.nan)).clip(0, 1),
    }
    for column in TEMPORAL_FEATURES:
        for lag in LAGS:
            engineered[f"{column}__lag{lag}"] = data[column].shift(lag)
        engineered[f"{column}__delta1"] = data[column] - data[column].shift(1)
        engineered[f"{column}__delta3"] = data[column] - data[column].shift(3)
        for window in ROLLING_WINDOWS:
            history = data[column].rolling(window=window, min_periods=window)
            engineered[f"{column}__mean{window}"] = history.mean()
            engineered[f"{column}__std{window}"] = history.std()
    return pd.concat([data, pd.DataFrame(engineered, index=data.index)], axis=1)

