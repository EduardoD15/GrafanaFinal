CREATE SCHEMA IF NOT EXISTS bronze;
CREATE SCHEMA IF NOT EXISTS silver;
CREATE SCHEMA IF NOT EXISTS gold;

-- Bronze: copia de la fuente tal como llega (todas las columnas de la lectura en un JSONB)
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

-- Silver: lecturas tipadas, con la regla de calidad aplicada y sin agregar
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
ALTER TABLE silver.measurements ADD COLUMN IF NOT EXISTS measurement_date DATE;
ALTER TABLE silver.measurements ADD COLUMN IF NOT EXISTS line_power_w DOUBLE PRECISION;
ALTER TABLE silver.measurements ADD COLUMN IF NOT EXISTS line_voltage_v DOUBLE PRECISION;
ALTER TABLE silver.measurements ADD COLUMN IF NOT EXISTS line_freq_hz DOUBLE PRECISION;
ALTER TABLE silver.measurements ADD COLUMN IF NOT EXISTS battery_power_w DOUBLE PRECISION;

CREATE INDEX IF NOT EXISTS idx_silver_valid_time
    ON silver.measurements (measured_at, inverter) WHERE is_valid;
CREATE INDEX IF NOT EXISTS idx_silver_inverter_time
    ON silver.measurements (inverter, measured_at) WHERE NOT is_simulated;

CREATE TABLE IF NOT EXISTS silver.context (
    id BIGINT PRIMARY KEY,
    recorded TIMESTAMPTZ NOT NULL,
    starts TIMESTAMPTZ,
    ends TIMESTAMPTZ,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb
);

-- Gold: dimensiones
CREATE TABLE IF NOT EXISTS gold.dim_date (
    date_key INTEGER PRIMARY KEY,
    calendar_date DATE NOT NULL UNIQUE,
    year SMALLINT NOT NULL,
    quarter SMALLINT NOT NULL,
    month SMALLINT NOT NULL,
    day SMALLINT NOT NULL,
    day_of_week SMALLINT NOT NULL
);
ALTER TABLE gold.dim_date ADD COLUMN IF NOT EXISTS year_month TEXT;
ALTER TABLE gold.dim_date ADD COLUMN IF NOT EXISTS month_name TEXT;
ALTER TABLE gold.dim_date ADD COLUMN IF NOT EXISTS day_name TEXT;
ALTER TABLE gold.dim_date ADD COLUMN IF NOT EXISTS iso_week SMALLINT;
ALTER TABLE gold.dim_date ADD COLUMN IF NOT EXISTS is_weekend BOOLEAN;
ALTER TABLE gold.dim_date ADD COLUMN IF NOT EXISTS is_historical BOOLEAN NOT NULL DEFAULT FALSE;

CREATE TABLE IF NOT EXISTS gold.dim_time (
    time_key SMALLINT PRIMARY KEY,
    hour SMALLINT NOT NULL,
    minute SMALLINT NOT NULL,
    quarter_hour SMALLINT NOT NULL,
    time_label TEXT NOT NULL
);
ALTER TABLE gold.dim_time ADD COLUMN IF NOT EXISTS day_period TEXT;

INSERT INTO gold.dim_time (time_key, hour, minute, quarter_hour, time_label, day_period)
SELECT m, m / 60, m % 60, m / 15,
       lpad((m / 60)::text, 2, '0') || ':' || lpad((m % 60)::text, 2, '0'),
       CASE WHEN m / 60 BETWEEN 6 AND 8 THEN 'amanecer'
            WHEN m / 60 BETWEEN 9 AND 15 THEN 'dia'
            WHEN m / 60 BETWEEN 16 AND 18 THEN 'atardecer'
            ELSE 'noche' END
FROM generate_series(0, 1439) AS m
ON CONFLICT (time_key) DO UPDATE SET day_period = EXCLUDED.day_period;

CREATE TABLE IF NOT EXISTS gold.dim_inverter (
    inverter_id SMALLINT PRIMARY KEY,
    inverter_name TEXT NOT NULL,
    rated_power_kw NUMERIC(8, 3),
    pv_capacity_kwp NUMERIC(8, 3),
    source_system TEXT NOT NULL,
    valid_from DATE NOT NULL DEFAULT DATE '2025-07-12',
    valid_to DATE,
    is_current BOOLEAN NOT NULL DEFAULT TRUE
);
ALTER TABLE gold.dim_inverter ALTER COLUMN rated_power_kw DROP NOT NULL;
ALTER TABLE gold.dim_inverter ALTER COLUMN pv_capacity_kwp DROP NOT NULL;
ALTER TABLE gold.dim_inverter ADD COLUMN IF NOT EXISTS observed_pv1_peak_w DOUBLE PRECISION;
ALTER TABLE gold.dim_inverter ADD COLUMN IF NOT EXISTS observed_output_peak_w DOUBLE PRECISION;

-- Gold: lecturas (grano: una lectura valida por inversor; energia hasta la lectura siguiente)
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
ALTER TABLE gold.fact_energy_reading ADD COLUMN IF NOT EXISTS interval_seconds DOUBLE PRECISION;
ALTER TABLE gold.fact_energy_reading ADD COLUMN IF NOT EXISTS line_power_w DOUBLE PRECISION;
ALTER TABLE gold.fact_energy_reading ADD COLUMN IF NOT EXISTS battery_power_w DOUBLE PRECISION;
ALTER TABLE gold.fact_energy_reading ADD COLUMN IF NOT EXISTS grid_present BOOLEAN;
ALTER TABLE gold.fact_energy_reading ADD COLUMN IF NOT EXISTS grid_import_kwh_interval DOUBLE PRECISION;
ALTER TABLE gold.fact_energy_reading ADD COLUMN IF NOT EXISTS battery_discharge_kwh_interval DOUBLE PRECISION;
ALTER TABLE gold.fact_energy_reading ADD COLUMN IF NOT EXISTS battery_charge_kwh_interval DOUBLE PRECISION;

CREATE INDEX IF NOT EXISTS idx_fact_date_inverter
    ON gold.fact_energy_reading (date_key, inverter_id);
CREATE INDEX IF NOT EXISTS idx_fact_inverter_time
    ON gold.fact_energy_reading (inverter_id, measured_at DESC);
CREATE INDEX IF NOT EXISTS idx_fact_simulated_time
    ON gold.fact_energy_reading (measured_at DESC) WHERE is_simulated;

-- Gold: resumen por hora local (lo que consume Power BI; unas 14 mil filas en vez de 3 millones)
CREATE TABLE IF NOT EXISTS gold.fact_energy_hour (
    hour_start TIMESTAMPTZ NOT NULL,
    date_key INTEGER NOT NULL REFERENCES gold.dim_date(date_key),
    time_key SMALLINT NOT NULL REFERENCES gold.dim_time(time_key),
    inverter_id SMALLINT NOT NULL REFERENCES gold.dim_inverter(inverter_id),
    solar_kwh DOUBLE PRECISION,
    load_kwh DOUBLE PRECISION,
    grid_import_kwh DOUBLE PRECISION,
    battery_discharge_kwh DOUBLE PRECISION,
    battery_charge_kwh DOUBLE PRECISION,
    co2_avoided_kg DOUBLE PRECISION,
    avg_soc_pct DOUBLE PRECISION,
    min_soc_pct DOUBLE PRECISION,
    max_pv_w DOUBLE PRECISION,
    readings INTEGER NOT NULL,
    covered_seconds DOUBLE PRECISION NOT NULL,
    grid_present_ratio DOUBLE PRECISION,
    emission_factor_kg_kwh DOUBLE PRECISION NOT NULL,
    PRIMARY KEY (hour_start, inverter_id)
);
CREATE INDEX IF NOT EXISTS idx_hour_date_inverter
    ON gold.fact_energy_hour (date_key, inverter_id);

-- Gold: calidad y cobertura por dia local (grano: inversor y dia)
CREATE TABLE IF NOT EXISTS gold.fact_quality_day (
    date_key INTEGER NOT NULL REFERENCES gold.dim_date(date_key),
    inverter_id SMALLINT NOT NULL REFERENCES gold.dim_inverter(inverter_id),
    rows_received INTEGER NOT NULL,
    rows_valid INTEGER NOT NULL,
    rows_empty INTEGER NOT NULL,
    rows_other_invalid INTEGER NOT NULL,
    covered_seconds DOUBLE PRECISION NOT NULL,
    expected_seconds DOUBLE PRECISION NOT NULL,
    max_gap_seconds DOUBLE PRECISION,
    gaps_over_limit INTEGER NOT NULL,
    PRIMARY KEY (date_key, inverter_id)
);

-- Gold: eventos (grano: un evento de contexto o un episodio detectado en las lecturas)
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
ALTER TABLE gold.fact_event ADD COLUMN IF NOT EXISTS ended_at TIMESTAMPTZ;
ALTER TABLE gold.fact_event ADD COLUMN IF NOT EXISTS duration_seconds DOUBLE PRECISION;
ALTER TABLE gold.fact_event ADD COLUMN IF NOT EXISTS context_type TEXT;

CREATE INDEX IF NOT EXISTS idx_event_time_type
    ON gold.fact_event (occurred_at DESC, event_type);
CREATE INDEX IF NOT EXISTS idx_event_metadata_gin
    ON gold.fact_event USING GIN (metadata);
CREATE INDEX IF NOT EXISTS idx_event_context_type
    ON gold.fact_event (context_type) WHERE context_type IS NOT NULL;

-- Vistas para Power BI: hora local del sitio (UTC+8) y eventos sin los de la simulacion
CREATE OR REPLACE VIEW gold.v_energy_hour AS
SELECT (hour_start AT TIME ZONE 'UTC') + interval '8 hours' AS hour_local,
       date_key, time_key, inverter_id, solar_kwh, load_kwh, grid_import_kwh,
       battery_discharge_kwh, battery_charge_kwh, co2_avoided_kg, avg_soc_pct,
       min_soc_pct, max_pv_w, readings, covered_seconds, grid_present_ratio
FROM gold.fact_energy_hour;

CREATE OR REPLACE VIEW gold.v_event AS
SELECT event_id,
       (occurred_at AT TIME ZONE 'UTC') + interval '8 hours' AS occurred_local,
       (ended_at AT TIME ZONE 'UTC') + interval '8 hours' AS ended_local,
       date_key, time_key, inverter_id, event_type, severity, context_source,
       context_type, duration_seconds
FROM gold.fact_event
WHERE context_source IS DISTINCT FROM 'simulator' AND context_source IS DISTINCT FROM 'simulator_demo';

-- Rol de solo lectura para Power BI y Grafana: ve Gold y nada de Bronze ni Silver
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'bi_reader') THEN
        CREATE ROLE bi_reader NOLOGIN;
    END IF;
END
$$;
GRANT USAGE ON SCHEMA gold TO bi_reader;
GRANT SELECT ON ALL TABLES IN SCHEMA gold TO bi_reader;
ALTER DEFAULT PRIVILEGES IN SCHEMA gold GRANT SELECT ON TABLES TO bi_reader;

COMMENT ON TABLE gold.fact_energy_reading IS
    'Grano: una lectura valida por inversor y marca UTC. La energia se integra (trapecio) hasta la lectura siguiente del mismo inversor si el hueco no pasa de MAX_INTERVAL_SECONDS.';
COMMENT ON COLUMN gold.fact_energy_reading.pv_power_w IS
    'Potencia del canal PV1 (pv1power). Es solo un canal del inversor; no es la planta completa.';
COMMENT ON COLUMN gold.fact_energy_reading.load_power_w IS
    'Potencia activa de salida del inversor (outw_a), W; es la carga que alimenta el inversor.';
COMMENT ON COLUMN gold.fact_energy_reading.grid_power_w IS
    'Canal gridpowerw_a, sin semantica confirmada en el diccionario; no se usa en ningun KPI.';
COMMENT ON COLUMN gold.fact_energy_reading.line_power_w IS
    'Potencia activa tomada de la red (linepowerw_a), W; base de la energia importada.';
COMMENT ON COLUMN gold.fact_energy_reading.battery_power_w IS
    'battvolt x battcurr, W; positivo descarga y negativo carga, segun el diccionario de battcurr.';
COMMENT ON COLUMN gold.fact_energy_reading.co2_avoided_kg_interval IS
    'Estimacion: solar_kwh_interval x factor del SIN configurado; no mide emisiones.';
COMMENT ON TABLE gold.fact_energy_hour IS
    'Grano: inversor y hora local del sitio (UTC+8). Solo lecturas historicas validas; covered_seconds es el tiempo cubierto por intervalos continuos.';
COMMENT ON TABLE gold.fact_quality_day IS
    'Grano: inversor y dia local. Filas recibidas, validas y vacias, y segundos cubiertos por lecturas continuas frente a los esperados dentro del periodo observado.';
COMMENT ON TABLE gold.fact_event IS
    'Grano: un registro de contexto o un episodio derivado de las lecturas (bateria baja, red ausente, hueco de datos).';
