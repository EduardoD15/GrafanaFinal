# Calidad, ETL y conciliación

## Secuencia

El ETL (`python -m app.etl`) corre como Job en Kubernetes o como servicio en Compose y escribe su evidencia en `reports/etl-report.json`.

1. **Verificación.** Calcula el SHA-256 del `.sqlite.zst` y lo compara con el manifiesto oficial; descomprime, abre la base en solo lectura y comprueba `PRAGMA quick_check`.
2. **Inventario.** Registra tabla por tabla las columnas, tipos, filas y rango temporal de la fuente.
3. **Bronze.** Copia `analog_measurements` y `context` por lotes, con `INSERT ... ON CONFLICT DO UPDATE` (upsert). Cada lectura queda con todas sus columnas en un JSONB. El contexto se minimiza antes de escribirse (ver [ETICA_Y_SEGURIDAD.md](ETICA_Y_SEGURIDAD.md)).
4. **Silver.** Tipa las columnas, aplica las reglas de calidad y marca cada fila con `is_valid` e `invalid_reason`. Nada se borra ni se imputa.
5. **Gold.** Reconstruye dimensiones, lecturas con energía, resumen por hora, calidad por día y eventos.
6. **Reporte.** Guarda conteos antes y después de cada regla, la conciliación y los totales de energía.

Tiempos en el equipo de desarrollo (PostgreSQL local, 2,99 millones de lecturas): Bronze unos 3 minutos, Silver 1,6, lecturas Gold 1,2, resumen y calidad 10 segundos, eventos 20 segundos; unos 7 minutos en total. Con `--from-bronze` se omite la importación y se reconstruye Silver y Gold en unos 3,5 minutos.

## Reglas de calidad

Se aplican en orden; cada fila queda con la primera regla que falla. Los conteos vienen de la corrida completa.

| Regla | Condición de rechazo | Antes | Rechazadas | Después |
|---|---|---:|---:|---:|
| `invalid_timestamp` | `read_ts` fuera de 2000 a 2100 | 2.989.759 | 0 | 2.989.759 |
| `unsupported_inverter` | inversor distinto de 1 o 2 | 2.989.759 | 0 | 2.989.759 |
| `empty_snapshot` | ninguno de los canales clave tiene valor (PV, salida, red, SoC, tensión o corriente de batería) | 2.989.759 | 55.658 | 2.934.101 |
| `negative_pv1power` | `pv1power` < 0 | 2.934.101 | 0 | 2.934.101 |
| `negative_outw_a` | `outw_a` < 0 | 2.934.101 | 0 | 2.934.101 |
| `invalid_battery_soc` | `battsoc` fuera de 0 a 100 | 2.934.101 | 0 | 2.934.101 |
| `zero_battery_voltage` | `battvolt` = 0 | 2.934.101 | 3 | 2.934.098 |

Resultado: de 2.989.759 lecturas recibidas quedan 2.934.098 válidas (98,1 %). Por inversor: el inversor 1 pierde 1 lectura; el inversor 2 pierde 55.660 (4,0 %). En contexto, 25.178 registros antes y después: ninguno se rechaza.

Reglas con 0 rechazos se conservan igualmente: documentan lo que se comprobó y protegen cargas futuras.

## Dato inválido frente a evento real

Un valor extraño no siempre es un error. El proyecto separa:

| Situación | Tratamiento | Razón |
|---|---|---|
| Lectura sin ningún canal | Inválida (`empty_snapshot`) | El registro existe pero no contiene medición |
| Tensión de batería igual a 0 | Inválida | Una batería conectada no tiene 0 V; es un fallo de lectura |
| SoC por debajo de 20 % | Válida; si dura, evento `low_battery_soc` | Es un estado real de operación |
| Voltaje de línea menor a 100 V | Válida; si dura, evento `grid_absent` | Un corte o la operación en isla es real y relevante |
| Intervalo mayor a 900 s entre lecturas | Las lecturas son válidas; el intervalo es un evento `data_gap` y no se integra energía a través de él | Falta información, pero no hay un valor malo |
| Red importada negativa | Válida; la importación se trunca a 0 | Exportación no es importación negativa |

Los episodios exigen al menos 2 lecturas consecutivas y se cortan en los huecos, para no tomar un parpadeo aislado ni un hueco de semanas como un solo evento. Esa regla se añadió tras comprobar que la primera versión generaba 1.617 eventos de una sola lectura y un episodio de 906 horas con cinco lecturas.

## Granularidad de Silver

Silver conserva **una fila por lectura**, sin agregar. Razones:

- La cadencia no es constante: mediana de 366 s en julio de 2025, 110 s en agosto y septiembre y 12,6 s desde octubre. Una malla fija (por ejemplo, un minuto) habría que rellenarla por interpolación en los meses ralos, es decir, inventar datos.
- La energía se calcula integrando entre lecturas reales. Con un promedio por minuto se pierden picos de potencia y se cruzan huecos.
- Las reglas de calidad y de eventos necesitan la resolución original (`read_ts` con fracción de segundo).

La agregación se hace en Gold, a hora local (`fact_energy_hour`, 14.077 filas) y a día para calidad (`fact_quality_day`, 711 filas), que es lo que consumen los tableros.

## Conciliación

| Comprobación | Resultado |
|---|---|
| Bronze = filas de `analog_measurements` | 2.989.759 = 2.989.759 |
| Silver válidas + rechazadas = Bronze | 2.934.098 + 55.661 = 2.989.759 |
| Gold lecturas = Silver válidas | 2.934.098 = 2.934.098 |
| Suma de lecturas de `fact_energy_hour` = Silver válidas | 2.934.098 |
| Suma de `rows_received` de `fact_quality_day` = Bronze | 2.989.759 |
| Contexto: Bronze, Silver y eventos de contexto | 25.178 en los tres |
| Energía solar: lecturas frente a horas | 2.346,6098 kWh en ambos |
| Energía importada de la red: lecturas frente a horas | 4.734,4397 kWh en ambos |
| CO2: lecturas frente a horas | 516,2541 kg en ambos |
| Eventos derivados | 116 (19 red ausente, 28 batería baja, 69 huecos) |

Gold lecturas no tiene más filas que Silver válidas, pero la última lectura de cada tramo continuo no tiene intervalo siguiente y su energía queda en NULL: es esperado y por eso se concilia contando filas, no kWh. Los totales de energía también se verificaron recalculándolos de forma independiente con pandas desde el SQLite, y coinciden.

## Idempotencia

- Bronze: clave `(read_ts, inverter)` con upsert (`ON CONFLICT DO UPDATE`); el contexto usa `context.id`.
- Silver y Gold se reconstruyen en cada corrida (se borran las filas no simuladas y se vuelven a llenar; las del simulador se conservan), así que repetir no acumula duplicados ni deja datos de una versión anterior de las reglas.
- `--from-bronze` omite la importación, comprueba que Bronze coincide con la fuente y reconstruye lo demás.
- Los eventos tienen un `event_id` determinista (`context:<id>`, `derived:<tipo>:<inversor>:<epoch>`), por lo que no se duplican.

Evidencia de repetición: ver [DEMO_Y_EVIDENCIAS.md](DEMO_Y_EVIDENCIAS.md).

## Carga de demostración

`.\run.ps1 up -Demo` (o `k8s-up -Demo`) carga solo del 1 al 14 de marzo de 2026, un tramo continuo a 12,6 s, para levantar todo en pocos minutos durante la sustentación. Los totales cambian, pero las reglas, el modelo y los tableros son los mismos.
