import type { HourlyObservation } from "../types";

const timeFormatter = new Intl.DateTimeFormat("ko-KR", {
  hour: "2-digit",
  hour12: false,
  timeZone: "Asia/Seoul",
});

export function HourlyStrip({ items }: { items: HourlyObservation[] }) {
  if (items.length === 0) {
    return <p className="empty-message">오늘 시간별 관측이 아직 제공되지 않았습니다.</p>;
  }

  return (
    <div className="hourly-strip" aria-label="시간별 기온">
      {items.map((item) => (
        <article className="hourly-item" key={item.observed_at}>
          <time dateTime={item.observed_at}>{timeFormatter.format(new Date(item.observed_at))}시</time>
          <span className="hourly-item__dot" />
          <strong>{item.temperature === null ? "—" : `${item.temperature.toFixed(1)}°`}</strong>
          <small>습도 {item.humidity === null ? "—" : `${Math.round(item.humidity)}%`}</small>
        </article>
      ))}
    </div>
  );
}

