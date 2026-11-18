# Calidad, ETL y conciliacion

## Secuencia

1. Verificar SHA-256 contra el manifiesto oficial; luego comprobar que SQLite responda `PRAGMA quick_check = ok` y contenga `analog_measurements` y `context`.
2. Leer el SQLite en modo solo lectura. Importar lecturas y contexto por lotes con claves naturales; reejecucion usa UPSERT.
3. Persistir snapshots seleccionados en Bronze PostgreSQL. El archivo Bronze original queda inmutable.
4. Silver conserva los valores nulos; no rellena lecturas faltantes. Rechaza timestamp fuera de 2000-2100, inversor que no sea 1/2, potencia PV/salida negativa o SoC fuera de 0-100%. Un hueco temporal no se clasifica como dato invalido.
5. Gold calcula energia trapezoidal y no integra huecos superiores a cinco minutos. Contexto JSON se minimiza para descartar texto libre.
6. El ETL inventaria cada tabla SQLite (columnas, tipos, filas y rango temporal) y genera `reports/etl-report.json` con el SHA-256 verificado, conteos antes/despues, rechazos y conciliacion de Bronze, Silver y Gold.

La repeticion usa `(read_ts, inverter)` y `context.id`; no duplica filas y recalcula Gold. Silver se mantiene a grano de lectura. Una agregacion a minuto perderia picos, huecos y resolucion de alertas; por volumen se recomienda particionar Bronze/Silver por fecha al pasar a produccion.

## Interpretacion de alertas/eventos

La calidad juzga si un registro se puede analizar. Una lectura SoC baja valida es un evento operacional, no un error de calidad. La falta de datos es una alerta de disponibilidad, no un valor imputado. Exportacion de red negativa tampoco es importacion negativa: el KPI de importacion se trunca a cero.

## Criterios de conciliacion

- Bronze: `bronze_measurements` del reporte debe igualar filas de `analog_measurements` (claves unicas).
- Silver: validas + rechazadas debe igualar Bronze.
- Gold historico: filas deberian igualar validas; las energias NULL en el ultimo punto o tras huecos >5 min son esperadas.
- Contexto: importados y transformados se reportan por separado porque timestamps fuera de rango pueden filtrarse.

El resumen incluye la cobertura y las reglas aplicadas. El archivo comprimido se comprueba por SHA-256 antes de la carga.
