# Taller 6: requisitos y dónde está cada cosa

Enfoque: SolarBI, CO2 evitado por generación solar con el factor del SIN. Estado de cada requisito en el momento de la entrega; lo que no se ha podido comprobar aquí se marca como tal.

| Sección | Requisito | Dónde está | Estado |
|---|---|---|---|
| A | Usuarios, preguntas y diagnóstico de la fuente | [docs/NEGOCIO_Y_FUENTE.md](docs/NEGOCIO_Y_FUENTE.md), `reports/etl-report.json` | Hecho |
| B | Catálogo de indicadores (solar, carga, red, disponibilidad, % válido por inversor, día, mes y periodo) | [docs/INDICADORES.md](docs/INDICADORES.md), medidas de [PowerBI/Medidas.dax](PowerBI/Medidas.dax) | Hecho |
| B | Al menos dos indicadores híbridos | Dependencia de la red, autosuficiencia, horas con SoC bajo, horas sin red | Hecho |
| B | Indicador en COP con tarifa citada | Ahorro solar y costo de red; 790,49 COP/kWh (EPM, estrato 3, dic. 2025) | Hecho |
| B | Análisis crítico del PR | [docs/INDICADORES.md](docs/INDICADORES.md) | Hecho |
| C | Modelo dimensional: grano, matriz de bus, dimensión propia, eventos, SCD | [docs/MODELO.md](docs/MODELO.md), `sql/schema.sql` | Hecho (SCD descrito sobre `dim_inverter`; sin cambios reales que demostrar) |
| D | Diccionario con evidencia de unidades | [docs/DICCIONARIO.md](docs/DICCIONARIO.md) | Hecho |
| E | ETL con reglas, conteos antes y después, evento real frente a dato inválido, frecuencia de Silver, conciliación, idempotencia | [docs/CALIDAD_Y_ETL.md](docs/CALIDAD_Y_ETL.md), `app/etl.py`, `app/core.py`, `tests/` | Hecho; corrida completa ejecutada y conciliada |
| F | DDL en PostgreSQL, consulta JSON y SQL analítico | `sql/schema.sql`, `sql/consultas.sql` (13 consultas, ejecutadas) | Hecho. Falta la captura de DBeaver (ver [evidencias](docs/DEMO_Y_EVIDENCIAS.md)) |
| G | Power BI: conexión a PostgreSQL, tabla de fechas, inteligencia de tiempo, 3 o más páginas con navegación, segmentadores y drill-down, balance energético, what-if | `PowerBI/` y capturas en `docs/img/powerbi-*.png` | Hecho; abierto y verificado contra la base completa |
| H | Grafana: fuente de datos, series, estado por inversor, variables | `grafana/provisioning/datasources`, `grafana/dashboards/*.json` | Hecho y validado por consultas; sin capturas (ver abajo) |
| H | Al menos dos alertas con umbral justificado | `grafana/provisioning/alerting/alert-rules.yml`; justificación en [docs/INDICADORES.md](docs/INDICADORES.md) | Hecho |
| H | Simulador de streaming como carga de trabajo del cluster | `app/simulator.py`, `k8s/simulator.yml` | Hecho; ejecutado contra PostgreSQL |
| I | Diagrama de arquitectura, Power BI frente a Grafana, evolución Modbus/SCADA | [docs/ARQUITECTURA.md](docs/ARQUITECTURA.md) | Hecho |
| J | Ley 1581 y seguridad | [docs/ETICA_Y_SEGURIDAD.md](docs/ETICA_Y_SEGURIDAD.md) | Hecho; falta la confirmación del docente |
| K | Docker: imagen propia y Compose | `Dockerfile`, `docker-compose.yml`, `run.ps1` | Escrito y revisado; no ejecutado en la máquina de edición (sin Docker) |
| K | Kubernetes: namespace, StatefulSet y PVC, Secret, ConfigMap, DNS, Job, simulador, sondas, requests y límites, acceso desde el anfitrión, un comando para subir y bajar | `k8s/`, `run.ps1 k8s-up`, `run.ps1 k8s-down` | Escrito y revisado; la corrida en el cluster y sus capturas las produce quien tenga Docker Desktop |
| K | Demostración de borrar un pod | [README](README.md) y [docs/DEMO_Y_EVIDENCIAS.md](docs/DEMO_Y_EVIDENCIAS.md) | Procedimiento listo; falta evidencia |
| GitHub | README con cita, `.gitignore`, sin credenciales, commits de los integrantes | este repositorio | `.env` ignorado y sin claves; los commits los hace cada integrante |
| Entrega | PDF `BI_Taller6_Apellido1_Apellido2.pdf` con portada, enlace al repositorio, componentes, capturas y evidencia de `kubectl` | carpeta de entrega | Armado; faltan las capturas de Grafana y de `kubectl` |

## Cómo se verificó lo que sí se pudo

- ETL completo contra PostgreSQL 18 local con los 2.989.759 registros: conciliación exacta (reporte en `reports/etl-report.json`) y repetición con `--from-bronze` con los mismos conteos.
- Las 13 consultas de `sql/consultas.sql` se ejecutaron sin errores, y los totales de energía de Gold se contrastaron con un recálculo independiente en pandas desde el SQLite.
- Power BI Desktop abrió `SolarBI.pbip`, cargó los datos de la base y sus medidas coincidieron con las consultas SQL (energía solar, red importada, CO2, disponibilidad, datos válidos, episodios).
- Grafana 13.2.3 cargó sin errores la fuente de datos, los dos tableros y las tres reglas desde los archivos de aprovisionamiento. La conexión al plugin de PostgreSQL de esa instalación la bloqueó una directiva de control de aplicaciones de Windows, así que las consultas de los 22 paneles, variables y alertas se ejecutaron directamente en PostgreSQL con el rol lector (22 de 22 correctas) en lugar de verse renderizadas.
- El simulador escribió lecturas cada segundo en una base de demostración y generó eventos de batería baja y la alerta aleatoria.
- Las pruebas unitarias (`tests/test_core.py`) pasan.

No se pudo verificar en esta máquina: construcción de la imagen Docker, `docker compose up`, la aplicación de los manifiestos en Kubernetes y los tableros de Grafana dibujados. Eso debe correrse con Docker Desktop antes de la sustentación, partiendo de cero con `.\run.ps1 k8s-up`.
