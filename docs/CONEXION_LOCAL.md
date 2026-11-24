# Guia de Tuneles y Conexion Local (Windows + Docker Desktop)

Para exponer los servicios del cluster hacia herramientas locales en Windows, mantener abierta una terminal por cada tunel:

| Servicio | Comando de tunel | Endpoint local | Uso |
| :--- | :--- | :--- | :--- |
| Grafana | kubectl port-forward -n solarbi service/grafana 30030:3000 | http://localhost:30030 | Tableros Operacion e Historico y Alertas |
| PostgreSQL | kubectl port-forward -n solarbi service/postgres-nodeport 30432:5432 | localhost:30432 | Consultas en DBeaver y modelo en Power BI |
