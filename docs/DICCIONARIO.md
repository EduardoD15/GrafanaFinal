# Diccionario de datos

Llaves, claves foráneas e índices están en `sql/schema.sql` (cada tabla de Gold lleva además un `COMMENT` con su grano). Las marcas de tiempo se guardan en UTC; las fechas y horas de análisis se convierten a hora local del sitio (UTC+8) con la constante `SITE_UTC_OFFSET_HOURS`.

## Unidades de las columnas de origen y su evidencia

Fuente de evidencia: `documentation/column_dictionary.csv` del archivo de soporte de Zenodo (`opencem-v1.0.0-support.zip`), columnas `unit` y `scale_and_sign`. Todas las columnas usadas dicen "already converted; no additional register scaling", es decir, no hay que aplicar factores de escala.

| Columna de origen | Unidad según el diccionario | Significado | Uso en el proyecto |
|---|---|---|---|
| `read_ts` | s desde 1970-01-01 UTC | marca de tiempo Unix fraccionaria en UTC | `measured_at` |
| `pv1power` | W | potencia activa del canal de entrada FV 1 | energía solar (solo PV1) |
| `outw_a` | W | campo procesado, salida fase A | energía de salida del inversor |
| `linepowerw_a` | W | campo procesado, potencia de línea fase A | energía importada de la red |
| `gridpowerw_a` | W | campo procesado, sin descripción funcional | no se usa: el significado no está confirmado |
| `battsoc` | % | estado de carga | SoC |
| `battvolt` | V | tensión en bornes de la batería | potencia de batería |
| `battcurr` | A | corriente con signo: positivo descarga, negativo carga | potencia de batería |
| `linevolta` | V | voltaje de línea | detección de red presente (>= 100 V) |
| `linefreq` | Hz | frecuencia de línea | se conserva, no se usa en KPI |
| `outsumw` | "unitless or component-defined" | sin unidad confirmada | no se usa |

Para los campos procesados (`outw_a`, `linepowerw_a`, `gridpowerw_a`) el diccionario solo confirma la unidad; el significado se toma del nombre del campo y por eso se documentan sus límites en [NEGOCIO_Y_FUENTE.md](NEGOCIO_Y_FUENTE.md).

Nulos: el diccionario establece que NULL significa dato no disponible y que no se imputa. El proyecto respeta esa regla.

## Bronze

| Tabla.columna | Tipo | Llave | Origen o regla |
|---|---|---|---|
| `measurements_raw.read_ts` | double precision | PK compuesta | `analog_measurements.read_ts` |
| `measurements_raw.inverter` | smallint | PK compuesta | `analog_measurements.inverter` |
| `measurements_raw.payload` | jsonb | | todas las columnas de la lectura, sin cambios |
| `measurements_raw.is_simulated` | boolean | | falso para la fuente; verdadero para el simulador |
| `measurements_raw.ingested_at` | timestamptz | | `now()` al cargar |
| `context_raw.id` | bigint | PK | `context.id` |
| `context_raw.recorded/starts/ends` | double precision | | epochs de la fuente |
| `context_raw.metadata` | jsonb | | solo las claves de la lista permitida |

## Silver

| Columna | Tipo | Unidad | Regla |
|---|---|---|---|
| `read_ts`, `inverter` | double precision, smallint | s, id | PK; `inverter` solo 1 o 2 |
| `measured_at` | timestamptz | UTC | `to_timestamp(read_ts)` si está entre los años 2000 y 2100 |
| `measurement_date` | date | día local | fecha en hora del sitio |
| `pv_power_w` | double precision | W | `pv1power` |
| `load_power_w` | double precision | W | `outw_a` |
| `grid_power_w` | double precision | W | `gridpowerw_a`, sin uso |
| `line_power_w` | double precision | W | `linepowerw_a` |
| `line_voltage_v`, `line_freq_hz` | double precision | V, Hz | `linevolta`, `linefreq` |
| `battery_soc_pct` | double precision | % | `battsoc` |
| `battery_voltage_v`, `battery_current_a` | double precision | V, A | `battvolt`, `battcurr` |
| `battery_power_w` | double precision | W | `battvolt x battcurr`; + descarga, - carga |
| `is_valid`, `invalid_reason` | boolean, text | | primera regla de calidad que falla (ver [CALIDAD_Y_ETL.md](CALIDAD_Y_ETL.md)) |
| `is_simulated` | boolean | | marca del simulador |
| `silver.context.metadata` | jsonb | | claves permitidas: `inverter`, `source`, `device`, `context_type`, `task_type`, `llm_estimated_task_effort`, `cpu_cores_active`, `day_type`, `gap_seconds`, `threshold_seconds` |

## Gold: dimensiones

| Tabla.columna | Tipo | Llave | Descripción |
|---|---|---|---|
| `dim_date.date_key` | integer | PK | AAAAMMDD |
| `dim_date.calendar_date` | date | único | día local |
| `year, quarter, month, day, day_of_week, iso_week` | smallint | | jerarquía de calendario (día ISO 1 a 7) |
| `year_month, month_name, day_name` | text | | etiquetas de agrupación y orden |
| `is_weekend, is_historical` | boolean | | `is_historical` es falso para días que solo existen por la simulación |
| `dim_time.time_key` | smallint | PK | minuto del día, 0 a 1439 |
| `hour, minute, quarter_hour, time_label` | smallint, text | | hora, minuto, cuarto de hora y HH:MM |
| `dim_time.day_period` | text | | amanecer (6 a 8 h), día (9 a 15), atardecer (16 a 18), noche |
| `dim_inverter.inverter_id` | smallint | PK | 1 o 2 |
| `inverter_name, source_system` | text | | "Inversor 1" y "Inversor 2"; OpenCEM |
| `rated_power_kw, pv_capacity_kwp` | numeric | | NULL: no están en los datos |
| `observed_pv1_peak_w, observed_output_peak_w` | double precision | W | máximos medidos (3.902 y 1.715 W; 3.933 y 5.539 W) |
| `valid_from, valid_to, is_current` | date, boolean | | vigencia de la versión (ver [MODELO.md](MODELO.md)) |

## Gold: hechos

| Tabla.columna | Tipo | Unidad | Descripción |
|---|---|---|---|
| `fact_energy_reading.measured_at, inverter_id` | timestamptz, smallint | UTC | PK |
| `date_key, time_key` | integer, smallint | | FK a las dimensiones (hora local) |
| `pv_power_w, load_power_w, line_power_w, battery_power_w` | double precision | W | potencias de la lectura |
| `battery_soc_pct` | double precision | % | SoC |
| `grid_present` | boolean | | voltaje de línea >= 100 V |
| `interval_seconds` | double precision | s | tiempo hasta la lectura siguiente; NULL si el hueco supera 900 s |
| `solar_kwh_interval, load_kwh_interval, grid_import_kwh_interval, battery_discharge_kwh_interval, battery_charge_kwh_interval` | double precision | kWh | integral trapezoidal del intervalo |
| `co2_avoided_kg_interval, emission_factor_kg_kwh` | double precision | kg, kg/kWh | energía solar x factor |
| `fact_energy_hour.hour_start` | timestamptz | UTC | inicio de la hora local; PK con `inverter_id` |
| `solar_kwh, load_kwh, grid_import_kwh, battery_discharge_kwh, battery_charge_kwh, co2_avoided_kg` | double precision | kWh, kg | sumas de la hora |
| `avg_soc_pct, min_soc_pct, max_pv_w` | double precision | %, %, W | promedio, mínimo y máximo de la hora |
| `readings, covered_seconds, grid_present_ratio` | integer, double precision | , s, razón | lecturas válidas, segundos cubiertos y proporción con red |
| `fact_quality_day.date_key, inverter_id` | integer, smallint | | PK |
| `rows_received, rows_valid, rows_empty, rows_other_invalid` | integer | filas | todas las lecturas del día, incluidas las rechazadas |
| `covered_seconds, expected_seconds, max_gap_seconds, gaps_over_limit` | double precision, integer | s | cobertura frente a lo esperado entre la primera y la última lectura del inversor |
| `fact_event.event_id` | text | | PK: `context:<id>`, `derived:<tipo>:...` o id del simulador |
| `occurred_at, ended_at, duration_seconds` | timestamptz, double precision | UTC, s | inicio, fin y duración |
| `event_type, severity, context_source, context_type` | text | | tipo (`context_record`, `low_battery_soc`, `grid_absent`, `data_gap`), severidad, origen y tipo de contexto |
| `metadata` | jsonb | | claves permitidas; índice GIN |

## Lo que no se encuentra en Gold

Las tablas `fault_history`, `settings`, `specs`, `statistics`, `status` y `totals` de la fuente se inventarían pero no se cargan: sus campos de texto codifican listas sin diccionario verificable y no son necesarias para el enfoque. Si se decodificaran, entrarían como un hecho de eventos adicional.
