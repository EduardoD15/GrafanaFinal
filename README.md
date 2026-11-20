# SolarBI: CO2 evitado por generación solar en una microrred

Taller 6 de Inteligencia de Negocios (Pascual Bravo). Solución de BI contenedorizada y orquestada con Kubernetes sobre los datos de la microrred OpenCEM. Enfoque: **emisiones de CO2 evitadas por la generación solar con el factor del SIN**.

Integrantes: Samuel Vélez Guzmán, Tomás Restrepo Pulgarin, Eduardo Deossa Bohórquez y Kevin Patiño Duque.

> El flujo de Grafana es sintético y está marcado como tal (`is_simulated`). El CO2 es una estimación sobre el canal PV1 con un factor oficial citado, no una reducción medida.

## Contenido

```text
app/          ETL, reglas de calidad y simulador
sql/          DDL (schema.sql) y consultas analíticas (consultas.sql)
grafana/      Dashboards, fuente de datos y alertas aprovisionados
k8s/          Manifiestos de Kubernetes
PowerBI/      Proyecto PBIP (modelo TMDL e informe), medidas DAX
docs/         Documentación del taller y capturas (docs/img)
tests/        Pruebas unitarias del núcleo del ETL
docker-compose.yml, Dockerfile, run.ps1
```

La correspondencia entre cada requisito del taller y el archivo que lo cubre está en [README_TALLER6.md](README_TALLER6.md).

## Datos

Se usa la base SQLite oficial `opencem-v1.0.0.sqlite.zst` (302 MB; no se versiona). Descargarla de Zenodo, comprobar su SHA-256 con `data/bronze/opencem-v1.0.0-SHA256SUMS.txt` y copiarla a `data/bronze/`.

Cita:

> Bartels, T. S., Wu, R., Lu, X., Chen, Y., Lu, Y., Xia, F., Yang, H., Chen, Y., & Li, T. (2026). *OpenCEM: Multimodal photovoltaic and battery microgrid dataset with context records and sky imagery* (Version 1.0.0) [Dataset]. Zenodo. https://doi.org/10.5281/zenodo.21436223

Datos bajo CC BY 4.0. Factor de emisión: Resolución UPME 085 de 2026 (SIN, inventarios 2024: 0,220 tCO2eq/MWh). Tarifa de referencia: EPM estrato 3, diciembre de 2025 (790,49 COP/kWh).

## Puesta en marcha

Requisitos: Docker Desktop con Kubernetes activado (contexto `docker-desktop`) y PowerShell.

Con Compose:

```powershell
.\run.ps1 up          # construye, carga datos, levanta PostgreSQL, simulador y Grafana
.\run.ps1 down        # detiene conservando los datos
```

Con Kubernetes:

```powershell
.\run.ps1 k8s-up      # namespace, volúmenes, secretos, PostgreSQL, ETL, Grafana y simulador
.\run.ps1 k8s-status
.\run.ps1 k8s-down    # elimina el namespace (pide confirmación)
```

Para una demostración rápida se agrega `-Demo`, que carga solo del 1 al 14 de marzo de 2026 (unas 191 mil lecturas en lugar de 2,99 millones), mucho más rápido para una demostración.

La primera ejecución crea `.env` con claves aleatorias; no se sube al repositorio. Detalle de acceso:

| Servicio | Compose | Kubernetes |
|---|---|---|
| Grafana | http://localhost:3000 | http://localhost:30030 (túnel) |
| PostgreSQL | localhost:5432 | localhost:30432 (túnel) |

En Windows los NodePort de Docker Desktop pueden no responder; los túneles son:

```powershell
kubectl port-forward -n solarbi service/grafana 30030:3000
kubectl port-forward -n solarbi service/postgres-nodeport 30432:5432
```

Prueba de recuperación: `kubectl delete pod -n solarbi -l app=postgres` y verificar que el pod vuelve con los mismos datos; igual con `app=grafana` y el tablero aprovisionado.

## Qué hay en cada pieza

- **ETL** ([docs/CALIDAD_Y_ETL.md](docs/CALIDAD_Y_ETL.md)): verifica el hash, carga a Bronze, aplica reglas de calidad en Silver y construye el modelo dimensional en Gold. Repetirlo no duplica datos. Deja `reports/etl-report.json` con conteos antes y después y la conciliación.
- **Modelo** ([docs/MODELO.md](docs/MODELO.md), [docs/DICCIONARIO.md](docs/DICCIONARIO.md)): estrella con tres dimensiones (fecha, hora, inversor) y cuatro hechos (lecturas, resumen por hora, calidad por día, eventos).
- **Indicadores** ([docs/INDICADORES.md](docs/INDICADORES.md)): energía, red, batería, disponibilidad, datos válidos, CO2, ahorro en COP, indicadores híbridos y el análisis de por qué no se calcula el PR.
- **SQL** (`sql/consultas.sql`): 13 consultas con agregaciones, ventanas, `LAG`, rankings y operadores JSON de PostgreSQL.
- **Power BI** ([PowerBI/README.md](PowerBI/README.md)): proyecto PBIP con cinco páginas, navegación, segmentadores, drill-down, inteligencia de tiempo y tres parámetros what-if.
- **Grafana** (`grafana/`): fuente de datos, dos tableros (operación e histórico), variables de inversor y tarifa y tres reglas de alerta, todo aprovisionado por archivos.
- **Arquitectura** ([docs/ARQUITECTURA.md](docs/ARQUITECTURA.md)): diagrama, objetos de Kubernetes con su justificación, Power BI frente a Grafana y evolución a Modbus/SCADA.
- **Ética y seguridad** ([docs/ETICA_Y_SEGURIDAD.md](docs/ETICA_Y_SEGURIDAD.md)).
- **Evidencias y demostración** ([docs/DEMO_Y_EVIDENCIAS.md](docs/DEMO_Y_EVIDENCIAS.md)).

## Pruebas

```powershell
python -m unittest tests.test_core -v
```
