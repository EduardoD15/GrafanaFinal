-- Energia diaria por inversor y CO2 evitado estimado.
SELECT d.calendar_date, f.inverter_id,
       sum(f.solar_kwh_interval) AS solar_kwh,
       sum(f.load_kwh_interval) AS load_phase_a_kwh,
       sum(f.grid_kwh_interval) AS grid_import_kwh,
       sum(f.co2_avoided_kg_interval) AS co2_avoided_kg
FROM gold.fact_energy_reading AS f
JOIN gold.dim_date AS d USING (date_key)
WHERE NOT f.is_simulated
GROUP BY d.calendar_date, f.inverter_id
ORDER BY d.calendar_date, f.inverter_id;

-- Promedio movil de siete dias para comparar energia solar.
WITH daily AS (
    SELECT d.calendar_date, f.inverter_id,
           sum(f.solar_kwh_interval) AS solar_kwh
    FROM gold.fact_energy_reading AS f
    JOIN gold.dim_date AS d USING (date_key)
    WHERE NOT f.is_simulated
    GROUP BY d.calendar_date, f.inverter_id
)
SELECT calendar_date, inverter_id, solar_kwh,
       avg(solar_kwh) OVER (
           PARTITION BY inverter_id ORDER BY calendar_date
           ROWS BETWEEN 6 PRECEDING AND CURRENT ROW
       ) AS solar_kwh_7d_avg
FROM daily
ORDER BY inverter_id, calendar_date;

-- Comparativo mensual frente al mes anterior usando LAG.
WITH monthly AS (
    SELECT d.year, d.month, f.inverter_id,
           sum(f.solar_kwh_interval) AS solar_kwh,
           sum(f.grid_kwh_interval) AS grid_kwh
    FROM gold.fact_energy_reading AS f
    JOIN gold.dim_date AS d USING (date_key)
    WHERE NOT f.is_simulated
    GROUP BY d.year, d.month, f.inverter_id
)
SELECT *, lag(solar_kwh) OVER (
    PARTITION BY inverter_id ORDER BY year, month
) AS previous_month_solar_kwh
FROM monthly
ORDER BY inverter_id, year, month;

-- Consulta nativa JSONB sobre metadatos de contexto desidentificados.
SELECT context_source, metadata->>'device' AS device, count(*) AS records
FROM gold.fact_event
WHERE event_type = 'context_record'
  AND metadata ? 'source'
GROUP BY context_source, metadata->>'device'
ORDER BY records DESC;

-- Calidad, cobertura y proporción de filas validas por inversor.
SELECT inverter,
       count(*) AS raw_rows,
       count(*) FILTER (WHERE is_valid) AS valid_rows,
       round(100.0 * count(*) FILTER (WHERE is_valid) / nullif(count(*), 0), 2)
           AS valid_percent,
       min(measured_at) AS first_valid_utc,
       max(measured_at) AS last_valid_utc
FROM silver.measurements
GROUP BY inverter
ORDER BY inverter;

-- Disponibilidad estimada por dia/inversor usando la cadencia mediana de 13 s.
WITH daily_coverage AS (
    SELECT measurement_date, inverter,
           count(*) FILTER (WHERE is_valid AND NOT is_simulated) AS valid_rows,
           count(*) FILTER (WHERE NOT is_simulated) AS received_rows
    FROM silver.measurements
    GROUP BY measurement_date, inverter
),
source_bounds AS (
    SELECT inverter, min(measured_at) AS first_seen, max(measured_at) AS last_seen
    FROM silver.measurements
    WHERE NOT is_simulated AND is_valid
    GROUP BY inverter
),
inverter_days AS (
    SELECT d.calendar_date, i.inverter_id,
           1 + floor(extract(epoch FROM (
               least((d.calendar_date::timestamp AT TIME ZONE 'UTC') +
                     interval '1 day', b.last_seen) -
               greatest(d.calendar_date::timestamp AT TIME ZONE 'UTC', b.first_seen)
           )) / 13.0) AS expected_rows
    FROM gold.dim_date AS d
    CROSS JOIN gold.dim_inverter AS i
    JOIN source_bounds AS b ON b.inverter = i.inverter_id
    WHERE d.calendar_date BETWEEN b.first_seen::date AND b.last_seen::date
)
SELECT d.calendar_date AS measurement_date, d.inverter_id AS inverter,
       coalesce(c.received_rows, 0) AS received_rows,
       coalesce(c.valid_rows, 0) AS valid_rows,
       round(100.0 * coalesce(c.valid_rows, 0) /
             nullif(coalesce(c.received_rows, 0), 0), 2) AS valid_percent,
       round(100.0 * least(1.0, coalesce(c.valid_rows, 0) /
             nullif(d.expected_rows, 0)), 2) AS estimated_availability_percent
FROM inverter_days AS d
LEFT JOIN daily_coverage AS c
  ON c.measurement_date = d.calendar_date AND c.inverter = d.inverter_id
ORDER BY d.calendar_date, d.inverter_id;
