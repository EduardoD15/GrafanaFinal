# Guia de solucion y trazabilidad del Taller 6

Este documento responde en que consiste cada archivo descargado, donde se usa, que requisito cubre cada componente y que queda pendiente. El objetivo elegido es **SolarBI: estimacion de CO2 potencialmente evitado por generacion solar usando el factor del SIN**.

## 1. Que datos se descargaron y donde estan

| Recurso | Ubicacion en este equipo | Para que sirve | Se carga al almacen BI |
|---|---|---|---|
| SQLite oficial de Zenodo, archivo comprimido | `data\bronze\opencem-v1.0.0.sqlite.zst` | Fuente historica oficial; se comprueba su SHA-256 antes de procesarla | No directamente; el ETL lee su SQLite |
| SQLite oficial descomprimida | `data\bronze\opencem-v1.0.0.sqlite` (4,439,408,640 bytes) | Bronze original inmutable; conserva las tablas y columnas de OpenCEM | Solo algunas tablas se copian a PostgreSQL |
| Manifiesto SHA-256 | `data\bronze\opencem-v1.0.0-SHA256SUMS.txt` | Comprobar que el archivo descargado no cambio | No; sirve para validar la fuente |
| `opencem-v1.0.0-support.zip` | `..\opencem-v1.0.0-support.zip` | Documentacion tecnica: diccionario de columnas/unidades, esquema, validaciones y codigo de referencia | No; es documentacion, no una tabla de telemetria |
| Repositorio CSV `opencem-dataset-main` | `..\opencem-dataset-main\` | Referencia complementaria: README, metadatos y particiones CSV de mediciones/contexto | No; se evita mezclar una publicacion CSV distinta con la SQLite oficial |
| Dataset de cielos `opencem-v1.0.0-sky-*.tar.zst` | No descargado | Imagenes de cielo, fuera del alcance de este analisis energetico | No |

El repositorio CSV local tiene 39 archivos de datos (~897 MB). Su README informa 4,021,653 mediciones y 30,009 contextos; la SQLite oficial procesada contiene 2,989,759 mediciones y 25,178 contextos. **No son una copia identica:** no se combinan para evitar duplicados, fechas/versiones incompatibles y cambios de significado. El README del repositorio CSV se uso como referencia de estructura, granularidad y unidades; la fuente del ETL es la SQLite de Zenodo.

El ZIP de soporte permanece junto a los archivos descargados, fuera de `SolarBI`; no se importa a PostgreSQL. Sus documentos tecnicos respaldan la interpretacion de campos (por ejemplo, `pv1power` y `outw_a` en W, `battsoc` en %, `read_ts` como epoch UTC). No se deben confundir documentos, esquemas y codigo con filas que Grafana vaya a graficar.

### Inventario de la SQLite procesada

El reporte dinamico, con columnas/tipos y periodos UTC, esta en `reports\etl-report.json`. Resume:

| Tabla fuente | Filas inventariadas | Uso actual |
|---|---:|---|
| `analog_measurements` | 2,989,759 | Importada a Bronze y Silver; sus lecturas validas alimentan Gold |
| `context` | 25,178 | Importada a Bronze/Silver; sus metadatos permitidos llegan a eventos Gold |
| `fault_history` | 2,129,517 | Inventariada, aun no transformada a eventos |
| `settings` | 2,284,775 | Inventariada, aun no importada |
| `specs` | 1,333,174 | Inventariada, aun no importada |
| `statistics` | 2,285,904 | Inventariada, aun no importada |
| `status` | 2,240,052 | Inventariada, aun no importada |
| `totals` | 2,279,918 | Inventariada, aun no importada |

`analog_measurements` cubre 2025-07-12 a 2026-07-13 UTC; `context` cubre 2024-07-27 a 2026-07-14 UTC. El ETL copia solo `analog_measurements` y `context` a PostgreSQL. Las otras tablas aparecen en el inventario porque se revisa el contenido de la fuente, pero **no se muestran en Power BI/Grafana**. En particular, `fault_history` contiene registros tipo lista JSON con semantica de componentes; aun falta decodificarlos con reglas verificadas antes de llamarlos alarmas reales.

## 2. Por que los datos no aparecen como archivos en los tableros

La SQLite es un archivo de entrada local, no un informe. Al ejecutar el ETL, se copian las tablas seleccionadas a un volumen PostgreSQL administrado por Docker/Kubernetes. Grafana y Power BI consultan PostgreSQL; no abren ni dibujan directamente el `.sqlite` o los CSV.

El flujo es:

```text
SQLite oficial -> ETL y controles -> PostgreSQL Bronze -> Silver -> Gold -> Power BI historico
                                                                      -> Grafana operativo
Simulador sintetico cada 1 s -------------------------------> Bronze/Silver/Gold -> Grafana
```

- **Bronze fuente:** `bronze.measurements_raw` y `bronze.context_raw`, snapshots seleccionados de la SQLite. El archivo original no se altera.
- **Silver:** mediciones tipadas y validadas, y contexto JSON minimizado. Se conservan nulos y timestamps originales; no se imputan.
- **Gold:** `fact_energy_reading` para el analisis energetico y `fact_event` para contexto y eventos derivados.
- **Grafana:** las series operativas del dashboard filtran `is_simulated=true`; por eso muestran el simulador, no la curva historica del archivo. Cada muestra sintetica queda marcada y el panel lo indica.
- **Power BI:** debe conectarse a Gold para el historico y excluir las filas simuladas. El archivo PBIP todavia no se ha creado en Power BI Desktop.

Para ver las filas en DBeaver con Compose, conecta PostgreSQL a `localhost:5432`. Con Kubernetes, primero deja abierto `kubectl port-forward -n solarbi service/postgres-nodeport 30432:5432` y conecta a `localhost:30432`. En ambos casos usa la base y credenciales de `.\.env`, y ejecuta:

```sql
SELECT count(*) FROM bronze.measurements_raw WHERE NOT is_simulated;
SELECT count(*) FROM silver.measurements WHERE NOT is_simulated;
SELECT count(*) FROM gold.fact_energy_reading WHERE NOT is_simulated;
SELECT count(*) FROM gold.fact_event WHERE event_type = 'context_record';
SELECT * FROM gold.fact_energy_reading
WHERE NOT is_simulated ORDER BY measured_at DESC LIMIT 20;
```

La conciliacion ya verificada esta en `reports\etl-report.json`: 2,989,759 mediciones en Bronze, Silver validas y Gold; 0 rechazos; 25,178 contextos transformados en 25,178 eventos de contexto. El SHA-256 del archivo comprimido se verifico contra el manifiesto.

## 3. Requisitos del taller y ubicacion de la solucion

| Requisito | Implementacion / archivo | Estado real |
|---|---|---|
| A. Negocio, usuarios y preguntas | `docs\NEGOCIO_Y_FUENTE.md` | Documentado |
| A. Diagnostico, periodo y limites | `reports\etl-report.json`, `docs\NEGOCIO_Y_FUENTE.md` | Inventario ejecutado; limitar conclusiones a lo que miden las variables |
| B. Catalogo de KPI y SolarBI | `docs\INDICADORES.md`, `PowerBI\Medidas.dax`, `sql\consultas.sql` | Documentado; factor SIN y tarifa siguen siendo valores demostrativos hasta citar fuente/año |
| B. Balance, calidad, disponibilidad, COP y KPI hibridos | `docs\INDICADORES.md`, `sql\consultas.sql`, `PowerBI\Medidas.dax` | Reglas y calculos preparados |
| B. Limite de PR fotovoltaico | `docs\NEGOCIO_Y_FUENTE.md`, `docs\INDICADORES.md` | Explicado: falta irradiancia/calibracion suficiente |
| C. Hechos, dimensiones, granos y bus matrix | `docs\MODELO.md`, `sql\schema.sql` | Modelo estrella con lecturas y eventos; dimensiones fecha/hora/inversor |
| D. Diccionario, llaves, unidades y lineage | `docs\DICCIONARIO.md`, `docs\MODELO.md`, reporte ETL | Documentado; el reporte incluye inventario completo de columnas SQLite |
| E. ETL reproducible y calidad | `app\etl.py`, `app\core.py`, `docs\CALIDAD_Y_ETL.md` | ETL ejecutado y conciliado para mediciones/contexto; `fault_history` y otras tablas aun no se transforman |
| E. JSON, reglas y trazabilidad | `silver.context.metadata`, `gold.fact_event.metadata`, `sql\consultas.sql` | JSON filtrado por lista permitida; texto libre eliminado |
| F. PostgreSQL, indices y SQL analitico | `sql\schema.sql`, `sql\consultas.sql` | DDL, consulta JSON nativa, agregaciones, ventanas y `LAG` disponibles |
| G. Power BI desde PostgreSQL, 3 paginas, fecha, drill-down, what-if | `PowerBI\README.md`, `PowerBI\Medidas.dax` | **Pendiente manual:** construir y guardar el `.pbip` en Power BI Desktop; no existe todavia |
| H. Datasource, variables, series y estado Grafana | `grafana\provisioning\`, `grafana\dashboards\solarbi-operacion.json`, `grafana\dashboards\solarbi-historico.json` | Ambos dashboards consultan PostgreSQL; se verificaron filas historicas Solar/CO2 y muestras simuladas recientes; datasource OK y 3 reglas |
| H. Dos alertas de KPI y streaming | `grafana\provisioning\alerting\alert-rules.yml`, `app\simulator.py` | Reglas de SoC bajo y flujo ausente; se agrega una alerta aleatoria marcada como demo y su tabla visual |
| I. Arquitectura y comparacion BI/Grafana | `docs\ARQUITECTURA.md` | Documentado; evolucion Modbus/SCADA incluida |
| J. Etica, privacidad y credenciales | `docs\ETICA_Y_SEGURIDAD.md`, `.env`, Secret de Kubernetes | Controles base preparados; validar el tratamiento final con docente |
| K. Docker Compose | `docker-compose.yml`, `Dockerfile`, `run.ps1` | Un comando: `.\run.ps1 up` |
| K. Kubernetes local | `k8s\`, `run.ps1` | PostgreSQL, Grafana y simulador listos; carga historica conciliada (2,989,759 lecturas). `run.ps1 k8s-up` reutiliza un Job ETL activo/completado; solo `-ForceEtl` solicita recalcular. En este equipo se requiere `kubectl port-forward` desde Windows |
| Entregable Power BI, PDF, GitHub y evidencias | `PowerBI\`, `docs\DEMO_Y_EVIDENCIAS.md` | **Pendiente:** PBIP, PDF con nombres/evidencias, enlace GitHub y commits de la pareja; esta carpeta no contiene `.git` |

El documento del taller exige minimo tres paginas de Power BI; no pide exactamente tres. La guia recomienda tres que cubren resumen, balance energetico, y bateria/calidad. Si el docente pide cambios, ajusta la guia.

## 4. Docker y Kubernetes: cual comando usar

Docker Compose y Kubernetes no son dos comandos que se deban ejecutar simultaneamente para levantar la misma instancia. Compose es el camino local sencillo; Kubernetes orquesta los contenedores en el cluster local. Elige uno para trabajar y para la demostracion:

```powershell
# Opcion Compose
.\run.ps1 up

# Opcion Kubernetes (requiere Docker Desktop Kubernetes activo)
.\run.ps1 k8s-up
```

`k8s-up` necesita que `kubectl config current-context` sea `docker-desktop`. Despliega PostgreSQL con PVC, Grafana con ConfigMaps de provisioning, un Job ETL y el Deployment del simulador. Si el Job ya esta activo o completo, lo reutiliza para no reiniciar la carga historica; para recalcularla de forma intencional usa `.\run.ps1 k8s-up -ForceEtl`. Se actualizaron los ConfigMaps desde los mismos archivos versionados de Grafana para evitar dashboards/alertas divergentes.

El cluster Docker Desktop observado tiene ~3.9 GiB de memoria disponible. La carga inicial procesa casi tres millones de lecturas y puede tardar. Si tarda, observa `.\run.ps1 k8s-status` y `kubectl logs -n solarbi job/solarbi-etl`; no ejecutes otro ETL en paralelo.

## 5. Alertas de demostracion y limpieza segura

El simulador escribe una muestra sintetica por inversor cada 1000 ms y emite una alerta de demostracion en intervalos aleatorios. La regla aparece en Grafana > **Alerting > Alert rules** y el evento queda en la tabla **Alertas/eventos sinteticos recientes (demostracion)**. El registro incluye `simulated=true`, `demo_only=true` y texto que niega que sea una falla real. No se envia correo/SMS hasta configurar un contact point.

```powershell
# Ver recursos Kubernetes
.\run.ps1 k8s-status

# Detener Compose y conservar su volumen PostgreSQL
.\run.ps1 down

# Borrar solo datos/recursos de SolarBI en Compose y namespace Kubernetes
.\run.ps1 clean
```

`clean` solicita escribir `BORRAR SOLARBI`. Elimina volumenes PostgreSQL/Grafana de Compose y el namespace `solarbi` (incluidos sus PVC) si el contexto local responde. **No borra** la SQLite, el archivo comprimido, el CSV descargado, `.env`, imagenes Docker ni reportes. No uses `docker system prune` ni `kubectl delete` sobre otros namespaces para limpiar este proyecto.

Si un contenedor termina con codigo 137, revisa `docker inspect` y `docker logs`; ese codigo por si solo no prueba que falte memoria. En la comprobacion previa se observo `OOMKilled=false`, por lo que la causa no se pudo afirmar.

## 6. Pasos que faltan antes de entregar

1. La ETL ya termino y el reporte de esta ejecucion esta en `reports\etl-report.json`. Para evidencia, guardar `kubectl get all,pvc -n solarbi`, `kubectl describe` y `kubectl logs -n solarbi job/solarbi-etl`; no volver a ejecutar `k8s-up` si solo se quiere consultar esta carga.
2. Grafana esta disponible en `http://localhost:30030` mientras siga abierto el tunel Kubernetes. **SolarBI | Operacion de microrred** muestra la simulacion; **SolarBI | Historico OpenCEM** muestra la historia OpenCEM. Si una pestaña abierta antes de terminar el ETL muestra datos vacios, recargarla.
3. Crear el `.pbip` siguiendo `PowerBI\README.md`; validar las tres paginas, relaciones y medidas DAX.
4. Confirmar con fuente oficial el factor SIN del año analizado y con factura la tarifa; sustituir los valores demostrativos y citar fuente, fecha y unidad.
5. Decodificar `fault_history` solo si se confirma la semantica de sus listas y codigos; de lo contrario, declarar como limitacion que no se cargaron alarmas fisicas.
6. Completar nombres, conclusiones, fuentes, capturas y evidencia Kubernetes en `BI_Taller6_Apellido1_Apellido2.pdf`. Publicar proyecto y commits de ambos integrantes en el repositorio GitHub del equipo.

El guia operativa de tareas del equipo esta en [readmePasoAseguir.md](readmePasoAseguir.md); los visuales, relaciones y medidas Power BI estan en [PowerBI/README.md](PowerBI/README.md).
