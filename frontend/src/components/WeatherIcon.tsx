type WeatherIconProps = {
  cloudAmount: number | null;
  size?: "small" | "large";
};

export function WeatherIcon({ cloudAmount, size = "large" }: WeatherIconProps) {
  const cloudy = cloudAmount !== null && cloudAmount >= 6;
  return (
    <div className={`weather-icon weather-icon--${size}`} aria-label={cloudy ? "흐림" : "맑음"}>
      <span className="weather-icon__sun" />
      <span className={`weather-icon__cloud ${cloudy ? "weather-icon__cloud--dense" : ""}`} />
    </div>
  );
}

