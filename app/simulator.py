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

from app.core import SITE_UTC_OFFSET_HOURS


logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)sZ %(levelname)s %(message)s",
)
LOGGER = logging.getLogger("solarbi.simulator")

# Hora a la que sale el sol en la simulacion. Por defecto la del sitio (Shenzhen); para que el
# amanecer coincida con la hora del salon de clase se puede poner -5 (Colombia).
DAYLIGHT_OFFSET_HOURS = float(os.getenv("SIMULATOR_DAYLIGHT_OFFSET_HOURS", str(SITE_UTC_OFFSET_HOURS)))


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


Values = tuple[float, float, float, float, float]  # solar, carga, red, SoC, bateria (W, + descarga)


def simulated_values(timestamp: float, inverter: int, rng: random.Random) -> Values:
    local_time = datetime.fromtimestamp(timestamp, timezone.utc) + timedelta(hours=DAYLIGHT_OFFSET_HOURS)
    daylight = max(0.0, math.sin((local_time.hour + local_time.minute / 60 - 6) * math.pi / 12))
    solar_w = max(0.0, 3200 * daylight * (0.9 + 0.1 * math.sin(timestamp / 97)) + rng.uniform(-40, 40))
    load_w = max(120.0, 450 + 220 * math.sin(timestamp / 310 + inverter) + rng.uniform(-40, 40))
    soc = min(100.0, max(5.0, 50 + 45 * math.sin(timestamp / 150 + inverter) + rng.uniform(-0.3, 0.3)))
    battery_w = -600 * math.cos(timestamp / 150 + inverter)
    line_w = max(0.0, load_w - solar_w * 0.9 - max(battery_w, 0.0))
    return solar_w, load_w, line_w, soc, battery_w


def write_tick(
    connection: psycopg.Connection[Any],
    timestamp: float,
    inverter: int,
    values: Values,
    previous: dict[int, tuple[float, Values]],
    emission_factor: float,
) -> None:
    solar_w, load_w, line_w, soc, battery_w = values
    measured_at = datetime.fromtimestamp(timestamp, timezone.utc)
    local = measured_at + timedelta(hours=SITE_UTC_OFFSET_HOURS)
    date_key = int(local.strftime("%Y%m%d"))
    time_key = local.hour * 60 + local.minute
    prior = previous.get(inverter)
    elapsed = timestamp - prior[0] if prior else None
    continuous = prior is not None and elapsed is not None and 0 < elapsed <= 10

    def interval_kwh(index: int, power: float, clip: bool = False, sign: float = 1.0) -> float | None:
        if not continuous:
            return None
        before, now = prior[1][index] * sign, power * sign
        if clip:
            before, now = max(before, 0.0), max(now, 0.0)
        return (before + now) * 0.5 * elapsed / 3_600_000

    solar_kwh = interval_kwh(0, solar_w)
    load_kwh = interval_kwh(1, load_w)
    grid_kwh = interval_kwh(2, line_w, clip=True)
    discharge_kwh = interval_kwh(4, battery_w, clip=True)
    charge_kwh = interval_kwh(4, battery_w, clip=True, sign=-1.0)
    payload = {
        "read_ts": timestamp,
        "inverter": inverter,
        "pv1power": round(solar_w, 2),
        "outw_a": round(load_w, 2),
        "linepowerw_a": round(line_w, 2),
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
                line_power_w, battery_soc_pct, battery_power_w, is_valid, invalid_reason, is_simulated
            ) VALUES (%s, %s, to_timestamp(%s), %s, %s, %s, %s, %s, %s, true, NULL, true)
            ON CONFLICT (read_ts, inverter) DO NOTHING
            """,
            (timestamp, inverter, timestamp, local.date(), solar_w, load_w, line_w, soc, battery_w),
        )
        connection.execute(
            """
            INSERT INTO gold.dim_date (date_key, calendar_date, year, quarter, month,
                                       day, day_of_week, year_month, is_weekend)
            VALUES (%s, %s::date, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (date_key) DO NOTHING
            """,
            (
                date_key,
                local.date(),
                local.year,
                (local.month - 1) // 3 + 1,
                local.month,
                local.day,
                local.isoweekday(),
                local.strftime("%Y-%m"),
                local.isoweekday() >= 6,
            ),
        )
        connection.execute(
            """
            INSERT INTO gold.fact_energy_reading (
                measured_at, date_key, time_key, inverter_id, pv_power_w, load_power_w,
                line_power_w, battery_power_w, battery_soc_pct, grid_present, interval_seconds,
                solar_kwh_interval, load_kwh_interval, grid_import_kwh_interval,
                battery_discharge_kwh_interval, battery_charge_kwh_interval,
                co2_avoided_kg_interval, emission_factor_kg_kwh, is_simulated
            ) VALUES (
                to_timestamp(%s), %s, %s, %s, %s, %s, %s, %s, %s, true, %s,
                %s, %s, %s, %s, %s, %s, %s, true
            )
            ON CONFLICT (measured_at, inverter_id) DO NOTHING
            """,
            (
                timestamp,
                date_key,
                time_key,
                inverter,
                solar_w,
                load_w,
                line_w,
                battery_w,
                soc,
                elapsed if continuous else None,
                solar_kwh,
                load_kwh,
                grid_kwh,
                discharge_kwh,
                charge_kwh,
                solar_kwh * emission_factor if solar_kwh is not None else None,
                emission_factor,
            ),
        )
        if soc < 20 and (prior is None or prior[1][3] >= 20):
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
    local = datetime.fromtimestamp(timestamp, timezone.utc) + timedelta(hours=SITE_UTC_OFFSET_HOURS)
    date_key = int(local.strftime("%Y%m%d"))
    time_key = local.hour * 60 + local.minute
    metadata = {
        "simulated": True,
        "demo_only": True,
        "scenario": scenario,
        "message": "Alerta aleatoria de demostracion; no representa una falla real.",
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
    previous: dict[int, tuple[float, Values]] = {}
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
                write_tick(connection, timestamp, inverter, values, previous, emission_factor)
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
