# Power BI SolarBI

## Que entregar y cuantas paginas

El documento del Taller 6 exige **un minimo de tres paginas** en Power BI. La guia de abajo propone tres: resumen ejecutivo, balance energetico, y bateria/calidad. Agrega mas solo si el profesor lo solicita o si tu analisis lo necesita.

**Estado actual:** aqui estan el diseno, las medidas DAX y las instrucciones. Aun falta abrir Power BI Desktop, conectar el modelo, crear los visuales y guardar `SolarBI.pbip`; este archivo no se puede generar ni validar fielmente sin Power BI Desktop.

## 1. Conectar e importar

1. Elige un solo modo de ejecucion. Con Compose, inicia la base y la ETL con `.\run.ps1 up`; PostgreSQL queda en `localhost:5432`. Con Kubernetes, ejecuta `.\run.ps1 k8s-up` y, en otra terminal que debes dejar abierta, ejecuta `kubectl port-forward -n solarbi service/postgres-nodeport 30432:5432`; con ese modo, PostgreSQL queda en `localhost:30432`. En ambos casos la base se llama `solarbi`.
2. En Power BI Desktop elige **Obtener datos > Base de datos PostgreSQL**. Servidor: `localhost:5432` con Compose o `localhost:30432` con Kubernetes; base: `solarbi`; modo **Importar**. Usa las credenciales locales de `SolarBI\.env`; no las guardes en el repositorio ni en capturas.
3. Importa estas tablas:

   - `gold.dim_date`
   - `gold.dim_time`
   - `gold.dim_inverter`
   - `gold.fact_energy_reading`
   - `gold.fact_event`
   - `silver.measurements` (renombrala en Power Query como `silver_measurements`)

   No cargues el SQLite ni los CSV para el modelo del tablero.

## 2. Crear las relaciones

En **Vista Modelo**, crea relaciones de cardinalidad **uno a varios (1:*)**, con filtro de la dimension hacia el hecho:

| Lado 1 | Lado * |
|---|---|
| `dim_date[date_key]` | `fact_energy_reading[date_key]` |
| `dim_date[date_key]` | `fact_event[date_key]` |
| `dim_time[time_key]` | `fact_energy_reading[time_key]` |
| `dim_time[time_key]` | `fact_event[time_key]` |
| `dim_inverter[inverter_id]` | `fact_energy_reading[inverter_id]` |
| `dim_inverter[inverter_id]` | `fact_event[inverter_id]` |
| `dim_date[calendar_date]` | `silver_measurements[measurement_date]` |
| `dim_inverter[inverter_id]` | `silver_measurements[inverter]` |

Marca `dim_date` como tabla de fechas usando `dim_date[calendar_date]`. No relaciones directamente las tablas de hechos entre si. En los visuales historicos excluye `is_simulated = true`; las filas simuladas son solo para la demostracion en vivo.

## 3. Crear las medidas

En **Modelado > Nueva tabla**, crea una tabla para organizar las medidas:

```DAX
_Medidas = ROW ( "Grupo", "Medidas" )
```

Oculta la columna `_Medidas[Grupo]`. Selecciona la tabla `_Medidas` y usa **Modelado > Nueva medida** para cada medida de `Medidas.dax`. Copia y pega cada bloque completo, incluido el nombre antes del signo `=`.

Medidas incluidas:

- Energia: `Energia Solar (kWh)`, `Energia Red Importada (kWh)`, `Energia Salida Fase A (kWh)`.
- SolarBI: `CO2 Evitado Estimado (kg)` y `(t)`.
- Tendencia: `Solar Mes Anterior (kWh)` y `Variacion Solar MoM (%)`.
- Escenario economico: `Costo Red (COP)` y `Ahorro Solar Potencial (COP)`.
- Operacion/calidad: `Porcentaje Dependencia de Red`, `SoC Promedio (%)`, `Lecturas Validas (%)` y `Disponibilidad del Registro (%)`.

Si tu Power BI traduce nombres de funciones, manten el separador regional que use el editor. No cambies las unidades de CO2: el factor configurado es `kg CO2e/kWh`.

### Parametro What-if de tarifa

1. Ve a **Modelado > Nuevo parametro > Rango numerico**.
2. Nombre: `Tarifa escenario`; tipo: **Numero decimal fijo**; minimo: `0`; maximo: ajustalo a la tarifa real de la factura; incremento sugerido: `10`; valor predeterminado de demostracion: `1000`.
3. Marca **Agregar segmentador a esta pagina**. El parametro genera la tabla y columna `'Tarifa escenario'[Tarifa escenario]` que usan las dos medidas de COP.
4. Sustituye el valor provisional por una tarifa real solo despues de documentar su unidad, periodo y fuente.

## 4. Paginas y visuales que debes crear

Usa **Insertar > Nueva pagina** tres veces. Escribe el titulo indicado en cada pagina y agrega los visuales sugeridos desde el panel **Visualizaciones**.

### Pagina 1 — `Resumen ejecutivo`

- Segmentadores: `dim_date[calendar_date]` (tipo Entre), `dim_inverter[inverter_name]` y el parametro `Tarifa escenario`.
- Cuatro tarjetas: `Energia Solar (kWh)`, `Energia Red Importada (kWh)`, `CO2 Evitado Estimado (t)` y `Ahorro Solar Potencial (COP)`.
- Grafico de lineas: eje `dim_date[calendar_date]`; valores `Energia Solar (kWh)` y `Energia Red Importada (kWh)`.
- Grafico de barras agrupadas: eje `dim_inverter[inverter_name]`; valores `CO2 Evitado Estimado (kg)`.
- Agrega una nota visible: factor SIN provisional hasta verificar año y fuente UPME.

### Pagina 2 — `Balance energetico`

- Segmentadores sincronizados: fecha, inversor y `is_simulated = false`.
- Grafico de columnas apiladas: eje `dim_date[year]` > `quarter` > `month` > `calendar_date`; leyenda `dim_inverter[inverter_name]`; valores `Energia Solar (kWh)` y `Energia Red Importada (kWh)`. Habilita **Exploracion en profundidad (drill-down)**.
- Grafico de lineas: eje `dim_date[calendar_date]`; valores `Energia Solar (kWh)`, `Energia Salida Fase A (kWh)` y `Energia Red Importada (kWh)`.
- Matriz: filas `dim_inverter[inverter_name]`; columnas `dim_date[year]` y `dim_date[month]`; valores las tres medidas de energia y `CO2 Evitado Estimado (kg)`.
- Etiqueta siempre `Energia Salida Fase A`: `outw_a` no representa la carga trifasica total.

### Pagina 3 — `Bateria y calidad`

- Segmentadores: fecha e inversor.
- Grafico de lineas: eje `silver_measurements[measured_at]`; leyenda `silver_measurements[inverter]`; valor `silver_measurements[battery_soc_pct]`. Anade una linea constante de 20 % como referencia de alerta.
- Tarjetas: `SoC Promedio (%)`, `Lecturas Validas (%)` y `Disponibilidad del Registro (%)`.
- Grafico de columnas: eje `silver_measurements[invalid_reason]`; valor **Recuento de** `silver_measurements[read_ts]`; filtro visual `is_valid = false` y `is_simulated = false`.
- Tabla de eventos: `fact_event[occurred_at]`, `fact_event[inverter_id]`, `fact_event[event_type]`, `fact_event[severity]` y `fact_event[context_source]`.
- En disponibilidad, la cadencia esperada de 13 s es una estimacion de cobertura, no un SLA ni una medicion de disponibilidad del inversor.

## 5. Guardar y comprobar

1. Agrega navegacion entre paginas (**Insertar > Botones > Navegador de paginas**) y titulos/unidades en todos los visuales. Si quieres que los segmentadores controlen las tres paginas, activa **Vista > Sincronizar segmentadores** y marca cada pagina.
2. Prueba los segmentadores, el drill-down y el parametro What-if. Comprueba que las paginas historicas excluyen las muestras sinteticas.
3. Guarda como **Power BI Project (`.pbip`)** dentro de `SolarBI\PowerBI\`, con nombre `SolarBI.pbip`. No publiques ni subas claves o datos locales.
4. Exporta una copia PDF para el informe del taller y toma capturas sin credenciales.

Kubernetes requiere tuneles `kubectl port-forward` para PostgreSQL y Grafana en este equipo; la guia operativa indica los comandos. Compose usa directamente `localhost:5432` y Grafana en `http://localhost:3000`. Distingue siempre la simulacion del tablero Grafana de la telemetria historica real.
