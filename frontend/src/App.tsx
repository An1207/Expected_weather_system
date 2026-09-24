import useSWR from "swr";

import { fetchDashboard } from "./api";
import { HourlyStrip } from "./components/HourlyStrip";
import { Metric } from "./components/Metric";
import { VariableTable } from "./components/VariableTable";
import { WeatherIcon } from "./components/WeatherIcon";

const dateFormatter = new Intl.DateTimeFormat("ko-KR", {
  month: "long",
  day: "numeric",
  weekday: "long",
  timeZone: "Asia/Seoul",
});

const timeFormatter = new Intl.DateTimeFormat("ko-KR", {
  hour: "2-digit",
  minute: "2-digit",
  hour12: false,
  timeZone: "Asia/Seoul",
});

function formatValue(value: number | null, unit: string, digits = 0) {
  return value === null ? "—" : `${value.toFixed(digits)}${unit}`;
}

function ErrorState({ message, retry }: { message: string; retry: () => void }) {
  return (
    <main className="state-page">
      <div className="state-card">
        <span className="state-card__mark">!</span>
        <h1>날씨 데이터를 불러오지 못했습니다</h1>
        <p>{message}</p>
        <button onClick={retry} type="button">다시 시도</button>
      </div>
    </main>
  );
}

export default function App() {
  const { data, error, isLoading, mutate } = useSWR("/api/v1/dashboard", fetchDashboard, {
    refreshInterval: 10 * 60 * 1000,
    revalidateOnFocus: false,
    dedupingInterval: 60 * 1000,
  });

  if (error) return <ErrorState message={error.message} retry={() => void mutate()} />;
  if (isLoading || !data) {
    return (
      <main className="state-page">
        <div className="loader" aria-label="날씨 데이터 로딩 중" />
        <p>서울의 하늘을 읽고 있어요</p>
      </main>
    );
  }

  const today = data.today;
  const tomorrow = data.tomorrow;
  const observedAt = today.observed_at ? new Date(today.observed_at) : null;
  const predictedDate = new Date(`${tomorrow.predicted_for_date}T12:00:00+09:00`);
  const delta = tomorrow.predicted_avg_temperature - tomorrow.observed_avg_temperature;

  return (
    <div className="app-shell">
      <header className="topbar">
        <a className="brand" href="/" aria-label="하늘결 홈">
          <span className="brand__symbol">ㅎ</span>
          <span><strong>하늘결</strong><small>AI 기온 예측</small></span>
        </a>
        <div className="location">
          <span className="location__dot" />
          <span>{data.station_name} · ASOS {today.station_id}</span>
        </div>
        <button className="refresh-button" onClick={() => void mutate()} type="button">새로고침</button>
      </header>

      <main className="dashboard">
        <section className="today-panel panel">
          <div className="section-heading">
            <div>
              <span className="eyebrow">TODAY</span>
              <h1>오늘의 날씨</h1>
              <p>{dateFormatter.format(new Date())} · {observedAt ? `${timeFormatter.format(observedAt)} 기준` : "관측 대기"}</p>
            </div>
            <span className="live-badge"><i /> 실시간 관측</span>
          </div>

          <div className="current-weather">
            <WeatherIcon cloudAmount={today.cloud_amount} />
            <div className="current-weather__temperature">
              <strong>{today.temperature === null ? "—" : today.temperature.toFixed(1)}</strong><span>°C</span>
              <p>최저 {formatValue(today.min_temperature, "°", 1)} · 최고 {formatValue(today.max_temperature, "°", 1)}</p>
            </div>
          </div>

          <div className="metric-grid">
            <Metric label="습도" value={formatValue(today.humidity, "%")} hint="상대습도" />
            <Metric label="강수" value={formatValue(today.precipitation, " mm", 1)} hint="오늘 누적" />
            <Metric label="바람" value={formatValue(today.wind_speed, " m/s", 1)} hint="현재 풍속" />
            <Metric label="기압" value={formatValue(today.local_pressure, " hPa", 1)} hint="현지기압" />
          </div>

          <div className="subsection-heading">
            <div><span className="eyebrow">HOURLY</span><h2>시간별 관측</h2></div>
            <span>기온 · 습도</span>
          </div>
          <HourlyStrip items={today.hourly} />
          <VariableTable variables={today.variables} />
        </section>

        <aside className="tomorrow-panel panel">
          <div className="prediction-orb prediction-orb--one" />
          <div className="prediction-orb prediction-orb--two" />
          <div className="section-heading section-heading--light">
            <div>
              <span className="eyebrow">TOMORROW · AI FORECAST</span>
              <h1>내일 날씨 기온 예측</h1>
              <p>{dateFormatter.format(predictedDate)}</p>
            </div>
            <span className="ai-badge">AI</span>
          </div>

          <div className="forecast-hero">
            <WeatherIcon cloudAmount={today.cloud_amount} />
            <p>예상 평균기온</p>
            <div><strong>{tomorrow.predicted_avg_temperature.toFixed(1)}</strong><span>°C</span></div>
            <span className={`temperature-change ${delta < 0 ? "temperature-change--down" : ""}`}>
              관측 기준 대비 {delta >= 0 ? "+" : ""}{delta.toFixed(1)}°
            </span>
          </div>

          <div className="model-card">
            <div><span>모델</span><strong>CatBoost × LightGBM</strong></div>
            <div><span>테스트 MAE</span><strong>{tomorrow.model_test_mae?.toFixed(2) ?? "—"}°C</strong></div>
            <div><span>입력 기준일</span><strong>{tomorrow.observation_date}</strong></div>
          </div>

          <div className="prediction-note">
            <span>i</span>
            <p>기상청 ASOS 일자료와 최근 14일 흐름을 사용한 AI 예측입니다. 실제 기온은 기상 변화에 따라 달라질 수 있습니다.</p>
          </div>

          <div className="history">
            <div className="subsection-heading subsection-heading--light">
              <div><span className="eyebrow">HISTORY</span><h2>최근 예측</h2></div>
            </div>
            {data.recent_predictions.length === 0 ? (
              <p className="history__empty">첫 예측이 저장됐습니다. 날짜가 쌓이면 여기에 표시됩니다.</p>
            ) : (
              <ul>
                {data.recent_predictions.map((item) => (
                  <li key={`${item.predicted_for_date}-${item.model_version}`}>
                    <time>{item.predicted_for_date}</time>
                    <strong>{item.predicted_avg_temperature.toFixed(1)}°</strong>
                    <span>{item.actual_avg_temperature === null ? "관측 대기" : `실제 ${item.actual_avg_temperature.toFixed(1)}°`}</span>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </aside>
      </main>

      <footer>
        <span>DATA · 기상청 ASOS</span>
        <span>MODEL · {tomorrow.model_version}</span>
      </footer>
    </div>
  );
}

