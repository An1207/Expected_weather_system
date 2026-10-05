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
  calculation: PredictionCalculation | null;
};

export type InputEvidence = {
  name: string;
  source_key?: string | null;
  raw_value?: number | null;
  model_value: number | null;
  treatment: string;
};

export type PredictionCalculation = {
  history_start: string;
  history_end: string;
  source_row_count: number;
  current_avg_temperature: number;
  catboost_residual: number;
  lightgbm_residual: number;
  catboost_prediction: number;
  lightgbm_prediction: number;
  catboost_weight: number;
  lightgbm_weight: number;
  unrounded_prediction: number;
  base_inputs: InputEvidence[];
  derived_inputs: InputEvidence[];
};

export type ModelEvidence = {
  model_version: string | null;
  base_feature_count: number;
  engineered_feature_count: number;
  model_input_count: number;
  forecast_offset_days: number;
  data_start: string | null;
  data_end: string | null;
  train_end: string | null;
  valid_end: string | null;
  daily_source: string;
  hourly_source: string;
  cache_ttl_seconds: number;
  scores: { model: string; mae: number; rmse: number; r2: number }[];
  latest_training: {
    candidate_version: string;
    compared_model_version: string;
    candidate_mae: number;
    incumbent_mae: number;
    persistence_mae: number;
    train_rows: number;
    valid_rows: number;
    test_rows: number;
    test_start: string;
    test_end: string;
    promotion_approved: boolean;
  } | null;
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
  server_time: string;
  yesterday: TodayWeather | null;
  today: TodayWeather | null;
  tomorrow: Prediction | null;
  errors: Record<string, string>;
  recent_predictions: PredictionHistory[];
  model_evidence: ModelEvidence | null;
};

