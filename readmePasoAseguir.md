# Pasos que requieren tu participacion

El proyecto queda organizado en esta carpeta. La tarifa y el factor oficial requieren que confirmes sus fuentes; algunas acciones de entrega tambien dependen de tu cuenta y del docente. Sigue estos pasos antes de entregar/sustentar:

## Estado actual verificado

- La fuente SQLite esta descomprimida en `data\bronze\opencem-v1.0.0.sqlite` (4,439,408,640 bytes); se conserva tambien el archivo comprimido.
- En Gold hay 2,989,759 lecturas historicas validas y 25,178 contextos/eventos conciliados. El Job `solarbi-etl` termino en 54 minutos; se recupero `reports\etl-report.json` desde el PVC y se verificaron esos conteos.
- Kubernetes esta habilitado y el contexto `docker-desktop` responde. PostgreSQL, Grafana y el simulador estan `Running`; el Job `solarbi-etl` esta `Complete` y las PVC estan `Bound`. No uses `-ForceEtl` salvo que quieras recalcular intencionalmente el historico.
- PostgreSQL contiene 10,942 muestras simuladas en la ultima consulta, 1,780 de ellas dentro de los ultimos 15 minutos. La salud del datasource de Grafana es `OK` y una consulta directa devuelve filas; la pestaña compartida, sin embargo, mostro `No data` y errores de sesion WebSocket `401`/rotacion de token. Usa el puerto Kubernetes `http://localhost:30030` y vuelve a iniciar sesion con las credenciales de `.env`; `localhost:3000` corresponde a Compose.
- Los NodePort `localhost:30030` y `localhost:30432` no responden directamente desde Windows en este equipo. Tras completar el ETL, usa los comandos `kubectl port-forward` de la seccion 2 y deja esas terminales abiertas para ver Grafana y conectar Power BI.
- El PVC de PostgreSQL ahora pide 10 GiB porque el almacen historico llego a ocupar aproximadamente 6.3 GB. El cluster local anuncia aproximadamente 3.9 GiB de memoria, por lo que ejecutar ETL y otros contenedores puede tardar y requiere vigilar sus recursos.
- Se agrego al dashboard una tabla para alertas/eventos sinteticos y una regla que se activa con eventos aleatorios de demostracion. No son fallas reales ni telemetria del inversor.
- El Deployment del simulador ya esta activo y escribe muestras sinteticas; las alertas aleatorias son solo de demostracion. No ejecutes Compose junto con Kubernetes.
- Power BI Desktop esta instalado y abierto, pero el informe actual sigue vacio: no tiene tablas importadas y solo muestra `Pagina 1`. Aun no existe `SolarBI.pbip`. El taller exige minimo tres paginas; sigue la guia para construirlas.

## 1. Insumos (primera vez)

1. Instala/actualiza **Docker Desktop** y deja iniciado el motor Linux.
2. El archivo comprimido `opencem-v1.0.0.sqlite.zst` y el manifiesto oficial ya estan en `SolarBI\data\bronze\`; no hace falta volver a descargarlos ni descomprimirlos en este equipo. El proyecto ignora estos archivos y no los sube a GitHub. Si preparas otra copia del proyecto, coloca alli el archivo y el manifiesto.
3. Si necesitas verificar los archivos originales, compara sus hashes con el manifiesto oficial `SolarBI\data\bronze\opencem-v1.0.0-SHA256SUMS.txt`. En este equipo ya fueron verificados.

4. Cuando la ejecucion actual termine, para iniciar Compose en el futuro desde `SolarBI`, ejecuta:

   ```powershell
   .\run.ps1 up
   ```

   Compose compila la imagen, ejecuta el ETL y, cuando este termina correctamente, inicia el simulador y Grafana. La carga inicial puede tardar varios minutos. El simulador escribe dos lecturas por segundo (una por inversor), pero no representa telemetria real. `Ctrl+C` detiene los servicios; los datos PostgreSQL quedan en el volumen.
5. Abre Grafana en `http://localhost:3000`. Las credenciales aleatorias se escriben solo en `SolarBI\.env`; consultalas alli, no las publiques. El dashboard se llama **SolarBI | Operacion de microrred**.
6. El reporte de ETL queda en `SolarBI\reports\etl-report.json`. Si el puerto 5432 o 3000 esta ocupado, cambia el puerto en `.env`.

## 2. Dejar Kubernetes listo (una vez)

Docker Compose y Kubernetes son opciones distintas: que Docker este instalado o que Compose funcione no significa que exista un cluster Kubernetes activo. Por eso los recursos Kubernetes no aparecen hasta habilitar el cluster local.

1. Kubernetes local esta habilitado y el contexto `docker-desktop` respondio en la ultima verificacion. Si algun dia aparece apagado, abre **Settings > Kubernetes** en Docker Desktop y espera a que el cluster local de un nodo este corriendo. No se requiere nube ni Helm.
2. Verifica en PowerShell:

   ```powershell
   kubectl config current-context
   kubectl cluster-info
   ```

   El contexto debe ser `docker-desktop`. `kubectl` ya estaba instalado en el equipo al revisar la carpeta.
3. El despliegue ya esta creado. Para iniciarlo o actualizar la configuracion (desde `SolarBI`):

   ```powershell
   .\run.ps1 k8s-up
   ```

   El script compila la imagen, aplica los recursos y deja el simulador activo. Reutiliza un Job ETL que ya este en curso o haya terminado; la carga inicial de esta fuente tardo aproximadamente 49 minutos. Solo recalcula intencionalmente con `.\run.ps1 k8s-up -ForceEtl`. En este equipo, deja abierta una terminal con el tunel de Grafana:

   ```powershell
   kubectl port-forward -n solarbi service/grafana 30030:3000
   ```

   Para Power BI/PostgreSQL, abre otra terminal y deja tambien este tunel:

   ```powershell
   kubectl port-forward -n solarbi service/postgres-nodeport 30432:5432
   ```

   Abre Grafana en `http://localhost:30030`; DBeaver/Power BI se conectan a `localhost:30432`.
4. Evidencia y estado: `.\run.ps1 k8s-status`; el resultado ETL puede revisarse con `kubectl logs -n solarbi job/solarbi-etl`.
5. Para borrar el despliegue ejecuta `.\run.ps1 k8s-down` y confirma `BORRAR`. **Esto elimina el namespace y sus volumenes persistentes.** No lo ejecutes si necesitas conservar la carga.

## 3. Completar BI y factores oficiales

1. Espera a que termine el Job ETL (`kubectl get job -n solarbi solarbi-etl` debe indicar `Complete`) para no importar mientras se recalcula el historico. Power BI Desktop ya esta instalado y abierto en este equipo. En la pantalla Inicio, selecciona **Obtener datos de otros origenes > Base de datos PostgreSQL**; elige servidor `localhost:30432` (con el tunel Kubernetes abierto), base `solarbi` y modo **Importar**. Usa las credenciales locales de `SolarBI\.env`; no las publiques ni las guardes en el repositorio.
2. Abre `SolarBI\PowerBI\README.md` y sigue el modelo sugerido. Crea al menos tres paginas, relaciones y tabla de fechas, agrega las medidas de `Medidas.dax`, segmentadores, drill-down, navegacion y un parametro What-if. Guarda como `.pbip` dentro de `SolarBI\PowerBI\`; evita subir un PBIX con datos/credenciales.
3. Confirma con el profesor/la fuente institucional la tarifa real en COP/kWh. Confirma tambien el factor de emision **del SIN colombiano para el año que se va a reportar**, en una fuente oficial UPME. Reemplaza `TARIFF_COP_PER_KWH` y `CO2_FACTOR_KG_PER_KWH` en `.env` y `SolarBI\k8s\configmap.yml`; anota URL, documento, fecha y unidad en el informe. Los valores actuales `1000` y `0.112` son demostrativos/provisionales, no deben presentarse como vigentes sin verificar.
4. Agrega nombres de integrantes, enlace al repo GitHub, commits de ambos, conclusiones con datos, fuentes oficiales, capturas sin contraseñas y la evidencia del cluster en el informe PDF llamado `BI_Taller6_Apellido1_Apellido2.pdf`.
5. Revisa con el docente el tratamiento de contexto bajo Ley 1581 de 2012 y si la minimizacion del texto libre es suficiente para la entrega.

## 4. Comandos utiles

| Accion | Compose | Kubernetes |
|---|---|---|
| Iniciar | `.\run.ps1 up` | `.\run.ps1 k8s-up` |
| Estado | `.\run.ps1 status` | `.\run.ps1 k8s-status` |
| Detener conservando DB | `.\run.ps1 down` | — |
| Borrar DB/volumenes del proyecto | `.\run.ps1 clean` | incluido; confirma `BORRAR SOLARBI` |
| Logs | `.\run.ps1 logs` | `kubectl logs -n solarbi deploy/simulator` |

Compose y Kubernetes son dos formas alternativas de levantar la misma aplicacion. No es necesario ejecutar ambos a la vez. `.\run.ps1 clean` conserva los archivos fuente descargados, la imagen Docker, `.env` y los reportes; borra unicamente los volumenes y recursos del proyecto.

Para demostrar recuperacion en Kubernetes: `kubectl delete pod -n solarbi -l app=postgres`, espera a que el StatefulSet recupere el pod y verifica filas Gold; repite con `app=grafana` y verifica el dashboard provisionado. Captura `kubectl get all,pvc -n solarbi` y `kubectl describe pod` para el PDF.
