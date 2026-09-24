type MetricProps = {
  label: string;
  value: string;
  hint?: string;
};

export function Metric({ label, value, hint }: MetricProps) {
  return (
    <div className="metric">
      <span className="metric__label">{label}</span>
      <strong className="metric__value">{value}</strong>
      {hint ? <span className="metric__hint">{hint}</span> : null}
    </div>
  );
}

