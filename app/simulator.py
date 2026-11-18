from __future__ import annotations

import logging
import math
import os
import random
import time
from datetime import datetime, timedelta, timezone
from typing import Any

import psycopg
from psycopg.types.json import Jsonb


logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)sZ %(levelname)s %(message)s",
)
LOGGER = logging.getLogger("solarbi.simulator")
SITE_TZ = timezone(timedelta(hours=8))


def required_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Falta la variable obligatoria {name}.")
    return value


def latest_source_values(connection: psycopg.Connection[Any]) -> dict[int, float]:
    rows = connection.execute(
        """
        SELECT DISTINCT ON (inverter_id) inverter_id, battery_soc_pct
        FROM gold.fact_energy_reading
        WHERE NOT is_simulated AND battery_soc_pct IS NOT NULL
        ORDER BY inverter_id, measured_at DESC
        """
    ).fetchall()
    values = {int(inverter): float(soc) for inverter, soc in rows}
    if set(values) != {1, 2}:
        raise RuntimeError(
            "El simulador necesita lecturas historicas validas para ambos inversores. "
            "Ejecute primero el ETL y verifique su reporte."
        )
    return values


def simulated_values(timestamp: float, inverter: int, rng: random.Random) -> tuple[float, float, float, float]:
    local_time = datetime.fromtimestamp(timestamp, timezone.utc).astimezone(SITE_TZ)
    daylight = max(0.0, math.sin((local_time.hour + local_time.minute / 60 - 6) * math.pi / 12))
    solar_w = max(0.0, 6200 * daylight + rng.uniform(-120, 120))
    load_w = max(500.0, 1600 + 300 * math.sin(timestamp / 31 + inverter) + rng.uniform(-100, 100))
    grid_w = max(0.0, load_w - solar_w * 0.65)
    soc = min(100.0, max(5.0, 50 + 42 * math.sin(timestamp / 150 + inverter) + rng.uniform(-0.3, 0.3)))
    return solar_w, load_w, grid_w, soc


def write_tick(
    connection: psycopg.Connection[Any],
    timestamp: float,
    inverter: int,
    values: tuple[float, float, float, float],
    previous: dict[int, tuple[float, tuple[float, float, float, float]]],
    emission_factor: float,
) -> None:
    solar_w, load_w, grid_w, soc = values
    measured_at = datetime.fromtimestamp(timestamp, timezone.utc)
    date_key = int(measured_at.strftime("%Y%m%d"))
    time_key = measured_at.hour * 60 + measured_at.minute
    prior = previous.get(inverter)
    elapsed = timestamp - prior[0] if prior else None

    def interval_kwh(index: int, power: float) -> float | None:
        if prior is None or elapsed is None or elapsed <= 0 or elapsed > 10:
            return None
        return (prior[1][index] + power) * 0.5 * elapsed / 3_600_000

    solar_kwh = interval_kwh(0, solar_w)
    load_kwh = interval_kwh(1, load_w)
    grid_kwh = interval_kwh(2, grid_w)
    payload = {
        "read_ts": timestamp,
        "inverter": inverter,
        "pv1power": round(solar_w, 2),
        "outw_a": round(load_w, 2),
        "gridpowerw_a": round(grid_w, 2),
        "battsoc": round(soc, 2),
        "simulated": True,
    }

    with connection.transaction():
        connection.execute(
            """
            INSERT INTO bronze.measurements_raw (read_ts, inverter, payload, is_simulated)
            VALUES (%s, %s, %s, true)
            ON CONFLICT (read_ts, inverter) DO NOTHING
            """,
            (timestamp, inverter, Jsonb(payload)),
        )
        connection.execute(
            """
            INSERT INTO silver.measurements (
                read_ts, inverter, measured_at, measurement_date, pv_power_w, load_power_w,
                grid_power_w, battery_soc_pct, is_valid, invalid_reason, is_simulated
            ) VALUES (%s, %s, to_timestamp(%s), to_timestamp(%s)::date,
                      %s, %s, %s, %s, true, NULL, true)
            ON CONFLICT (read_ts, inverter) DO NOTHING
            """,
            (timestamp, inverter, timestamp, timestamp, solar_w, load_w, grid_w, soc),
        )
        connection.execute(
            """
            INSERT INTO gold.dim_date (date_key, calendar_date, year, quarter, month,
                                       day, day_of_week)
            VALUES (%s, %s::date, %s, %s, %s, %s, %s)
            ON CONFLICT (date_key) DO NOTHING
            """,
            (
                date_key,
                measured_at.date(),
                measured_at.year,
                (measured_at.month - 1) // 3 + 1,
                measured_at.month,
                measured_at.day,
                measured_at.isoweekday(),
            ),
        )
        connection.execute(
            """
            INSERT INTO gold.fact_energy_reading (
                measured_at, date_key, time_key, inverter_id, pv_power_w,
                load_power_w, grid_power_w, battery_soc_pct, solar_kwh_interval,
                load_kwh_interval, grid_kwh_interval, co2_avoided_kg_interval,
                emission_factor_kg_kwh, is_simulated
            ) VALUES (
                to_timestamp(%s), %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, true
            )
            ON CONFLICT (measured_at, inverter_id) DO UPDATE SET
                pv_power_w = EXCLUDED.pv_power_w,
                load_power_w = EXCLUDED.load_power_w,
                grid_power_w = EXCLUDED.grid_power_w,
                battery_soc_pct = EXCLUDED.battery_soc_pct,
                solar_kwh_interval = EXCLUDED.solar_kwh_interval,
                load_kwh_interval = EXCLUDED.load_kwh_interval,
                grid_kwh_interval = EXCLUDED.grid_kwh_interval,
                co2_avoided_kg_interval = EXCLUDED.co2_avoided_kg_interval,
                emission_factor_kg_kwh = EXCLUDED.emission_factor_kg_kwh
            """,
            (
                timestamp,
                date_key,
                time_key,
                inverter,
                solar_w,
                load_w,
                grid_w,
                soc,
                solar_kwh,
                load_kwh,
                grid_kwh,
                solar_kwh * emission_factor if solar_kwh is not None else None,
                emission_factor,
            ),
        )
        if soc < 20:
            connection.execute(
                """
                INSERT INTO gold.fact_event (
                    event_id, occurred_at, date_key, time_key, inverter_id,
                    event_type, severity, context_source, metadata
                ) VALUES (%s, to_timestamp(%s), %s, %s, %s, 'low_battery_soc',
                          %s, 'simulator', %s)
                ON CONFLICT (event_id) DO UPDATE SET
                    severity = EXCLUDED.severity, metadata = EXCLUDED.metadata
                """,
                (
                    f"sim-low-soc:{timestamp}:{inverter}",
                    timestamp,
                    date_key,
                    time_key,
                    inverter,
                    "critical" if soc < 10 else "warning",
                    Jsonb({"battery_soc_pct": round(soc, 2), "simulated": True}),
                ),
            )
    previous[inverter] = (timestamp, values)


def write_demo_alert(
    connection: psycopg.Connection[Any],
    timestamp: float,
    inverter: int,
    scenario: str,
) -> None:
    occurred_at = datetime.fromtimestamp(timestamp, timezone.utc)
    date_key = int(occurred_at.strftime("%Y%m%d"))
    time_key = occurred_at.hour * 60 + occurred_at.minute
    metadata = {
        "simulated": True,
        "demo_only": True,
        "scenario": scenario,
        "message": (
            "Alerta aleatoria de demostracion; no representa una falla real."
        ),
    }
    with connection.transaction():
        connection.execute(
            """
            INSERT INTO gold.fact_event (
                event_id, occurred_at, date_key, time_key, inverter_id,
                event_type, severity, context_source, metadata
            ) VALUES (%s, to_timestamp(%s), %s, %s, %s, 'demo_random_alert',
                      'warning', 'simulator_demo', %s)
            ON CONFLICT (event_id) DO NOTHING
            """,
            (
                f"sim-demo-alert:{timestamp:.6f}:{inverter}",
                timestamp,
                date_key,
                time_key,
                inverter,
                Jsonb(metadata),
            ),
        )


def main() -> None:
    interval_ms = int(os.getenv("SIMULATOR_INTERVAL_MS", "1000"))
    if interval_ms < 100:
        raise ValueError("SIMULATOR_INTERVAL_MS debe ser al menos 100 ms.")
    emission_factor = float(required_env("CO2_FACTOR_KG_PER_KWH"))
    rng = random.Random(2026)
    demo_rng = random.SystemRandom()
    previous: dict[int, tuple[float, tuple[float, float, float, float]]] = {}
    with psycopg.connect(required_env("DATABASE_URL"), autocommit=True) as connection:
        connection.execute("SET TIME ZONE 'UTC'")
        latest_source_values(connection)
        next_demo_alert_at = time.monotonic() + demo_rng.uniform(20.0, 45.0)
        LOGGER.info(
            "Simulacion activa: un registro por inversor cada %s ms; alertas aleatorias solo de demostracion",
            interval_ms,
        )
        while True:
            started = time.monotonic()
            timestamp = time.time()
            for inverter in (1, 2):
                values = simulated_values(timestamp, inverter, rng)
                write_tick(
                    connection,
                    timestamp,
                    inverter,
                    values,
                    previous,
                    emission_factor,
                )
            now = time.monotonic()
            if now >= next_demo_alert_at:
                inverter = demo_rng.choice((1, 2))
                scenario = demo_rng.choice(
                    ("solar_variation_demo", "load_notice_demo", "battery_notice_demo")
                )
                write_demo_alert(connection, timestamp, inverter, scenario)
                LOGGER.warning(
                    "ALERTA DE DEMOSTRACION (sintetica): inversor %s, escenario %s",
                    inverter,
                    scenario,
                )
                next_demo_alert_at = now + demo_rng.uniform(60.0, 120.0)
            remaining = interval_ms / 1000 - (time.monotonic() - started)
            if remaining > 0:
                time.sleep(remaining)


if __name__ == "__main__":
    main()
