# Modelo dimensional, diccionario y lineage

## Eleccion y grano

Esquema estrella en Gold: `fact_energy_reading` registra una lectura valida por inversor y timestamp UTC; las tres energias son la integral desde esa lectura hasta la siguiente del mismo inversor si el hueco no supera cinco minutos. `fact_event` tiene una fila por anotacion de contexto o por lectura con SoC bajo. Son dos procesos de negocio distintos: telemetria y eventos.

## Dimensiones y hechos

| Objeto | Grano / llave | Atributos y origen |
|---|---|---|
| `gold.dim_date` | dia; PK `date_key` YYYYMMDD | fecha, anio, trimestre, mes, dia, dia ISO; cubre lecturas UTC y fechas de contexto |
| `gold.dim_time` | minuto del dia; PK `time_key` 0-1439 | hora, minuto, bloque 15 min; dimension construida para el taller |
| `gold.dim_inverter` | inversor; PK `inverter_id` | 1/2, 8 kW nominal, 12.48 kWp (26 x 480 W por arreglo), fuente OpenCEM |
| `gold.fact_energy_reading` | instante x inversor; PK `(measured_at, inverter_id)` | potencias, SoC, energia integrada, factor y CO2; Silver + parametro |
| `gold.fact_event` | evento; PK `event_id` | anotacion o SoC bajo, severidad, metadatos minimizados; contexto JSON / Silver |

`dim_inverter` empieza como SCD tipo 1. Si cambian potencia, baterias o paneles, se debe conservar la version historica con SCD tipo 2 antes de atribuir periodos a nuevas capacidades.

## Capas y lineage

| Campo destino | Tipo | Llave | Regla | Origen |
|---|---|---|---|---|
| `bronze.measurements_raw.payload` | JSONB | `(read_ts, inverter)` | copia de columnas originales del SQLite; no se altera el archivo de origen | `analog_measurements.*` |
| `silver.measurements.measured_at` | timestamptz | `(read_ts, inverter)` | `to_timestamp(read_ts)`, UTC; epoch fuera de 2000-2100 se rechaza | `analog_measurements.read_ts` |
| `silver.measurements.measurement_date` | date | — | fecha UTC derivada de la marca temporal; habilita relación de calidad con DimFecha en Power BI | `read_ts` |
| `pv_power_w` | double | — | PV1; nulos se conservan; negativos se rechazan | `pv1power`, W |
| `load_power_w` | double | — | salida fase A, no carga total | `outw_a`, W |
| `grid_power_w` | double | — | canal A; valores negativos se interpretan como exportacion y no suman importacion | `gridpowerw_a`, W |
| `battery_soc_pct` | double | — | solo se rechaza fuera de [0,100]; ausente no se imputa | `battsoc`, % |
| `silver.context.metadata` | JSONB | id | lista permitida: `inverter`, `source`, `device`, `task_type`; elimina texto libre | `context.value` |
| `solar_kwh_interval` | double | — | potencia media de dos lecturas x segundos / 3,600,000; huecos > 300 s son NULL | `pv1power` + `read_ts` |
| `co2_avoided_kg_interval` | double | — | solar kWh x `CO2_FACTOR_KG_PER_KWH` | KPI derivado / ConfigMap |
| `fact_event.event_type` | text | — | `context_record` o `low_battery_soc` (SoC <20%) | contexto/mediciones |

Todas las horas de almacenamiento son UTC. Las marcas de simulacion se etiquetan `is_simulated=true` y se excluyen del historico analitico.

## Bus matrix

| Proceso | Fecha | Hora | Inversor | Evento/JSON |
|---|---:|---:|---:|---:|
| Lecturas / energia | X | X | X | |
| Anotaciones de contexto | X | X | X (cuando existe) | X |
| Alerta derivada SoC bajo | X | X | X | X |
