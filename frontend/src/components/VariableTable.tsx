const FIELD_LABELS: Record<string, string> = {
  tm: "관측 시각",
  ta: "기온",
  rn: "강수량",
  ws: "풍속",
  wd: "풍향",
  hm: "상대습도",
  pv: "증기압",
  pa: "현지기압",
  ps: "해면기압",
  ss: "일조",
  icsr: "일사량",
  dsnw: "적설",
  dc10Tca: "전운량",
  dc10LmcsCa: "중하층운량",
  ts: "지면온도",
};

export function VariableTable({ variables }: { variables: Record<string, string | number | null> }) {
  const entries = Object.entries(variables);
  if (entries.length === 0) return null;

  return (
    <details className="variable-details">
      <summary>전체 관측 변수 <span>{entries.length}개</span></summary>
      <div className="variable-grid">
        {entries.map(([key, value]) => (
          <div className="variable-row" key={key}>
            <span>{FIELD_LABELS[key] ?? key}</span>
            <strong>{value === "" || value === null ? "—" : String(value)}</strong>
          </div>
        ))}
      </div>
    </details>
  );
}

