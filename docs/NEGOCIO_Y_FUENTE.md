# Negocio y diagnóstico de la fuente

## Enfoque elegido

SolarBI: emisiones de CO2 evitadas por la generación solar con el factor del SIN. Se estima cuánta energía produce el canal fotovoltaico de la microrred, cuánta energía se sigue comprando a la red y qué cantidad de CO2 se habría emitido si la energía solar hubiera salido de la red colombiana.

## Usuarios y preguntas

| Usuario | Decisión que apoya | Preguntas que debe poder contestar |
|---|---|---|
| Dirección de infraestructura | Dimensionar y priorizar inversión en generación o baterías | ¿Cuántos kWh solares se producen por día, mes e inversor? ¿Cuánto se compra a la red y en qué horas? ¿Cuánto es ese ahorro en pesos con una tarifa dada? ¿Qué pasaría con +10 o +20 % de capacidad? |
| Operación y mantenimiento | Reaccionar a fallas y cuidar la batería | ¿Qué estado de carga tiene cada batería ahora? ¿Se cortó la red? ¿Dejó de llegar información? ¿Cuántas horas estuvo cada batería por debajo del 20 %? |
| Analista de BI y sostenibilidad | Reportar CO2 con método defendible | ¿Qué factor se usó, de qué año y fuente? ¿Qué porcentaje de las lecturas es válido? ¿Qué parte del periodo no tiene datos y por qué? |
| Administrador de datos y TI | Mantener la plataforma | ¿Se puede repetir la carga sin duplicar? ¿Se recupera la base si se cae el pod? ¿Las claves están fuera del repositorio? |

## Fuente

OpenCEM v1.0.0 (Zenodo, CC BY 4.0): microrred con paneles solares y baterías en Longgang, Shenzhen (China). La cita exacta está en el [README](../README.md). El archivo se verifica contra el manifiesto SHA-256 oficial antes de cargarlo (`8e38f982...4b27517` para el `.sqlite.zst`) y se abre en solo lectura.

La tabla se inventarió completa (`reports/etl-report.json` guarda columnas, tipos, filas y rango de cada tabla):

| Tabla de origen | Filas | Periodo UTC | Uso |
|---|---:|---|---|
| `analog_measurements` | 2.989.759 | 2025-07-12 a 2026-07-13 | Núcleo del análisis |
| `context` | 25.178 | 2024-07-27 a 2026-07-14 | Eventos y contexto (JSON) |
| `fault_history` | 2.129.517 | igual que mediciones | Inventariada, no se carga (códigos sin diccionario verificable) |
| `settings`, `specs`, `statistics`, `status`, `totals` | 1,3 a 2,3 millones c/u | igual | Inventariadas, no se cargan |

El archivo de soporte de Zenodo (`documentation/column_dictionary.csv`) es la referencia de unidades; ver [DICCIONARIO.md](DICCIONARIO.md).

## Diagnóstico del dato

Hora del sitio: UTC+8 (Asia/Shanghai, sin horario de verano). Las marcas de tiempo de la fuente están en UTC; todo agrupamiento por día u hora se hace en hora local del sitio, porque de lo contrario el perfil solar queda partido entre dos días.

| Aspecto | Hallazgo |
|---|---|
| Periodo | Inversor 1: 2025-07-12 a 2026-07-13 (hora local). Inversor 2: 2025-07-14 a 2026-06-19. |
| Cobertura | Disponibilidad del registro 82,5 % del tiempo (inversor 1: 89,1 %; inversor 2: 75,5 %). Los meses más pobres son agosto de 2025 (15,6 %), noviembre de 2025 (69,5 %), mayo (66,9 %) y junio de 2026 (54,0 %). |
| Cadencia | No es constante: ~366 s en julio de 2025, ~110 s en agosto y septiembre, 12,6 s de octubre de 2025 en adelante (mediana). Un solo valor de frecuencia esperada no describe el archivo. |
| Huecos | 69 huecos mayores a 900 s; el mayor, de 628 horas en agosto de 2025. |
| Lecturas vacías | 55.658 lecturas llegan sin ningún canal (55.657 del inversor 2, 1 del inversor 1). Son 1,9 % del total. |
| Lecturas válidas | 2.934.098 de 2.989.759 (98,1 %). |
| Red | Desde comienzos de mayo de 2026 el inversor 1 pasa semanas sin red (episodios de 524 y 581 horas) y el inversor 2 tiene cuatro episodios en mayo. Se detecta con el voltaje de línea (`linevolta`) por debajo de 100 V; puede ser operación en isla o un corte real, y los datos no permiten distinguirlo. |
| Contexto | 25.178 registros con 55 tipos (`cpu_compilation_workload`, `gpu_cnn_training_workload`, `solar_daylight`, `data_gap`, entre otros). Texto libre sensible; ver [ETICA_Y_SEGURIDAD.md](ETICA_Y_SEGURIDAD.md). |

## Qué mide y qué no

- `pv1power` es **un** canal de entrada FV (PV1) de cada inversor. El máximo observado es 3.902 W (inversor 1) y 3.933 W (inversor 2). No es la generación total de la instalación.
- `outw_a` es la potencia de salida de la fase A del inversor. No es la carga total de la instalación.
- La red importada es `linepowerw_a`. El canal `gridpowerw_a` existe en el archivo pero su significado no está confirmado en el diccionario y no se usa.
- La potencia de la batería se calcula como `battvolt x battcurr` (el diccionario define `battcurr` positivo en descarga).
- No hay irradiancia, temperatura de módulo, potencia nominal ni energía exportada. La placa del sistema no está en los datos, por eso `dim_inverter` deja la capacidad nominal en NULL y guarda solo los máximos observados.

Con estas limitaciones el cálculo de CO2 es una **estimación potencial** basada en PV1: si parte de esa energía se envió a la batería o se recortó, no desplazó necesariamente consumo de la red. Por eso los tableros dicen "estimado" y el análisis de rendimiento (PR) se discute en [INDICADORES.md](INDICADORES.md) en lugar de calcularse.

## Cálculo de SolarBI

```
CO2 evitado (kg) = energía solar PV1 integrada (kWh) x factor de emisión del SIN (kg CO2e/kWh)
```

Factor por defecto: 0,220 kg CO2e/kWh (Resolución UPME 085 del 23 de febrero de 2026, inventarios del SIN 2024: 0,220 tCO2eq/MWh). La misma resolución publica 0,660 tCO2eq/MWh para proyectos MDL eólicos y solares, que corresponde a otro propósito (acreditación de proyectos) y no se usa. El factor y la tarifa son parámetros: se cambian en `.env` o en `k8s/configmap.yml` y en Power BI con el parámetro what-if.

Resultado con el periodo completo: 2.346,6 kWh solares PV1, 4.734,4 kWh importados de la red y 516,3 kg de CO2 estimados como evitados (0,52 t).
