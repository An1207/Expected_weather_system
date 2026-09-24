export type HourlyObservation = {
  observed_at: string;
  temperature: number | null;
  precipitation: number | null;
  humidity: number | null;
  wind_speed: number | null;
  wind_direction: number | null;
  local_pressure: number | null;
  sea_level_pressure: number | null;
  sunshine: number | null;
  solar_radiation: number | null;
  cloud_amount: number | null;
  ground_temperature: number | null;
  raw: Record<string, string | number | null>;
};

export type TodayWeather = {
  station_id: string;
  station_name: string;
  observed_at: string | null;
  temperature: number | null;
  min_temperature: number | null;
  max_temperature: number | null;
  humidity: number | null;
  precipitation: number | null;
  wind_speed: number | null;
  local_pressure: number | null;
  cloud_amount: number | null;
  source: string;
  variables: Record<string, string | number | null>;
  hourly: HourlyObservation[];
};

export type Prediction = {
  station_id: string;
  observation_date: string;
  predicted_for_date: string;
  observed_avg_temperature: number;
  predicted_avg_temperature: number;
  model_version: string;
  model_test_mae: number | null;
  generated_at: string;
};

export type PredictionHistory = {
  predicted_for_date: string;
  predicted_avg_temperature: number;
  actual_avg_temperature: number | null;
  model_version: string;
  created_at: string;
};

export type Dashboard = {
  station_name: string;
  timezone: string;
  today: TodayWeather;
  tomorrow: Prediction;
  recent_predictions: PredictionHistory[];
};

