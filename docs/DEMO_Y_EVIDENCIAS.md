# Evidencias para el informe y la sustentacion

Guarde evidencias fuera de la base cruda y sin claves:

1. SHA-256 de los cuatro archivos descargados.
2. Inventario SQLite: tablas, columnas, filas, min/max por fecha.
3. `reports/etl-report.json`, reglas, rechazos y conciliacion.
4. Esquema dimensional y consultas SQL con resultados.
5. Capturas Power BI (analitica historica) y Grafana (simulador con etiqueta de datos simulados y alertas).
6. `kubectl get all,pvc -n solarbi` y `kubectl describe` de PostgreSQL/Grafana.
7. Eliminar el pod PostgreSQL y Grafana por nombre; verificar que regresan y que los datos/dashboard persisten.
8. Commits de cada integrante, informe PDF con nombres, repositorio y recomendaciones.

No presentar muestras sinteticas como telemetria real ni CO2 estimado como reduccion certificada.
