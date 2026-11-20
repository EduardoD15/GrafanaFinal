# Indicadores SolarBI

Todas las energías se integran con la regla del trapecio entre lecturas consecutivas del mismo inversor y no cruzan huecos mayores a 900 s (`MAX_INTERVAL_SECONDS`). Los días y las horas se cuentan en hora local del sitio (UTC+8).

## Catálogo

| Indicador | Definición | Unidad | Grano | Meta propuesta | Fuente | Usuario |
|---|---|---|---|---|---|---|
| Energía solar | Integral de `pv1power` (canal PV1) | kWh | lectura, hora, día, mes, inversor | sin meta; se compara contra el mes anterior | `gold.fact_energy_hour.solar_kwh` | Infraestructura |
| Energía de salida | Integral de `outw_a` (fase A del inversor) | kWh | igual | sin meta | `load_kwh` | Infraestructura |
| Energía importada de la red | Integral de `max(linepowerw_a, 0)` | kWh | igual | menor es mejor | `grid_import_kwh` | Infraestructura |
| Descarga y carga de batería | Integral de la parte positiva (descarga) o negativa (carga) de `battvolt x battcurr` | kWh | igual | carga y descarga deben ser del mismo orden | `battery_discharge_kwh`, `battery_charge_kwh` | Operación |
| Disponibilidad del registro | Segundos cubiertos por lecturas continuas / segundos esperados entre la primera y la última lectura del inversor | % | inversor, día, mes, periodo | >= 95 % | `fact_quality_day` | Operación |
| Datos válidos | Lecturas válidas / lecturas recibidas | % | inversor, día, mes, periodo | >= 99 % | `fact_quality_day` | Analista |
| CO2 evitado estimado | Energía solar x factor del SIN | kg CO2e | hora, día, mes, inversor | sin meta | `co2_avoided_kg` | Sostenibilidad |
| Ahorro solar | Energía solar x tarifa | COP | día, mes, inversor | sin meta | medida `Ahorro Solar (COP)` | Dirección |
| Costo de la red | Energía importada x tarifa | COP | día, mes, inversor | menor es mejor | medida `Costo Red (COP)` | Dirección |

### Indicadores híbridos (solar + red + batería)

| Indicador | Definición | Valor del periodo | Lectura |
|---|---|---:|---|
| Dependencia de la red | red / (red + solar) | 66,9 % (inversor 1: 60,3 %; inversor 2: 70,2 %) | De cada 3 kWh que llegan al sistema, 2 vienen de la red |
| Autosuficiencia | 1 - red / salida del inversor (mínimo 0) | 21,0 % | Qué parte de la salida se cubre sin comprar a la red |
| Horas con SoC bajo | horas con SoC mínimo < 20 % | 220 | Sirve para dimensionar la reserva |
| Horas sin red | 1 - fracción de lecturas con voltaje de línea >= 100 V | 11,7 % | Operación en isla o cortes |
| Episodios de batería baja y de red ausente | rachas de al menos 2 lecturas consecutivas bajo el umbral, separadas por huecos > 900 s | 28 y 19 | Eventos de operación, no errores de calidad |

El perfil horario muestra la complementariedad: de 9 a 15 h la dependencia de la red cae a 7 a 13 %, y de 19 a 5 h es 100 % porque no hay sol. La dependencia mensual está entre 65 y 77 % de julio de 2025 a abril de 2026; en mayo cae a 19,7 % y en junio y julio a 0 % porque el inversor 1 queda sin red (se ve en la autosuficiencia, que sube a 100 %). Esa mejora es un efecto de la ausencia de red, no de mejor desempeño.

### Indicador en pesos

`Ahorro solar (COP) = energía solar (kWh) x tarifa (COP/kWh)`. Valor por defecto del parámetro: 790 COP/kWh, el paso de 10 en 10 más cercano a la tarifa de referencia de EPM para estrato 3 de diciembre de 2025 (790,49 COP/kWh). Con ese valor el ahorro bruto del periodo es 1.853.822 COP y el costo de las compras a la red 3.740.207 COP. El parámetro permite probar otras tarifas entre 300 y 1.500 COP/kWh. Es un ahorro bruto: no descuenta cargos fijos ni considera que parte de la energía solar fue a la batería.

### Factor de emisión

0,220 kg CO2e/kWh: Resolución UPME 085 del 23 de febrero de 2026, factor del SIN con inventarios 2024 (0,220 tCO2eq/MWh). Con ese factor: 516,3 kg (0,52 t). El factor es un parámetro (`CO2_FACTOR_KG_PER_KWH`, parámetro what-if de 50 a 700 g/kWh); cada vez que se reporte debe citarse con su año. No se usa 0,660 tCO2eq/MWh porque es el valor para proyectos MDL eólicos y solares, no el de la red.

## Análisis crítico del PR (performance ratio)

El PR de una planta fotovoltaica es `energía AC entregada / (irradiación en el plano de los módulos / 1 kW/m2 x potencia pico instalada)`. Con este dataset no se calcula, y tampoco debería calcularse un sustituto:

1. **Falta el denominador.** No hay irradiancia en el plano de los módulos ni temperatura de módulo, y la placa del sistema (kWp) no figura en los datos. Los registros de contexto traen `solar_daylight` y crepúsculos, pero son marcas de tiempo, no irradiancia.
2. **El numerador no es la planta.** `pv1power` es el canal PV1 de cada inversor (DC, antes de la batería) y `outw_a` es una sola fase. Dividir uno por otro mezcla DC con AC y un canal con una fase.
3. **La energía solar se reparte.** Parte de PV1 carga la batería y parte se pierde o se recorta; no hay medición de exportación ni de recorte, así que no se puede separar lo producido de lo aprovechado.
4. **El máximo observado parece un tope.** Los dos inversores alcanzan 3.902 y 3.933 W en PV1, casi idénticos pese a tener comportamientos muy distintos, lo que sugiere un límite de entrada o del controlador más que la capacidad real de los módulos (hipótesis; no se puede confirmar con los datos).
5. **La cobertura cambia el resultado.** Con 82,5 % de disponibilidad y lecturas vacías concentradas en un inversor, cualquier razón mensual quedaría sesgada por la proporción de horas observadas.

En su lugar se publican la energía PV1 por día y mes, el promedio móvil de 7 días y la comparación con el mes anterior, que sí se pueden defender, siempre con la etiqueta "PV1".

## Indicador descartado: eficiencia de ida y vuelta de la batería

Descarga / carga del periodo da 1.602,5 / 1.360,0 = 117,8 %, físicamente imposible. Indica que la carga está subregistrada (intervalos de carga que caen en huecos de más de 900 s) o que el signo de `battcurr` cambia de semántica en algún tramo. Por eso el indicador se omite y la carga y la descarga se muestran por separado en el balance.

## Umbrales de las alertas de Grafana

| Regla | Condición | Justificación |
|---|---|---|
| Batería con estado de carga bajo | SoC mínimo de los últimos 2 min < 20 % durante 30 s | En los datos reales el percentil 5 del SoC es 71 % (inversor 1) y 25 % (inversor 2) y el 1 es 35 % y 13 %; solo 0,23 % y 3,1 % de las lecturas están bajo 20 %. El umbral separa los casos raros de la operación normal sin dispararse a diario. El simulador marca como críticos los eventos bajo 10 %. |
| Flujo sin lecturas recientes | menos de una lectura simulada en 90 s durante 30 s | El simulador escribe una lectura por segundo y por inversor; 90 s sin filas equivale a casi cien lecturas perdidas, mucho más que un retraso normal. Sin datos se trata como alerta. |
| Alerta aleatoria de demostración | evento `demo_random_alert` en los últimos 90 s | Solo sirve para mostrar el disparo en la sustentación; está marcada como sintética y no representa una falla. |
