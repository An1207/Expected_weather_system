CREATE DATABASE IF NOT EXISTS weather_prediction
  CHARACTER SET utf8mb4
  COLLATE utf8mb4_unicode_ci;

USE weather_prediction;

CREATE TABLE IF NOT EXISTS model_versions (
    id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    model_version VARCHAR(100) NOT NULL UNIQUE,
    model_type VARCHAR(100) NOT NULL,
    station_id VARCHAR(10) NOT NULL,
    target_name VARCHAR(100) NOT NULL,
    trained_from DATE NULL,
    trained_to DATE NULL,
    validation_mae DECIMAL(8, 4) NULL,
    test_mae DECIMAL(8, 4) NULL,
    artifact_uri VARCHAR(500) NOT NULL,
    metadata_json JSON NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_model_version (model_version)
);

CREATE TABLE IF NOT EXISTS temperature_predictions (
    id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    station_id VARCHAR(10) NOT NULL,
    observation_date DATE NOT NULL,
    predicted_for_date DATE NOT NULL,
    observed_avg_temperature DECIMAL(6, 2) NOT NULL,
    predicted_avg_temperature DECIMAL(6, 2) NOT NULL,
    actual_avg_temperature DECIMAL(6, 2) NULL,
    model_version VARCHAR(100) NOT NULL,
    source VARCHAR(30) NOT NULL DEFAULT 'KMA_ASOS_DAILY',
    input_snapshot JSON NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    CONSTRAINT fk_prediction_model_version
      FOREIGN KEY (model_version) REFERENCES model_versions(model_version),
    CONSTRAINT uq_prediction_station_date_model
      UNIQUE (station_id, predicted_for_date, model_version),
    INDEX idx_prediction_date (predicted_for_date),
    INDEX idx_observation_date (observation_date)
);

CREATE TABLE IF NOT EXISTS pipeline_runs (
    id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    run_type VARCHAR(30) NOT NULL,
    status VARCHAR(30) NOT NULL,
    model_version VARCHAR(100) NULL,
    started_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    finished_at DATETIME NULL,
    message TEXT NULL,
    INDEX idx_pipeline_started_at (started_at)
);

