CREATE SCHEMA IF NOT EXISTS bronze;
CREATE SCHEMA IF NOT EXISTS silver;
CREATE SCHEMA IF NOT EXISTS gold;

CREATE TABLE IF NOT EXISTS bronze.measurements_raw (
    read_ts DOUBLE PRECISION NOT NULL,
    inverter SMALLINT NOT NULL,
    payload JSONB NOT NULL,
    is_simulated BOOLEAN NOT NULL DEFAULT FALSE,
    ingested_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (read_ts, inverter)
);

CREATE TABLE IF NOT EXISTS bronze.context_raw (
    id BIGINT PRIMARY KEY,
    recorded DOUBLE PRECISION,
    starts DOUBLE PRECISION,
    ends DOUBLE PRECISION,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    ingested_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS silver.measurements (
    read_ts DOUBLE PRECISION NOT NULL,
    inverter SMALLINT NOT NULL,
    measured_at TIMESTAMPTZ,
    measurement_date DATE,
    pv_power_w DOUBLE PRECISION,
    load_power_w DOUBLE PRECISION,
    grid_power_w DOUBLE PRECISION,
    battery_soc_pct DOUBLE PRECISION,
    battery_voltage_v DOUBLE PRECISION,
    battery_current_a DOUBLE PRECISION,
    is_valid BOOLEAN NOT NULL,
    invalid_reason TEXT,
    is_simulated BOOLEAN NOT NULL DEFAULT FALSE,
    PRIMARY KEY (read_ts, inverter)
);

CREATE INDEX IF NOT EXISTS idx_silver_valid_time
    ON silver.measurements (measured_at, inverter) WHERE is_valid;

CREATE TABLE IF NOT EXISTS silver.context (
    id BIGINT PRIMARY KEY,
    recorded TIMESTAMPTZ NOT NULL,
    starts TIMESTAMPTZ,
    ends TIMESTAMPTZ,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS gold.dim_date (
    date_key INTEGER PRIMARY KEY,
    calendar_date DATE NOT NULL UNIQUE,
    year SMALLINT NOT NULL,
    quarter SMALLINT NOT NULL,
    month SMALLINT NOT NULL,
    day SMALLINT NOT NULL,
    day_of_week SMALLINT NOT NULL
);

CREATE TABLE IF NOT EXISTS gold.dim_time (
    time_key SMALLINT PRIMARY KEY,
    hour SMALLINT NOT NULL,
    minute SMALLINT NOT NULL,
    quarter_hour SMALLINT NOT NULL,
    time_label TEXT NOT NULL
);

INSERT INTO gold.dim_time (time_key, hour, minute, quarter_hour, time_label)
SELECT minute_of_day, minute_of_day / 60, minute_of_day % 60,
       minute_of_day / 15,
       lpad((minute_of_day / 60)::text, 2, '0') || ':' ||
       lpad((minute_of_day % 60)::text, 2, '0')
FROM generate_series(0, 1439) AS minute_of_day
ON CONFLICT (time_key) DO NOTHING;

CREATE TABLE IF NOT EXISTS gold.dim_inverter (
    inverter_id SMALLINT PRIMARY KEY,
    inverter_name TEXT NOT NULL,
    rated_power_kw NUMERIC(8, 3) NOT NULL,
    pv_capacity_kwp NUMERIC(8, 3) NOT NULL,
    source_system TEXT NOT NULL,
    valid_from DATE NOT NULL DEFAULT DATE '2025-01-01',
    valid_to DATE,
    is_current BOOLEAN NOT NULL DEFAULT TRUE
);

CREATE TABLE IF NOT EXISTS gold.fact_energy_reading (
    measured_at TIMESTAMPTZ NOT NULL,
    date_key INTEGER NOT NULL REFERENCES gold.dim_date(date_key),
    time_key SMALLINT NOT NULL REFERENCES gold.dim_time(time_key),
    inverter_id SMALLINT NOT NULL REFERENCES gold.dim_inverter(inverter_id),
    pv_power_w DOUBLE PRECISION,
    load_power_w DOUBLE PRECISION,
    grid_power_w DOUBLE PRECISION,
    battery_soc_pct DOUBLE PRECISION,
    solar_kwh_interval DOUBLE PRECISION,
    load_kwh_interval DOUBLE PRECISION,
    grid_kwh_interval DOUBLE PRECISION,
    co2_avoided_kg_interval DOUBLE PRECISION,
    emission_factor_kg_kwh DOUBLE PRECISION NOT NULL,
    is_simulated BOOLEAN NOT NULL DEFAULT FALSE,
    PRIMARY KEY (measured_at, inverter_id)
);

CREATE INDEX IF NOT EXISTS idx_fact_date_inverter
    ON gold.fact_energy_reading (date_key, inverter_id);
CREATE INDEX IF NOT EXISTS idx_fact_inverter_time
    ON gold.fact_energy_reading (inverter_id, measured_at DESC);
CREATE INDEX IF NOT EXISTS idx_fact_simulated_time
    ON gold.fact_energy_reading (measured_at DESC) WHERE is_simulated;

CREATE TABLE IF NOT EXISTS gold.fact_event (
    event_id TEXT PRIMARY KEY,
    occurred_at TIMESTAMPTZ NOT NULL,
    date_key INTEGER NOT NULL REFERENCES gold.dim_date(date_key),
    time_key SMALLINT NOT NULL REFERENCES gold.dim_time(time_key),
    inverter_id SMALLINT REFERENCES gold.dim_inverter(inverter_id),
    event_type TEXT NOT NULL,
    severity TEXT NOT NULL,
    context_source TEXT,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_event_time_type
    ON gold.fact_event (occurred_at DESC, event_type);
CREATE INDEX IF NOT EXISTS idx_event_metadata_gin
    ON gold.fact_event USING GIN (metadata);

COMMENT ON TABLE gold.fact_energy_reading IS
    'Grano: una lectura valida por inversor y timestamp UTC. Energia estimada hasta la siguiente lectura.';
COMMENT ON COLUMN gold.fact_energy_reading.load_power_w IS
    'Potencia de salida fase A (OpenCEM outw_a); no equivale a carga total trifasica.';
COMMENT ON COLUMN gold.fact_energy_reading.co2_avoided_kg_interval IS
    'Estimacion solar_kwh_interval por factor configurable del SIN; no mide energia exportada.';
COMMENT ON TABLE gold.fact_event IS
    'Grano: un evento de contexto o de bateria baja por inversor y timestamp.';
