# Pasos pendientes del equipo

Lo que no se puede hacer desde la máquina donde se armó el proyecto (no tiene Docker ni Kubernetes) y lo que depende de cada integrante.

## 1. Levantar el cluster desde cero (quien tenga Docker Desktop)

1. Docker Desktop con Kubernetes activado y el contexto `docker-desktop`:

   ```powershell
   kubectl config current-context
   ```

2. Copiar `opencem-v1.0.0.sqlite.zst` a `data\bronze\` (no se versiona) y comprobar el hash contra `data\bronze\opencem-v1.0.0-SHA256SUMS.txt`.
3. Desde la raíz del repositorio:

   ```powershell
   .\run.ps1 k8s-up
   ```

   La primera vez genera `.env`. La carga completa tarda en un cluster de Docker Desktop (la versión anterior de la ETL tardó unos 50 minutos); para ensayar, `.\run.ps1 k8s-up -Demo`. Si el cluster ya tiene datos de una versión anterior, borrar con `.\run.ps1 k8s-down` antes, porque cambiaron las reglas de la ETL y hay que recalcular Gold.
4. Abrir los túneles (dos terminales que quedan abiertas):

   ```powershell
   kubectl port-forward -n solarbi service/grafana 30030:3000
   kubectl port-forward -n solarbi service/postgres-nodeport 30432:5432
   ```

5. Comprobar: `.\run.ps1 k8s-status`, Grafana en http://localhost:30030 (usuario y clave en `.env`), dos tableros en la carpeta SolarBI y tres reglas en Alerting.

## 2. Evidencia para el PDF

Capturar sin mostrar claves:

- `kubectl get all,pvc,configmap,secret -n solarbi` (los Secret sin `-o yaml`).
- `kubectl describe pod` de PostgreSQL y de Grafana (sondas, requests y límites).
- `kubectl logs -n solarbi job/solarbi-etl` (conteos y conciliación) y `kubectl logs -n solarbi deploy/simulator`.
- Grafana: tablero de operación con la etiqueta de datos simulados, tablero histórico, estado de carga por inversor, lista de reglas de alerta y una alerta disparada (la aleatoria de demostración sale sola en 1 a 2 minutos; la de batería baja se dispara cuando el SoC simulado cae de 20 %).
- DBeaver conectado a `localhost:30432`: esquemas `bronze`, `silver`, `gold`, el DDL y el resultado de la consulta JSON (la 10 de `sql/consultas.sql`).
- Borrar un pod y ver que vuelve con datos:

  ```powershell
  kubectl delete pod -n solarbi -l app=postgres
  kubectl get pods -n solarbi -w
  ```

  y lo mismo con `app=grafana`.
- Recuperar el reporte de la ETL (`reports\etl-report.json`) y guardarlo.

## 3. Power BI

Abrir `PowerBI\SolarBI.pbip`, ajustar `Servidor` a `localhost:30432` si se usa Kubernetes (Transformar datos > Editar parámetros), iniciar sesión con `solarbi_reader` y la clave `BI_READER_PASSWORD` de `.env`, y actualizar. Los valores de referencia que deben salir con la carga completa están en `PowerBI\README.md`. Las capturas de las cinco páginas ya están en `docs\img`; repetirlas solo si cambia algo.

## 4. GitHub

- Subir el repositorio sin `.env`, sin la base y sin `data\bronze` (ya están en `.gitignore`).
- Que cada integrante haga sus propios commits con su usuario; el taller pide aportes incrementales de todos. Sugerencia de reparto: ETL y SQL, Power BI, Grafana y alertas, Kubernetes y documentación.
- Comprobar que el README muestra la cita del dataset y que no hay claves en el historial (`git log -p | findstr /i password`).

## 5. Entrega

- PDF `BI_Taller6_Apellido1_Apellido2.pdf` con portada (nombres de los cuatro integrantes), enlace al repositorio, descripción de cada componente, capturas y la evidencia de `kubectl`.
- Confirmar con el docente el tratamiento del contexto bajo la Ley 1581 de 2012 (ver `docs\ETICA_Y_SEGURIDAD.md`).
- Para la sustentación en vivo: partir del cluster apagado con `.\run.ps1 k8s-up -Demo` (o con la carga completa ya hecha si el docente lo permite) y tener los túneles listos; lo que se cree a mano en Grafana no cuenta como parte de la solución.

## Comandos

| Acción | Compose | Kubernetes |
|---|---|---|
| Subir | `.\run.ps1 up` | `.\run.ps1 k8s-up` |
| Estado | `.\run.ps1 status` | `.\run.ps1 k8s-status` |
| Bajar | `.\run.ps1 down` (conserva datos) | `.\run.ps1 k8s-down` (borra el namespace y sus volúmenes) |
| Borrar todo lo del proyecto | `.\run.ps1 clean` (pide escribir `BORRAR SOLARBI`) | incluido |
| Logs | `.\run.ps1 logs` | `kubectl logs -n solarbi deploy/simulator` |

Compose y Kubernetes son alternativas; no se ejecutan a la vez. Para cambiar el factor de CO2 o la tarifa: `.env` (Compose) o `k8s\configmap.yml` (Kubernetes), y reiniciar el simulador.
