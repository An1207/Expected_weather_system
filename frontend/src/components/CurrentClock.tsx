import { useEffect, useState } from "react";

const dateFormat = new Intl.DateTimeFormat("ko-KR", {
  year: "numeric", month: "long", day: "numeric", weekday: "long", timeZone: "Asia/Seoul",
});
const timeFormat = new Intl.DateTimeFormat("ko-KR", {
  hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false, timeZone: "Asia/Seoul",
});

export function CurrentClock() {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const timer = window.setInterval(() => setNow(new Date()), 1000);
    return () => window.clearInterval(timer);
  }, []);
  return <div className="current-clock"><span>{dateFormat.format(now)}</span><time dateTime={now.toISOString()}>{timeFormat.format(now)}</time><small>대한민국 표준시 · KST</small></div>;
}
