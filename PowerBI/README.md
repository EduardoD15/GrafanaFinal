# Power BI SolarBI

Proyecto de Power BI (`SolarBI.pbip`, formato PBIP con modelo en TMDL) conectado a la base `solarbi` de PostgreSQL en modo Importar. Se abre con Power BI Desktop reciente (activar "Proyectos de Power BI" y "Formato TMDL" en Opciones > Características de versión preliminar si el programa lo pide).

## Conexión

1. Levantar la base: `.\run.ps1 up` (Compose, `localhost:5432`) o `.\run.ps1 k8s-up` (Kubernetes; con `kubectl port-forward -n solarbi service/postgres-nodeport 30432:5432` queda en `localhost:30432`). La ETL debe haber terminado (`.\run.ps1 status`).
2. Abrir `SolarBI.pbip`. Los parámetros `Servidor` y `BaseDeDatos` (Transformar datos > Editar parámetros) apuntan por defecto a `localhost:5432` y `solarbi`.
3. Credenciales: usuario `solarbi_reader` y la clave `BI_READER_PASSWORD` del `.env` local. El rol `bi_reader` solo tiene SELECT sobre el esquema `gold`; no se usa el usuario dueño de la base. Ninguna credencial queda guardada en el proyecto.
4. Actualizar. El modelo importa seis tablas de `gold` (`dim_date`, `dim_time`, `dim_inverter`, `v_energy_hour`, `fact_quality_day`, `v_event`) y tres tablas de parámetros construidas en Power Query.

## Modelo

Esquema en estrella con tres dimensiones conformadas y tres hechos, todas las relaciones 1:* con filtro de la dimensión al hecho:

| Hecho | Grano | Dimensiones |
|---|---|---|
| `fact_energy_hour` (vista `gold.v_energy_hour`) | inversor x hora local del sitio | fecha, hora, inversor |
| `fact_quality_day` | inversor x día local | fecha, inversor |
| `fact_event` (vista `gold.v_event`) | evento o episodio | fecha, hora, inversor |

`dim_date` está marcada como tabla de fechas (`calendar_date`) y trae jerarquía año > trimestre > mes > día; `dim_time` trae jerarquía periodo del día > hora. Las vistas ya convierten la hora UTC a la hora local de Shenzhen (UTC+8) y excluyen las filas del simulador, de modo que el histórico no se mezcla con la demostración en vivo.

Las tablas `Tarifa`, `Factor CO2` y `Capacidad FV` son los parámetros de escenario (what-if) y `Balance` ordena los conceptos del balance energético. Ninguna de las cuatro se relaciona con los hechos; sus valores entran a las medidas con `SELECTEDVALUE`.

## Medidas

Las 39 medidas viven en la tabla `Medidas`, agrupadas por carpeta. El detalle está en [Medidas.dax](Medidas.dax).

| Carpeta | Contenido |
|---|---|
| 01. Energía | solar (PV1), salida del inversor (fase A), red importada, descarga y carga de batería, pérdidas y otros |
| 02. Escenarios | factor de CO2, tarifa y aumento de capacidad (what-if), CO2 evitado, ahorro y costo en COP, solar con más capacidad |
| 03. Híbrido y batería | dependencia de la red, autosuficiencia, SoC promedio y mínimo, horas con SoC bajo, horas sin red |
| 04. Calidad y disponibilidad | lecturas recibidas/válidas/vacías, % de datos válidos, disponibilidad del registro, huecos sobre el límite |
| 05. Eventos | episodios de red ausente, batería baja, huecos de datos y registros de contexto |
| 06. Inteligencia de tiempo | mes anterior, variación mensual, acumulado del año (TOTALYTD), acumulado del periodo, promedio móvil de 7 días |
| 07. Balance | valor por concepto para el gráfico de balance |

Valores de referencia con todos los segmentadores en "Todas" (coinciden con el reporte de la ETL y con las consultas de `sql/consultas.sql`): solar 2.346,6 kWh; red importada 4.734,4 kWh; CO2 evitado 0,52 t con el factor 0,220 kg/kWh; dependencia de la red 66,9 %; disponibilidad del registro 82,5 %; datos válidos 98,1 %.

Las medidas de razón devuelven vacío cuando no hay datos en el contexto, en lugar de 0 o 100 %.

## Páginas

| Página | Contenido |
|---|---|
| Resumen | tarjetas de energía, CO2, ahorro, dependencia y disponibilidad; solar frente a red por día; CO2 por mes e inversor; dependencia y autosuficiencia por mes |
| Balance energético | barras de balance (solar + red + descarga - carga - pérdidas = salida), columnas apiladas con drill-down año > trimestre > mes > día, matriz por mes e inversor |
| Batería y red | SoC promedio por hora, SoC mínimo diario, episodios por mes, perfil horario de solar frente a red |
| Calidad de datos | lecturas recibidas/válidas/vacías, disponibilidad por mes y por día, matriz por inversor |
| Escenarios | parámetros what-if de tarifa, factor de CO2 y aumento de capacidad; ahorro y costo; solar acumulado; promedio móvil |

Todas las páginas tienen el navegador de páginas arriba. Las cuatro primeras traen tres segmentadores desplegables (año, mes, inversor); la de escenarios trae los tres parámetros what-if.

## Parámetros what-if

| Parámetro | Rango | Valor por defecto | Fuente |
|---|---|---|---|
| Tarifa COP/kWh | 300 a 1.500, paso 10 | 790 | Tarifa de referencia EPM estrato 3 de diciembre de 2025: 790,49 COP/kWh |
| Factor g/kWh | 50 a 700, paso 5 | 220 | Resolución UPME 085 de 2026, inventarios del SIN 2024: 0,220 tCO2eq/MWh |
| Aumento % de capacidad | 0 a 100, paso 5 | 0 | Escenario hipotético; no hay placa de capacidad en los datos |

El factor 0,660 tCO2eq/MWh que publica la misma resolución es el de proyectos MDL eólicos y solares; no es el que corresponde a una comparación contra la red actual, por eso el valor por defecto es el del SIN.

## Lectura correcta de los indicadores

- Solar es el canal `pv1power` (un solo canal FV); la salida es `outw_a` (fase A). Ninguno es la generación ni la carga totales de la instalación, por eso "Pérdidas y otros" es un residuo de balance y no una pérdida física medida.
- La red importada es el canal `linepowerw_a`. A partir de mayo de 2026 el inversor 1 reporta la red ausente durante semanas (episodios de 524 y 581 horas); en esos meses la dependencia cae a 0 % y la autosuficiencia sube a 100 %, lo que describe operación en isla o pérdida del canal, no una mejora de desempeño.
- La disponibilidad es tiempo cubierto por lecturas continuas (huecos de hasta 900 s) sobre el tiempo esperado; no es un SLA ni la disponibilidad del equipo.

## Guardado

El proyecto se guarda sin datos importados ni credenciales. Al abrirlo en otra máquina hay que actualizar una vez. Las capturas de las cinco páginas están en `docs/img/powerbi-*.png`.
