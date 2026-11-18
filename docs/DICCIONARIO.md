# Diccionario del almacen analitico

Las llaves primarias, foraneas e indices se declaran en `sql/schema.sql`. Todas las horas estan en UTC. El inventario dinamico de SQLite, incluidos nombres/tipos de todas las columnas originales, conteos y periodos, queda en `reports/etl-report.json`.

## Bronze

| Tabla.columna | Tipo | Llave | Origen / regla |
|---|---|---|---|
| `measurements_raw.read_ts` | double precision | PK compuesta | `analog_measurements.read_ts`, epoch en segundos |
| `measurements_raw.inverter` | smallint | PK compuesta | `analog_measurements.inverter` |
| `measurements_raw.payload` | jsonb | — | Snapshot de las columnas originales de una lectura |
| `measurements_raw.is_simulated` | boolean | — | `false` para fuente; `true` para stream sintetico |
| `measurements_raw.ingested_at` | timestamptz | — | `now()` PostgreSQL |
| `context_raw.id` | bigint | PK | `context.id` |
| `context_raw.recorded/starts/ends` | double precision | — | Epochs SQLite del contexto |
| `context_raw.metadata` | jsonb | — | JSON permitido: inverter, source, device, task_type; excluye texto libre |
| `context_raw.ingested_at` | timestamptz | — | `now()` PostgreSQL |

## Silver

| Tabla.columna | Tipo | Llave | Origen / regla |
|---|---|---|---|
| `measurements.read_ts` | double precision | PK compuesta | Epoch fuente |
| `measurements.inverter` | smallint | PK compuesta | Id fuente (1/2) |
| `measurements.measured_at` | timestamptz | — | `to_timestamp(read_ts)` en UTC |
| `measurements.measurement_date` | date | FK logica a `dim_date` | Fecha UTC de `read_ts`, usada para relacionar calidad en Power BI |
| `measurements.pv_power_w` | double precision | — | `pv1power` (W), NULL se conserva |
| `measurements.load_power_w` | double precision | — | `outw_a` (W), salida fase A |
| `measurements.grid_power_w` | double precision | — | `gridpowerw_a` (W); signo negativo indica exportacion |
| `measurements.battery_soc_pct` | double precision | — | `battsoc` (%) |
| `measurements.battery_voltage_v` | double precision | — | `battvolt` (V) |
| `measurements.battery_current_a` | double precision | — | `battcurr` (A) |
| `measurements.is_valid` | boolean | — | Reglas descritas en `CALIDAD_Y_ETL.md` |
| `measurements.invalid_reason` | text | — | Primera regla de rechazo, NULL si valida |
| `measurements.is_simulated` | boolean | — | Marca muestras sinteticas |
| `context.id` | bigint | PK | `context.id` |
| `context.recorded/starts/ends` | timestamptz | — | Epoch del contexto convertido a UTC |
| `context.metadata` | jsonb | — | Metadatos de la lista permitida |

## Gold dimensiones

| Tabla.columna | Tipo | Llave | Origen / regla |
|---|---|---|---|
| `dim_date.date_key` | integer | PK | YYYYMMDD |
| `dim_date.calendar_date` | date | UQ | Dia UTC |
| `dim_date.year/quarter/month/day/day_of_week` | smallint | — | Jerarquia calendario; dia ISO 1-7 |
| `dim_time.time_key` | smallint | PK | Minuto del dia, 0-1439 |
| `dim_time.hour/minute/quarter_hour` | smallint | — | Hora, minuto y bloque de 15 min |
| `dim_time.time_label` | text | — | HH:MM |
| `dim_inverter.inverter_id` | smallint | PK | Identificador OpenCEM |
| `dim_inverter.inverter_name` | text | — | Etiqueta creada para el taller |
| `dim_inverter.rated_power_kw` | numeric(8,3) | — | 8 kW segun especificacion del dataset |
| `dim_inverter.pv_capacity_kwp` | numeric(8,3) | — | 26 paneles x 480 W = 12.48 kWp por subsistema |
| `dim_inverter.source_system` | text | — | OpenCEM |
| `dim_inverter.valid_from/valid_to` | date | — | Vigencia SCD tipo 1; valid_to NULL cuando vigente |
| `dim_inverter.is_current` | boolean | — | Indicador de version actual |

## Gold hechos

| Tabla.columna | Tipo | Llave | Origen / regla |
|---|---|---|---|
| `fact_energy_reading.measured_at` | timestamptz | PK compuesta | Timestamp exacto UTC |
| `fact_energy_reading.date_key` | integer | FK `dim_date` | Fecha YYYYMMDD |
| `fact_energy_reading.time_key` | smallint | FK `dim_time` | Hora y minuto |
| `fact_energy_reading.inverter_id` | smallint | PK compuesta, FK `dim_inverter` | Inversor |
| `pv_power_w/load_power_w/grid_power_w` | double precision | — | W desde Silver |
| `battery_soc_pct` | double precision | — | SoC desde Silver (%) |
| `solar_kwh_interval/load_kwh_interval/grid_kwh_interval` | double precision | — | Energia trapezoidal al siguiente punto; NULL si hueco >5 min o potencia ausente |
| `co2_avoided_kg_interval` | double precision | — | Energia solar x factor SIN configurado |
| `emission_factor_kg_kwh` | double precision | — | Factor utilizado en el calculo |
| `is_simulated` | boolean | — | Excluir para el historico |
| `fact_event.event_id` | text | PK | `context:<id>`, `low-soc:<timestamp>:<inversor>` o id sintetico |
| `fact_event.occurred_at` | timestamptz | — | Hora del contexto/lectura |
| `fact_event.date_key/time_key/inverter_id` | integer/smallint | FK dimensiones | Dimensiones de evento; inversor nullable para anotacion sin inversor |
| `fact_event.event_type/severity` | text | — | `context_record` o `low_battery_soc`; severidad informativa/advertencia/critica |
| `fact_event.context_source` | text | — | Fuente del contexto o regla que derivo alerta |
| `fact_event.metadata` | jsonb | — | JSON minimizado, con indice GIN |

`fact_energy_reading` y `fact_event` son procesos distintos en la matriz de bus. `dim_time` agrega a minuto, pero el hecho conserva segundos y fracciones del timestamp; no se pierde precision de la lectura.
