# Indicadores SolarBI

| Indicador | Definicion | Unidad | Grano | Umbral/meta inicial | Fuente / usuario |
|---|---|---|---|---|---|
| Potencia solar PV1 | `pv1power` | W / kW | lectura, inversor | 0 a 12,48 kWp instalados como referencia | Medicion / infraestructura |
| Energia solar | Integral trapezoidal PV1 entre lecturas consecutivas con intervalo <= 300 s | kWh | intervalo, dia, mes, inversor | No se integra un hueco mayor a 5 min | `pv1power` / direccion |
| Salida medida fase A | `outw_a` | W / kW | lectura, inversor | No interpretar como carga trifasica total | `outw_a` / direccion |
| Energia entregada a salida fase A | Integral trapezoidal `outw_a` | kWh | intervalo, dia, mes, inversor | Huecos > 5 min excluidos | `outw_a` / direccion |
| Importacion de red | `max(gridpowerw_a, 0)`; se separan lecturas negativas/exportacion | W / kW y kWh | lectura e intervalo | No negativa en el KPI de importacion | `gridpowerw_a` / direccion |
| Disponibilidad del registro | Lecturas validas / lecturas esperadas; esperado usa el intervalo mediano de 13 s publicado para la fuente | % | inversor y periodo | >= 95 % (objetivo propuesto) | Silver / mantenimiento |
| Validez de datos | Filas validas segun reglas / filas recibidas en Bronze | % | inversor y periodo | >= 99 % (objetivo propuesto) | Silver / datos |
| Estado de carga | `battsoc` | % | lectura, inversor | Alerta < 20 %; critica < 10 % | Bateria / mantenimiento |
| Intensidad de carbono evitada | Energia solar estimada x factor SIN configurado | kg CO2e | intervalo, dia, mes, inversor | Factor se verifica y cita por año antes de presentar | Calculo / sostenibilidad |
| Ahorro bruto estimado | Energia solar x tarifa elegida | COP | dia, mes, inversor | Tarifa y periodo se deben citar | Calculo / direccion |
| Autonomia relativa de bateria | SoC actual frente al SoC medio historico del mismo inversor y periodo | puntos porcentuales | hora, dia, inversor | Indicador derivado; no estima horas de autonomia | SoC / mantenimiento |
| Dependencia de red | kWh importados / (kWh importados + kWh solar) | % | dia, mes, inversor | Menor es mejor, sujeto a datos completos | Calculo / direccion |

La disponibilidad usa la cadencia mediana de 13 s y solo el intervalo historico observado de cada inversor; cuenta como cero los dias sin lecturas dentro de ese intervalo y excluye la simulacion. Es una estimacion de cobertura, no un SLA del equipo.

`0.112 kg CO2e/kWh` y `1000 COP/kWh` son valores iniciales de demostracion parametrizables, no una afirmacion de vigencia oficial. Sustituyalos por el factor SIN publicado por UPME para el año de analisis y por una tarifa de factura/fuente fechada. Guarde la fuente y el año en la sustentacion.

La fuente no expone una medicion comprobable de exportacion/curtailment, irradiancia en plano del panel ni energia DC calibrada para todos los canales. No se debe llamar PR (performance ratio) al rendimiento calculado. El CO2 evitado se comunica como estimacion potencial basada en PV1, no como reduccion medida neta de emisiones.
