# Modelo dimensional

## Procesos y granos

| Hecho | Proceso | Grano | Filas | Uso |
|---|---|---|---:|---|
| `gold.fact_energy_reading` | Telemetría | una lectura válida por inversor y marca UTC | 2.934.098 | Detalle y alimentación del simulador; nadie lo carga en Power BI |
| `gold.fact_energy_hour` | Telemetría resumida | inversor x hora local del sitio | 14.077 | Power BI: balance, SoC, red, CO2 |
| `gold.fact_quality_day` | Calidad y cobertura | inversor x día local | 711 | Power BI: datos válidos y disponibilidad |
| `gold.fact_event` | Eventos | un registro de contexto o un episodio derivado | 25.294 | Operación y análisis de contexto (JSON) |

La energía de una lectura es la integral hasta la siguiente lectura del mismo inversor, siempre que el hueco no pase de 900 s. `fact_energy_hour` suma esas energías por hora local; sus columnas son aditivas (kWh) salvo `avg_soc_pct`, `min_soc_pct` y `grid_present_ratio`, que se promedian o se toman como mínimo.

Power BI no necesita 3 millones de filas para un tablero mensual; una fila por hora conserva el perfil diario (amanecer, medio día, noche), que es lo que importa al negocio, y el modelo se importa en segundos.

## Dimensiones

| Dimensión | Llave | Atributos | Notas |
|---|---|---|---|
| `dim_date` | `date_key` (AAAAMMDD) | fecha, año, trimestre, mes y su nombre, `year_month`, semana ISO, día y su nombre, fin de semana, `is_historical` | Día local del sitio. `is_historical` deja fuera los días que solo existen por la simulación. |
| `dim_time` | `time_key` (minuto 0 a 1439) | hora, minuto, cuarto de hora, etiqueta HH:MM, `day_period` | **Dimensión propia**: `day_period` agrupa las horas en amanecer (6 a 8), día (9 a 15), atardecer (16 a 18) y noche, un corte del negocio solar que la fuente no trae. |
| `dim_inverter` | `inverter_id` | nombre, sistema de origen, vigencia (`valid_from`, `valid_to`, `is_current`), pico observado de PV1 y de salida | La capacidad nominal queda en NULL porque no está en los datos. |

Las tres dimensiones son conformadas: las usan los cuatro hechos.

## Matriz de bus

| Proceso | `dim_date` | `dim_time` | `dim_inverter` |
|---|:---:|:---:|:---:|
| Lecturas (`fact_energy_reading`) | X | X | X |
| Resumen horario (`fact_energy_hour`) | X | X (hora) | X |
| Calidad diaria (`fact_quality_day`) | X | | X |
| Eventos (`fact_event`) | X | X | X (puede ser NULL en contextos sin inversor) |

## Modelado de eventos

`fact_event` guarda dos tipos de origen distinto en una sola tabla de grano "evento":

- **Registros de contexto** (25.178): vienen de la tabla `context` de la fuente. Se conservan su tipo (`context_type`, 55 valores), el inicio, el fin y la duración. El campo `metadata` (JSONB, índice GIN) guarda solo las claves de una lista permitida.
- **Episodios derivados** (116): se detectan en las lecturas. Una racha de lecturas consecutivas con la condición (SoC < 20 %, voltaje de línea < 100 V) forma un episodio; se corta cuando el hueco entre lecturas pasa de 900 s y se exigen al menos 2 lecturas, para no registrar parpadeos de una sola muestra. Los 69 huecos de datos (`data_gap`) se registran con otra regla: cada intervalo mayor a 900 s entre dos lecturas.

El evento tiene inicio, fin y duración, así que sirve para medir cuánto tiempo estuvo un inversor sin red, y no solo cuántas veces. La tabla incluye además los eventos sintéticos del simulador (`simulator`, `simulator_demo`); la vista `gold.v_event` los excluye para Power BI.

## Evolución de la dimensión de inversor

`dim_inverter` trae `valid_from`, `valid_to` e `is_current`. Hoy los dos inversores tienen una sola versión (tipo 1 en la práctica, porque la fuente no muestra cambios de equipo). Si aparece un cambio relevante (nueva capacidad de batería, más módulos), se cierra la fila vigente (`valid_to` = fecha del cambio, `is_current` = false) y se inserta otra con nueva clave sustituta y atributos nuevos (tipo 2). Los hechos se unen por la versión vigente en la fecha de la lectura, de modo que el histórico no se reinterpreta con la capacidad nueva. No se simula ese cambio con datos inventados.

## Linaje y capas

| Capa | Tabla | Contenido |
|---|---|---|
| Bronze | `measurements_raw` | Cada lectura de la fuente con todas sus columnas en un JSONB. 2.989.759 filas, sin transformar. |
| Bronze | `context_raw` | Contexto con metadatos filtrados (el texto libre no se guarda). |
| Silver | `measurements` | Lecturas tipadas con `is_valid` e `invalid_reason`; conserva todo, también lo rechazado. |
| Silver | `context` | Contexto con fechas convertidas. |
| Gold | dimensiones y hechos arriba | Solo lecturas válidas e históricas. |

Reglas de columnas, tipos y unidades: [DICCIONARIO.md](DICCIONARIO.md). Reglas de calidad y conciliación: [CALIDAD_Y_ETL.md](CALIDAD_Y_ETL.md).

## Vistas y permisos

`gold.v_energy_hour` y `gold.v_event` convierten la hora UTC a hora local del sitio. El rol `bi_reader` (login `solarbi_reader`) solo tiene SELECT sobre el esquema `gold`: Power BI y Grafana no pueden leer Bronze ni Silver ni modificar nada.
