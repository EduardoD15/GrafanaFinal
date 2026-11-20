# Ética, privacidad y seguridad

## Datos personales y Ley 1581 de 2012

Las lecturas del inversor son datos técnicos. La tabla `context` es distinta: cada registro describe la actividad del lugar donde está la microrred y, además del tipo de evento, trae campos como `command`, `directory`, `task_files`, `project` y `textual_description` (comandos ejecutados, rutas de carpetas, nombres de archivos y una descripción en texto libre). En una muestra de 3.000 registros, esos campos aparecen en más del 98 %. Aunque el dataset es público y no trae nombres, esa información puede revelar hábitos de trabajo de una persona identificable, y la Ley 1581 de 2012 protege los datos personales aun cuando la identificación sea indirecta.

Decisiones del proyecto:

| Principio de la ley | Medida |
|---|---|
| Finalidad | El contexto se usa solo para relacionar eventos con la carga eléctrica (por ejemplo, entrenamientos de GPU) |
| Minimización | El ETL guarda únicamente una lista permitida de claves: `inverter`, `source`, `device`, `context_type`, `task_type`, `llm_estimated_task_effort`, `cpu_cores_active`, `day_type`, `gap_seconds`, `threshold_seconds`. Comandos, rutas, archivos, proyecto y texto libre se descartan antes de escribir en Bronze; no existen en la base |
| Acceso restringido | Power BI y Grafana usan el rol `bi_reader`, que solo lee Gold; el JSON minimizado es lo único que ven |
| Seguridad | Claves fuera del repositorio (ver abajo); capturas del informe sin credenciales |
| Retención | La base se puede destruir y reconstruir con un comando (`.\run.ps1 clean` y volver a levantar); no se hacen copias aparte |

La regla de la lista permitida está en `app/core.py` (`ALLOWED_CONTEXT_KEYS` y `sanitize_context`) y tiene una prueba unitaria que comprueba que `command`, `directory` y `textual_description` no sobreviven.

Pendiente de confirmar con el docente: quién actúa como responsable del tratamiento en un ejercicio académico y si la lista permitida es suficiente. Hasta entonces el proyecto trata el contexto como dato potencialmente personal y no lo expone en capturas ni exportaciones.

## Uso responsable del resultado

- El CO2 evitado es una estimación potencial sobre un solo canal (PV1) con un factor oficial citado; no es una reducción medida ni certificada.
- La señal de Grafana es sintética y se marca como tal en los paneles y en la base (`is_simulated`); la alerta aleatoria de demostración lo dice en su descripción. Nada de eso se presenta como telemetría real.
- Los indicadores que los datos no permiten (PR, eficiencia de la batería) se omiten con su justificación en [INDICADORES.md](INDICADORES.md) en lugar de forzar un número.

## Credenciales y secretos

- `.env` se genera con claves aleatorias (`run.ps1`) y está en `.gitignore`; el repositorio solo contiene `.env.example` con marcadores `CAMBIAR_...`.
- En Kubernetes las claves viven en un Secret creado desde `.env`; `k8s/secret.example.yml` documenta el formato sin valores reales. Las imágenes no contienen claves.
- Tres identidades con permisos distintos: el usuario dueño de la base (solo ETL y simulador), el rol lector `solarbi_reader` (solo SELECT sobre Gold, para Power BI y Grafana) y el administrador de Grafana. El proyecto de Power BI no guarda credenciales.
- Un Secret de Kubernetes sin cifrado externo no es una bóveda: cualquiera con acceso al cluster puede leerlo. Para uso compartido haría falta cifrado en reposo, rotación de claves y TLS en los puertos publicados.
- Los puertos 5432, 3000, 30030 y 30432 son solo para el equipo local; no se exponen a Internet.
- El ETL abre la fuente en solo lectura y verifica su SHA-256 antes de procesarla.

## Licencia y cita

Datos OpenCEM bajo CC BY 4.0; el trabajo derivado cita la fuente (ver [README](../README.md)) y no cambia la licencia de los datos. La resolución de la UPME y la tarifa de EPM se citan con su fecha en [INDICADORES.md](INDICADORES.md).
