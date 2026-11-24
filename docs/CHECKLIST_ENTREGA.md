# Checklist de Validacion y Entrega - SolarBI

- [x] Dataset OpenCEM verificado mediante SHA-256 en data/bronze/.
- [x] Cluster local desplegado en Kubernetes (namespace: solarbi) con almacenamiento persistente.
- [x] Proceso ETL completado (job/solarbi-etl) con factor oficial UPME (0.220 kg CO2/kWh).
- [x] Simulador en tiempo real activo (deployment/simulator).
- [x] Tableros Historico, Operacion en vivo y Alertas verificados en Grafana (localhost:30030).
- [x] Prueba de resiliencia ejecutada eliminando postgres-0 con recuperacion automatica sin perdida de datos.
