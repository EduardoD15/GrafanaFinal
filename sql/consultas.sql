-- 1. Energia diaria por inversor (dia local del sitio) y CO2 evitado estimado
SELECT d.calendar_date, h.inverter_id,
       round(sum(h.solar_kwh)::numeric, 2)        AS solar_kwh,
       round(sum(h.load_kwh)::numeric, 2)         AS salida_kwh,
       round(sum(h.grid_import_kwh)::numeric, 2)  AS red_kwh,
       round(sum(h.co2_avoided_kg)::numeric, 2)   AS co2_kg
FROM gold.fact_energy_hour AS h
JOIN gold.dim_date AS d USING (date_key)
GROUP BY d.calendar_date, h.inverter_id
ORDER BY d.calendar_date, h.inverter_id;

-- 2. Promedio movil de siete dias de la energia solar
WITH diaria AS (
    SELECT d.calendar_date, h.inverter_id, sum(h.solar_kwh) AS solar_kwh
    FROM gold.fact_energy_hour AS h
    JOIN gold.dim_date AS d USING (date_key)
    GROUP BY d.calendar_date, h.inverter_id
)
SELECT calendar_date, inverter_id, round(solar_kwh::numeric, 2) AS solar_kwh,
       round(avg(solar_kwh) OVER (
           PARTITION BY inverter_id ORDER BY calendar_date
           ROWS BETWEEN 6 PRECEDING AND CURRENT ROW)::numeric, 2) AS promedio_7_dias
FROM diaria
ORDER BY inverter_id, calendar_date;

-- 3. Comparativo mensual contra el mes anterior (LAG)
WITH mensual AS (
    SELECT d.year_month, h.inverter_id,
           sum(h.solar_kwh) AS solar_kwh, sum(h.grid_import_kwh) AS red_kwh
    FROM gold.fact_energy_hour AS h
    JOIN gold.dim_date AS d USING (date_key)
    GROUP BY d.year_month, h.inverter_id
)
SELECT year_month, inverter_id, round(solar_kwh::numeric, 1) AS solar_kwh,
       round(lag(solar_kwh) OVER w::numeric, 1) AS solar_mes_anterior,
       round((100 * (solar_kwh / nullif(lag(solar_kwh) OVER w, 0) - 1))::numeric, 1) AS variacion_pct,
       round(red_kwh::numeric, 1) AS red_kwh
FROM mensual
WINDOW w AS (PARTITION BY inverter_id ORDER BY year_month)
ORDER BY inverter_id, year_month;

-- 4. Los cinco dias con mas energia solar de cada inversor (RANK)
WITH diaria AS (
    SELECT d.calendar_date, h.inverter_id, sum(h.solar_kwh) AS solar_kwh
    FROM gold.fact_energy_hour AS h
    JOIN gold.dim_date AS d USING (date_key)
    GROUP BY d.calendar_date, h.inverter_id
    HAVING sum(h.solar_kwh) IS NOT NULL
), clasificada AS (
    SELECT *, rank() OVER (PARTITION BY inverter_id ORDER BY solar_kwh DESC) AS puesto
    FROM diaria
)
SELECT inverter_id, puesto, calendar_date, round(solar_kwh::numeric, 2) AS solar_kwh
FROM clasificada
WHERE puesto <= 5
ORDER BY inverter_id, puesto;

-- 5. Perfil horario: a que horas llega el sol y a que horas se compra a la red (hora local)
SELECT t.hour AS hora_local,
       round(avg(h.solar_kwh)::numeric, 3)       AS solar_kwh_por_hora,
       round(avg(h.grid_import_kwh)::numeric, 3) AS red_kwh_por_hora,
       round((100 * sum(h.grid_import_kwh) / nullif(sum(h.grid_import_kwh) + sum(h.solar_kwh), 0))::numeric, 1)
           AS dependencia_red_pct
FROM gold.fact_energy_hour AS h
JOIN gold.dim_time AS t USING (time_key)
GROUP BY t.hour
ORDER BY t.hour;

-- 6. CO2 evitado acumulado en el periodo (suma acumulada)
WITH diaria AS (
    SELECT d.calendar_date, sum(h.co2_avoided_kg) AS co2_kg
    FROM gold.fact_energy_hour AS h
    JOIN gold.dim_date AS d USING (date_key)
    GROUP BY d.calendar_date
)
SELECT calendar_date, round(co2_kg::numeric, 2) AS co2_dia_kg,
       round(sum(co2_kg) OVER (ORDER BY calendar_date)::numeric, 1) AS co2_acumulado_kg
FROM diaria
ORDER BY calendar_date;

-- 7. Distribucion del estado de carga por inversor (base de la alerta de bateria baja)
SELECT inverter_id,
       round(percentile_cont(0.01) WITHIN GROUP (ORDER BY battery_soc_pct)::numeric, 1) AS p01,
       round(percentile_cont(0.05) WITHIN GROUP (ORDER BY battery_soc_pct)::numeric, 1) AS p05,
       round(percentile_cont(0.50) WITHIN GROUP (ORDER BY battery_soc_pct)::numeric, 1) AS mediana,
       round(100.0 * count(*) FILTER (WHERE battery_soc_pct < 20) / count(*), 2)        AS pct_lecturas_bajo_20
FROM gold.fact_energy_reading
WHERE NOT is_simulated AND battery_soc_pct IS NOT NULL
GROUP BY inverter_id
ORDER BY inverter_id;

-- 8. Disponibilidad del registro por mes (tiempo cubierto por lecturas continuas)
SELECT d.year_month, q.inverter_id,
       round((100 * sum(q.covered_seconds) / nullif(sum(q.expected_seconds), 0))::numeric, 2) AS disponibilidad_pct,
       sum(q.gaps_over_limit) AS huecos_mayores_al_limite,
       round((max(q.max_gap_seconds) / 3600)::numeric, 1) AS hueco_maximo_horas
FROM gold.fact_quality_day AS q
JOIN gold.dim_date AS d USING (date_key)
GROUP BY d.year_month, q.inverter_id
ORDER BY d.year_month, q.inverter_id;

-- 9. Lecturas rechazadas por regla de calidad (conteos antes y despues)
SELECT inverter, coalesce(invalid_reason, 'valida') AS resultado, count(*) AS filas,
       round(100.0 * count(*) / sum(count(*)) OVER (PARTITION BY inverter), 3) AS pct_del_inversor
FROM silver.measurements
WHERE NOT is_simulated
GROUP BY inverter, invalid_reason
ORDER BY inverter, filas DESC;

-- 10. JSON nativo: tipos de contexto mas frecuentes y su duracion media (?, ->>, @>)
SELECT metadata->>'context_type' AS tipo_contexto, count(*) AS registros,
       round((avg(duration_seconds) / 3600)::numeric, 2) AS horas_promedio
FROM gold.fact_event
WHERE event_type = 'context_record' AND metadata ? 'context_type'
GROUP BY 1
ORDER BY registros DESC
LIMIT 12;

-- 11. JSON y ventanas: carga de salida mientras corre un entrenamiento de GPU frente al resto del tiempo
WITH entrenamiento AS (
    SELECT DISTINCT h.inverter_id, h.hour_start
    FROM gold.fact_event AS e
    JOIN gold.fact_energy_hour AS h
      ON h.inverter_id = e.inverter_id
     AND h.hour_start >= date_trunc('hour', e.occurred_at)
     AND h.hour_start < e.ended_at
    WHERE e.metadata @> '{"context_type": "gpu_cnn_training_workload"}'
)
SELECT h.inverter_id,
       (t.hour_start IS NOT NULL) AS con_entrenamiento_gpu,
       count(*) AS horas,
       round(avg(h.load_kwh)::numeric, 3) AS salida_kwh_por_hora
FROM gold.fact_energy_hour AS h
LEFT JOIN entrenamiento AS t USING (inverter_id, hour_start)
WHERE h.load_kwh IS NOT NULL
GROUP BY h.inverter_id, (t.hour_start IS NOT NULL)
ORDER BY h.inverter_id, con_entrenamiento_gpu;

-- 12. Episodios derivados de las lecturas: horas con la red ausente y con bateria baja por mes
SELECT d.year_month, e.inverter_id, e.event_type,
       count(*) AS episodios,
       round((sum(e.duration_seconds) / 3600)::numeric, 1) AS horas
FROM gold.fact_event AS e
JOIN gold.dim_date AS d USING (date_key)
WHERE e.event_type IN ('grid_absent', 'low_battery_soc') AND e.context_source LIKE 'derived%'
GROUP BY d.year_month, e.inverter_id, e.event_type
ORDER BY d.year_month, e.inverter_id, e.event_type;

-- 13. Dias en que la compra a la red subio mas de 50 % frente al dia anterior (LAG)
WITH diaria AS (
    SELECT d.calendar_date, sum(h.grid_import_kwh) AS red_kwh
    FROM gold.fact_energy_hour AS h
    JOIN gold.dim_date AS d USING (date_key)
    GROUP BY d.calendar_date
), con_anterior AS (
    SELECT calendar_date, red_kwh, lag(red_kwh) OVER (ORDER BY calendar_date) AS red_dia_anterior
    FROM diaria
)
SELECT calendar_date, round(red_kwh::numeric, 1) AS red_kwh, round(red_dia_anterior::numeric, 1) AS red_dia_anterior,
       round((100 * (red_kwh / red_dia_anterior - 1))::numeric, 0) AS variacion_pct
FROM con_anterior
WHERE red_dia_anterior > 5 AND red_kwh > 1.5 * red_dia_anterior
ORDER BY variacion_pct DESC
LIMIT 15;
