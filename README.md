# SolarBI - microrred OpenCEM

Implementacion del enfoque **“SolarBI: emisiones de CO2 evitadas por la generacion solar con el factor del SIN”** del Taller 6 de BI. Implementa ETL repetible, capas Bronze/Silver/Gold en PostgreSQL, modelo dimensional, panel operativo Grafana aprovisionado, simulador de datos recientes y configuracion de despliegue Docker Compose/Kubernetes.

> **Importante:** el stream de Grafana es sintetico y se etiqueta `is_simulated`. No son muestras reales del inversor. Las emisiones evitadas son una estimacion a partir de PV1 y de un factor configurable, no una reduccion certificada. Los valores iniciales de factor y tarifa son demostrativos.

## Estructura

```text
SolarBI/
|-- app/                  # ETL, calidad, simulador
|-- data/bronze/          # Entrada local ignorada por Git
|-- docs/                 # Arquitectura, modelo, calidad, etica y KPI
|-- grafana/              # Dashboard y provisioning versionados
|-- k8s/                  # Namespace, PVC, StatefulSet, Job y Deployments
|-- PowerBI/              # Guia, medidas DAX y proyecto PBIP que completar
|-- reports/              # Reporte generado por ETL, ignorado por Git
|-- sql/                  # DDL y consultas analiticas
|-- tests/                # Pruebas focalizadas
|-- docker-compose.yml
|-- Dockerfile
|-- readmePasoAseguir.md  # Tareas locales/externas pendientes
`-- run.ps1               # Comandos de un paso para Windows
```

La correspondencia requisito-por-requisito, el estado real y el inventario de las fuentes estan en [README_TALLER6.md](README_TALLER6.md).

## Inicio rapido Docker

Requiere Docker Desktop con motor Linux. Copia el archivo descargado `opencem-v1.0.0.sqlite.zst` a `data\bronze\` (no se versiona) y ejecuta desde PowerShell:

```powershell
.\run.ps1 up
```

El primer arranque crea `.env` con claves locales aleatorias, construye una imagen Python propia, inicia PostgreSQL, descomprime/valida/carga los datos una sola vez, y arranca un simulador que agrega dos muestras por segundo. Grafana escucha en `http://localhost:3000`; usuario y clave estan en `.env`. DBeaver y Power BI se conectan a `localhost:5432`, base `solarbi`. La primera carga puede tardar segun el disco/memoria; deja la terminal activa para ver logs. `.\run.ps1 down` detiene Compose sin borrar volumenes. Ejecutar nuevamente el ETL es idempotente.

## Kubernetes local

En Docker Desktop habilita Kubernetes (un nodo) y comprueba que `kubectl config current-context` sea `docker-desktop`. Luego:

```powershell
.\run.ps1 k8s-up
```

Si el Job ETL ya esta activo o completo, el script lo reutiliza; no vuelvas a ejecutarlo solo para abrir los dashboards. Para recalcular intencionalmente el historico, usa `.\run.ps1 k8s-up -ForceEtl`.

El script crea el namespace `solarbi`, PVC, Secret desde `.env`, copia el archivo comprimido al PVC local, compila la imagen, espera el Job ETL y lanza Grafana y el simulador. Al terminar el Job, copia `reports/etl-report.json` desde el PVC mediante un pod temporal de transferencia; no intenta ejecutar comandos dentro del pod ETL ya completado. En este equipo los NodePort no responden directamente desde Windows; al terminar, abre dos terminales PowerShell y deja ejecutandose estos tuneles:

```powershell
kubectl port-forward -n solarbi service/grafana 30030:3000
kubectl port-forward -n solarbi service/postgres-nodeport 30432:5432
```

Grafana queda en `http://localhost:30030`; PostgreSQL para Power BI/DBeaver, en `localhost:30432`. El cluster es un requisito previo de una sola vez. `.\run.ps1 k8s-status` muestra recursos y eventos. `.\run.ps1 k8s-down` requiere confirmacion y elimina tambien datos persistidos del namespace.

Los manifiestos eligen: StatefulSet/PVC para PostgreSQL; Deployment/PVC para Grafana; Job para ETL historico; Deployment de una replica para simulacion; Services NodePort para Power BI/DBeaver; ConfigMap para configuracion y Secret para credenciales. Se documenta el modelo en [docs/ARQUITECTURA.md](docs/ARQUITECTURA.md).

## ETL, calidad y seguridad

- Bronze SQLite se abre en solo lectura y pasa `PRAGMA quick_check`.
- Silver valida timestamps, inversor, potencias negativas y SoC. Los nulos y huecos se conservan; no se imputan.
- Gold calcula energias con integracion trapezoidal y descarta intervalos >5 min.
- Claves naturales hacen el proceso idempotente; el reporte de conteos y conciliacion queda en `reports/etl-report.json`.
- Metadatos de contexto se filtran por lista permitida y el texto libre se descarta.
- El canal de carga es fase A (`outw_a`); no afirmar que es carga trifasica total. PV1 es proxy solar, y no hay datos suficientes para PR ni energia exportada neta.
- No subas SQLite, archivos `.env`, credenciales o capturas con secretos a GitHub.

Si una transformacion falla despues de que Bronze se haya cargado por completo, puedes reconstruir Silver/Gold sin volver a importar las 3 millones de lecturas:

```powershell
docker compose run --rm etl python -m app.etl --from-bronze
```

El modo comprueba primero el hash del archivo y que los conteos de Bronze coincidan con SQLite; si falta algo, falla explicitamente. Para una carga inicial o Bronze incompleto, usa `.\run.ps1 up`.

Para borrar los volumenes de Compose y el namespace SolarBI de Kubernetes (requiere confirmar escribiendo `BORRAR SOLARBI`), usa `.\run.ps1 clean`. La fuente descargada, `.env`, imagenes y reportes se conservan. `.\run.ps1 down` solo detiene Compose sin borrar la base.

Ver [docs/CALIDAD_Y_ETL.md](docs/CALIDAD_Y_ETL.md), [docs/DICCIONARIO.md](docs/DICCIONARIO.md), [docs/MODELO.md](docs/MODELO.md), [docs/NEGOCIO_Y_FUENTE.md](docs/NEGOCIO_Y_FUENTE.md), [docs/INDICADORES.md](docs/INDICADORES.md) y [docs/ETICA_Y_SEGURIDAD.md](docs/ETICA_Y_SEGURIDAD.md).

## Power BI y Grafana

Grafana tiene datasource, variables, dos dashboards y tres reglas de alerta provisionados desde archivos. **SolarBI | Operacion de microrred** muestra la simulacion y se refresca cada cinco segundos; **SolarBI | Historico OpenCEM** consulta por dia las filas historicas no simuladas y su CO2 estimado, sin refresco automatico para no releer millones de filas innecesariamente. Para correo/SMS se requiere configurar un contact point SMTP u otro receptor en Grafana.

El panel **Alertas/eventos sinteticos recientes** y la regla **Alerta aleatoria de demostracion sintetica** muestran eventos generados por el simulador; son intencionalmente ficticios y no representan fallas del equipo.

Power BI se conecta a Gold en PostgreSQL, no a los CSV ni al SQLite. Sigue [PowerBI/README.md](PowerBI/README.md), usa las medidas de [PowerBI/Medidas.dax](PowerBI/Medidas.dax) y guarda el proyecto como `.pbip` desde Power BI Desktop.

## Datos y cita

La base fuente se distribuye comprimida y se debe conservar fuera de Git. Se verifican checksums contra `opencem-v1.0.0-SHA256SUMS.txt`; el archivo de soporte documenta esquema y unidades. El repositorio CSV `opencem-dataset-main` es una referencia complementaria, no sustituye la SQLite v1.0.0.

Cita requerida:

> Bartels, T. S., Wu, R., Lu, X., Chen, Y., Lu, Y., Xia, F., Yang, H., Chen, Y., & Li, T. (2026). *OpenCEM: Multimodal photovoltaic and battery microgrid dataset with context records and sky imagery* (Version 1.0.0) [Dataset]. Zenodo. https://doi.org/10.5281/zenodo.21436223

Datos bajo CC BY 4.0; software de OpenCEM bajo MIT en sus directorios de codigo. El nuevo trabajo del taller no sustituye ni relicencia los datos.

## Configuracion del enfoque

Cambiar factor y tarifa en `.env` para Docker. Para Kubernetes, cambiar los parametros correspondientes de `k8s/configmap.yml` y volver a aplicar el ConfigMap/reiniciar simulator. Antes de reportar, verificar año, fuente oficial UPME para el factor SIN y tarifa facturada. Ver [readmePasoAseguir.md](readmePasoAseguir.md) para las tareas manuales y evidencias.

## Validacion

Pruebas del nucleo sin dependencias externas:

```powershell
python -m unittest discover -s tests -v
```

`docker compose config` valida la sintaxis de Compose; `.\run.ps1 up` construye la imagen y ejecuta el flujo integrado.
