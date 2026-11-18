# Negocio y diagnostico de la fuente

## Usuarios y preguntas

| Stakeholder | Preguntas que responde |
|---|---|
| Direccion de infraestructura | Cuanta energia solar se estima por dia/mes/inversor; cuanto se importa de red; como cambia el costo bajo una tarifa; que inversiones priorizar |
| Mantenimiento/operacion | Que SoC tiene cada bateria, que potencia se observa ahora, hubo una pausa de muestras o una alerta |
| Analista BI/sostenibilidad | Que datos se validaron; como se calculo cada KPI; cuanto CO2 potencial se estima con el factor SIN del año escogido |
| Administrador de datos/IT | Puede repetirse el ETL, recuperarse PostgreSQL y Grafana, y mantenerse claves/configuracion separadas |

## Fuente y limitaciones

Fuente primaria: SQLite OpenCEM v1.0.0 comprimido en Zstandard, distribuido por Zenodo. El ETL consulta `analog_measurements` y `context` en modo lectura; antes de cargar verifica SHA-256 y `PRAGMA quick_check`. El reporte inventaria **todas** las tablas, columnas/tipos, filas y rango temporal de sus timestamps; esto es la evidencia exacta de cobertura de la copia local.

El repositorio CSV `opencem-dataset-main` es una publicacion complementaria y no sustituye la SQLite oficial. Su README publica una mediana de 13 segundos por inversor; Silver conserva grano original y Gold excluye gaps mayores a 5 minutos al integrar energia. Nulos estructurales no se rellenan. `pv1power` y `gridpowerw_a` estan documentados en W, `battsoc` en %, `battvolt` en V, `battcurr` en A y `read_ts` como epoch segundos UTC. `outw_a` esta documentado en W. `outsumw` no se usa porque su unidad no queda confirmada en el diccionario de la entrega.

No usar este dataset para certificar PR fotovoltaico: faltan irradiancia en plano de paneles y medidas calibradas suficientes para relacionar irradiancia con energia AC. Tampoco se conoce de forma fiable exportacion/curtailment neta; el indicador de CO2 es potencial y no una reduccion verificada.

## Profundizacion SolarBI

Estimacion: `kWh PV1 integrados x factor de emisiones SIN (kg CO2e/kWh)`. Se informan por separado origen, año, unidad y fecha de consulta del factor. En el entregable se debe validar un factor oficial UPME y registrar la fuente/año. Si la energia solar se exporta o se limita, la estimacion no prueba que cada kWh haya desplazado consumo de red. Por eso el nombre del tablero conserva “estimado”.
