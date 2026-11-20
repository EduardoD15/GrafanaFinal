# Evidencias y guion de la sustentación

## Evidencia ya generada

| Evidencia | Dónde | Qué muestra |
|---|---|---|
| Reporte del ETL | `reports/etl-report.json` en cada corrida; copia del reporte de la última corrida completa en `docs/evidencia/etl-report.json` | Hash verificado, inventario de las 8 tablas, conteos antes y después de cada regla, conciliación, totales de energía |
| Repetición del ETL | tabla de abajo y `docs/evidencia/repeticion-etl.txt` | Misma huella de datos tras repetir |
| Resultados de las 13 consultas | `docs/evidencia/resultados-consultas.txt` (consultas en `sql/consultas.sql`) | Primeras filas de cada consulta sobre la base completa |
| Capturas de Power BI | `docs/img/powerbi-1-resumen.png` a `powerbi-5-escenarios.png` | Las cinco páginas con la carga completa |
| Diagrama de arquitectura | `docs/img/arquitectura.png` | Componentes, namespace y puertos |

### Repetición del ETL (idempotencia)

Huella de la base antes y después de repetir la reconstrucción desde Bronze (`--from-bronze`, unos 3,5 minutos):

| Tabla | Filas antes | Filas después | Suma antes | Suma después |
|---|---:|---:|---:|---:|
| `bronze.measurements_raw` | 2.989.759 | 2.989.759 | | |
| `silver.measurements` (suma de `pv_power_w`) | 2.989.759 | 2.989.759 | 454.695.301 | 454.695.301 |
| `silver.measurements` válidas | 2.934.098 | 2.934.098 | | |
| `gold.fact_energy_reading` (kWh solar) | 2.934.098 | 2.934.098 | 2.346,609763 | 2.346,609763 |
| `gold.fact_energy_hour` (kWh solar) | 14.077 | 14.077 | 2.346,609763 | 2.346,609763 |
| `gold.fact_quality_day` (lecturas recibidas) | 711 | 711 | 2.989.759 | 2.989.759 |
| `gold.fact_event` (segundos de duración) | 25.294 | 25.294 | 547.932.569,102866 | 547.932.569,102867 |

La única diferencia es el último decimal de una suma de decimales (orden de suma distinto), no filas ni valores.

## Evidencia que debe generar quien tenga Docker Desktop

Ver los comandos en [readmePasoAseguir.md](../readmePasoAseguir.md). Lista de capturas para el informe:

1. `kubectl get all,pvc,configmap,secret -n solarbi` después de `.\run.ps1 k8s-up`.
2. `kubectl describe pod` de PostgreSQL y de Grafana (sondas, requests y límites).
3. `kubectl logs -n solarbi job/solarbi-etl` con la conciliación y `kubectl logs -n solarbi deploy/simulator`.
4. Grafana: tablero de operación (con la leyenda de datos simulados), tablero histórico, estado de carga por inversor, lista de reglas y una alerta disparada.
5. DBeaver conectado a `localhost:30432`: esquemas, DDL y resultado de la consulta JSON.
6. Borrado de un pod de PostgreSQL y otro de Grafana, con el pod regresando y los datos intactos.
7. Historial de commits en GitHub con aportes de los cuatro integrantes.

## Verificación hecha sin Docker

La máquina donde se armó el proyecto no tiene Docker ni Kubernetes. Se verificó lo que sí podía comprobarse:

- ETL completo y repetición contra PostgreSQL local.
- Power BI Desktop contra esa base: las medidas coinciden con SQL.
- Grafana 13.2.3 local: cargó sin errores la fuente de datos, los dos tableros y las tres reglas desde `grafana/provisioning`. La ejecución de consultas contra PostgreSQL la bloqueó una directiva de control de aplicaciones de Windows (el binario del plugin de PostgreSQL), así que las 22 consultas (paneles, variables y alertas) se ejecutaron directamente en la base con el rol lector: 22 de 22 correctas, tanto en la carga completa como en la de demostración.
- Simulador: corrida de 100 segundos contra la base de demostración, 100 lecturas por inversor, un evento de batería baja por cada cruce del umbral y una alerta aleatoria etiquetada como demostración.
- Pruebas unitarias: 8 de 8.

Sin comprobar aquí: construcción de la imagen Docker, `docker compose up`, los manifiestos aplicados en un cluster y los tableros de Grafana dibujados.

## Guion de la sustentación

1. **Partir de cero.** Cluster sin el namespace; mostrar `kubectl get ns`. Ejecutar `.\run.ps1 k8s-up -Demo` y explicar mientras corre: el Job carga los datos (conteos en `kubectl logs`), PostgreSQL arranca con su PVC, Grafana se aprovisiona sola.
2. **Qué se creó.** `.\run.ps1 k8s-status`: StatefulSet, Deployments, Job, PVC, Service, ConfigMap, Secret (sin mostrar valores).
3. **Grafana.** Tablero de operación en vivo (señal simulada), cambiar la variable de inversor, esperar la alerta aleatoria o la de batería baja y mostrar la regla. Aclarar que lo creado a mano no hace parte de la solución.
4. **Resiliencia.** `kubectl delete pod -n solarbi -l app=postgres`, ver cómo vuelve y consultar el conteo de `gold.fact_energy_hour`; repetir con Grafana.
5. **Power BI.** Abrir `SolarBI.pbip`: navegación, segmentadores, drill-down del balance y el parámetro de tarifa y factor de CO2.
6. **Preguntas esperables.** Por qué Silver no agrega (ver [CALIDAD_Y_ETL.md](CALIDAD_Y_ETL.md)); por qué no se calcula el PR ([INDICADORES.md](INDICADORES.md)); por qué el simulador es un Deployment y no un CronJob ([ARQUITECTURA.md](ARQUITECTURA.md)); qué cambia con Modbus/SCADA; qué dato personal hay en el contexto y qué se descarta ([ETICA_Y_SEGURIDAD.md](ETICA_Y_SEGURIDAD.md)).

No presentar la señal sintética como telemetría real ni el CO2 estimado como reducción certificada.
