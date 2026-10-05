import useSWR from "swr";
import { fetchDashboard } from "./api";
import { CurrentClock } from "./components/CurrentClock";
import { HourlyStrip } from "./components/HourlyStrip";
import { Metric } from "./components/Metric";
import { VariableTable } from "./components/VariableTable";
import { WeatherIcon } from "./components/WeatherIcon";
import type { TodayWeather } from "./types";

const dateFormat = new Intl.DateTimeFormat("ko-KR", { month: "long", day: "numeric", weekday: "short", timeZone: "Asia/Seoul" });
const timeFormat = new Intl.DateTimeFormat("ko-KR", { hour: "2-digit", minute: "2-digit", hour12: false, timeZone: "Asia/Seoul" });
const value = (n: number | null, unit: string, digits = 1) => n === null ? "—" : `${n.toFixed(digits)}${unit}`;

function ObservationPanel({ weather, yesterday, error, loading }: { weather?: TodayWeather | null; yesterday?: boolean; error?: string; loading: boolean }) {
  return <section className={`panel observation-panel ${yesterday ? "yesterday-panel" : "today-panel"}`}>
    <div className="section-heading"><div><span className="eyebrow">{yesterday ? "YESTERDAY" : "TODAY"}</span><h1>{yesterday ? "어제 날씨" : "오늘 날씨"}</h1><p>{weather?.observed_at ? dateFormat.format(new Date(weather.observed_at)) : "기상청 ASOS 관측"}</p></div><span className="live-badge">{yesterday ? "확정 일자료" : "시간별 관측"}</span></div>
    {weather ? <>
      <div className="current-weather"><WeatherIcon cloudAmount={weather.cloud_amount} /><div className="current-weather__temperature"><small>{yesterday ? "일 평균기온" : "최신 관측기온"}</small><strong>{weather.temperature?.toFixed(1) ?? "—"}</strong><span>°C</span><p>최저 {value(weather.min_temperature, "°")} · 최고 {value(weather.max_temperature, "°")}</p></div></div>
      <p className="observation-caption">{yesterday ? "하루 전체 관측을 집계한 값입니다." : `${weather.observed_at ? timeFormat.format(new Date(weather.observed_at)) : "—"} 기준 · 최저/최고는 현재까지 관측 범위`}</p>
      <div className="metric-grid"><Metric label="습도" value={value(weather.humidity, "%", 0)} hint={yesterday ? "일 평균" : "최신 관측"} /><Metric label="강수량" value={value(weather.precipitation, " mm")} hint="하루 누적" /><Metric label="풍속" value={value(weather.wind_speed, " m/s")} hint={yesterday ? "일 평균" : "최신 관측"} /><Metric label="현지기압" value={value(weather.local_pressure, " hPa")} hint={yesterday ? "일 평균" : "최신 관측"} /></div>
      {yesterday ? <div className="daily-note"><h2>어제의 기록</h2><p>확정된 일자료는 내일 평균기온 예측의 입력으로 사용됩니다.</p></div> : <><div className="subsection-heading"><h2>시간별 관측</h2><span>기온 · 습도</span></div><HourlyStrip items={weather.hourly} /></>}
      <VariableTable variables={weather.variables} />
    </> : <div className="panel-placeholder"><span>{loading ? "관측자료를 불러오는 중" : "관측자료 대기"}</span><p>{error ?? "기상청에서 제공되는 관측자료가 여기에 표시됩니다."}</p></div>}
  </section>;
}

export default function App() {
  const { data, error, isLoading, isValidating, mutate } = useSWR("/api/v1/dashboard", fetchDashboard, { refreshInterval: 600000, revalidateOnFocus: false, dedupingInterval: 60000 });
  const tomorrow = data?.tomorrow;
  return <div className="app-shell">
    <header className="topbar"><a className="brand" href="/"><span className="brand__symbol" role="img" aria-label="대한민국 국기">🇰🇷</span><span><strong>상명</strong><small>AI 기온 예측</small></span></a><CurrentClock /><button className="refresh-button" type="button" disabled={isValidating} onClick={() => void mutate()}>{isValidating ? "갱신 중…" : "새로고침"}</button></header>
    <div className="dashboard-intro"><div><span className="eyebrow">WEATHER OVERVIEW</span><h2>어제의 기록, 오늘의 날씨, 내일의 기온</h2></div><span>{data?.station_name ?? "서울"} · 기상청 ASOS</span></div>
    {error ? <div className="dashboard-alert" role="alert">연결 오류: {error.message}</div> : null}
    <main className="dashboard">
      <ObservationPanel yesterday weather={data?.yesterday} error={data?.errors.yesterday ?? error?.message} loading={isLoading} />
      <ObservationPanel weather={data?.today} error={data?.errors.today ?? error?.message} loading={isLoading} />
      <section className="tomorrow-panel panel"><div className="section-heading section-heading--light"><div><span className="eyebrow">TOMORROW · AI FORECAST</span><h1>내일 기온 예측</h1><p>{tomorrow ? dateFormat.format(new Date(`${tomorrow.predicted_for_date}T12:00:00+09:00`)) : "AI 모델 기반 평균기온"}</p></div><span className="ai-badge">AI</span></div>
        {tomorrow ? <><div className="forecast-hero"><p>예상 평균기온</p><div><strong>{tomorrow.predicted_avg_temperature.toFixed(1)}</strong><span>°C</span></div><span className="temperature-change">어제 평균 대비 {value(tomorrow.predicted_avg_temperature - tomorrow.observed_avg_temperature, "°C")}</span></div><div className="model-card"><div><span>예측 모델</span><strong>CatBoost × LightGBM</strong></div><div><span>테스트 평균 절대오차</span><strong>{tomorrow.model_test_mae?.toFixed(2) ?? "—"}°C</strong></div><div><span>관측 입력 기준일</span><strong>{tomorrow.observation_date}</strong></div></div><div className="prediction-note"><p>어제까지의 일자료와 최근 기상 흐름을 바탕으로 내일 평균기온을 예측합니다. 강수·구름 상태는 예측 대상에 포함되지 않습니다.</p></div></> : <div className="panel-placeholder"><span>{isLoading ? "AI 예측을 불러오는 중" : "예측 준비 중"}</span><p>{data?.errors.tomorrow ?? error?.message ?? "기상청 일자료가 준비되면 학습된 AI 모델의 예측값을 표시합니다."}</p></div>}
        <div className="history"><div className="subsection-heading"><h2>최근 예측 기록</h2></div><ul>{data?.recent_predictions.map(item => <li key={`${item.predicted_for_date}-${item.model_version}`}><time>{item.predicted_for_date}</time><strong>{item.predicted_avg_temperature.toFixed(1)}°</strong><span>{item.actual_avg_temperature === null ? "관측 대기" : `실제 ${item.actual_avg_temperature.toFixed(1)}°`}</span></li>)}</ul>{!data?.recent_predictions.length ? <p className="history__empty">예측 결과가 생성되면 자동으로 저장됩니다.</p> : null}</div>
      </section>
    </main><footer><span>DATA · 기상청 ASOS / KST</span><span>{tomorrow ? `MODEL · ${tomorrow.model_version}` : "일자료 기반 AI 평균기온 예측"}</span></footer>
  </div>;
}
