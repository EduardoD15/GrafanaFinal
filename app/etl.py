from __future__ import annotations

import json
import argparse
import hashlib
import logging
import math
import os
import re
import sqlite3
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import psycopg
from psycopg import sql
from psycopg.types.json import Jsonb
import zstandard

from app.core import (
    MAX_SOURCE_EPOCH,
    MIN_SOURCE_EPOCH,
    SITE_UTC_OFFSET_HOURS,
    sanitize_context,
)


logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)sZ %(levelname)s %(message)s",
)
LOGGER = logging.getLogger("solarbi.etl")
BATCH_SIZE = int(os.getenv("ETL_BATCH_SIZE", "10000"))
SCHEMA_FILE = Path(__file__).resolve().parents[1] / "sql" / "schema.sql"
QUALITY_RULE_ORDER = (
    "invalid_timestamp",
    "unsupported_inverter",
    "empty_snapshot",
    "negative_pv1power",
    "negative_outw_a",
    "invalid_battery_soc",
    "zero_battery_voltage",
)
OFFSET = SITE_UTC_OFFSET_HOURS


def required_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Falta la variable obligatoria {name}.")
    return value


def date_window() -> tuple[float | None, float | None]:
    """Ventana opcional de la carga (dias del sitio, ambos incluidos), para la demostracion."""
    def epoch(text: str, extra_days: int = 0) -> float:
        day = date.fromisoformat(text) + timedelta(days=extra_days)
        return datetime(day.year, day.month, day.day, tzinfo=timezone.utc).timestamp() - OFFSET * 3600

    start = os.getenv("ETL_START_DATE", "").strip()
    end = os.getenv("ETL_END_DATE", "").strip()
    return (epoch(start) if start else None, epoch(end, 1) if end else None)


def ensure_sqlite(archive_path: Path, sqlite_path: Path) -> None:
    if sqlite_path.exists() and sqlite_path.stat().st_size:
        return
    if not archive_path.is_file():
        raise FileNotFoundError(
            f"No se encontro la fuente {archive_path}. Copie opencem-v1.0.0.sqlite.zst "
            "en data/bronze antes de iniciar el ETL."
        )
    sqlite_path.parent.mkdir(parents=True, exist_ok=True)
    partial_path = sqlite_path.with_suffix(sqlite_path.suffix + ".partial")
    LOGGER.info("Descomprimiendo Bronze a %s", sqlite_path)
    try:
        with archive_path.open("rb") as source, partial_path.open("wb") as target:
            zstandard.ZstdDecompressor().copy_stream(source, target)
        partial_path.replace(sqlite_path)
    except (OSError, zstandard.ZstdError):
        partial_path.unlink(missing_ok=True)
        raise


def sqlite_connection(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    result = connection.execute("PRAGMA quick_check").fetchone()
    if result is None or result[0] != "ok":
        connection.close()
        raise RuntimeError(f"PRAGMA quick_check fallo para la fuente: {result}")
    tables = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )
    }
    missing = {"analog_measurements", "context"} - tables
    if missing:
        connection.close()
        raise RuntimeError(f"Faltan tablas requeridas en SQLite: {sorted(missing)}")
    return connection


def source_inventory(source: sqlite3.Connection) -> list[dict[str, Any]]:
    tables = source.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' "
        "AND name NOT LIKE 'sqlite_%' ORDER BY name"
    ).fetchall()
    inventory = []
    for (table_name,) in tables:
        quoted_name = '"' + table_name.replace('"', '""') + '"'
        columns = source.execute(f"PRAGMA table_info({quoted_name})").fetchall()
        names = {column[1] for column in columns}
        timestamp_column = "read_ts" if "read_ts" in names else (
            "recorded" if "recorded" in names else None
        )
        row_count = source.execute(f"SELECT count(*) FROM {quoted_name}").fetchone()[0]
        period: dict[str, Any] | None = None
        if timestamp_column:
            quoted_timestamp = '"' + timestamp_column.replace('"', '""') + '"'
            bounds = source.execute(
                f"SELECT min({quoted_timestamp}), max({quoted_timestamp}) FROM {quoted_name}"
            ).fetchone()
            period = {
                "column": timestamp_column,
                "min_epoch": bounds[0],
                "max_epoch": bounds[1],
            }
            if bounds[0] is not None and bounds[1] is not None:
                period["min_utc"] = datetime.fromtimestamp(
                    float(bounds[0]), timezone.utc
                ).isoformat()
                period["max_utc"] = datetime.fromtimestamp(
                    float(bounds[1]), timezone.utc
                ).isoformat()
        inventory.append(
            {
                "table": table_name,
                "row_count": row_count,
                "columns": [
                    {"name": column[1], "type": column[2]} for column in columns
                ],
                "period": period,
            }
        )
    return inventory


def verify_archive_checksum(archive_path: Path) -> tuple[str, bool]:
    digest = hashlib.sha256()
    with archive_path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    actual = digest.hexdigest()
    manifest_path = archive_path.parent / "opencem-v1.0.0-SHA256SUMS.txt"
    if not manifest_path.exists():
        raise FileNotFoundError(
            f"No se encontro el manifiesto de checksums {manifest_path}."
        )
    expected = None
    for line in manifest_path.read_text(encoding="utf-8").splitlines():
        match = re.fullmatch(r"([0-9a-fA-F]{64})\s+\*?(.+)", line.strip())
        if match and Path(match.group(2)).name == archive_path.name:
            expected = match.group(1).lower()
            break
    if expected is None:
        raise RuntimeError(
            f"El manifiesto no contiene un hash para {archive_path.name}."
        )
    if actual != expected:
        raise RuntimeError(
            f"SHA-256 no coincide para {archive_path.name}: "
            f"esperado {expected}, obtenido {actual}."
        )
    return actual, True


def create_schema(connection: psycopg.Connection[Any]) -> None:
    connection.execute(SCHEMA_FILE.read_text(encoding="utf-8"))
    reader_user = os.getenv("BI_READER_USER")
    reader_password = os.getenv("BI_READER_PASSWORD")
    if reader_user and reader_password:
        exists = connection.execute(
            "SELECT 1 FROM pg_roles WHERE rolname = %s", (reader_user,)
        ).fetchone()
        action = "ALTER" if exists else "CREATE"
        connection.execute(
            sql.SQL(action + " ROLE {} LOGIN PASSWORD {}").format(
                sql.Identifier(reader_user), sql.Literal(reader_password)
            )
        )
        connection.execute(
            sql.SQL("GRANT bi_reader TO {}").format(sql.Identifier(reader_user))
        )
    connection.commit()


def source_window_clause(lo: float | None, hi: float | None) -> tuple[str, list[float]]:
    parts, params = [], []
    if lo is not None:
        parts.append("read_ts >= ?")
        params.append(lo)
    if hi is not None:
        parts.append("read_ts < ?")
        params.append(hi)
    return (" WHERE " + " AND ".join(parts)) if parts else "", params


def import_measurements(
    source: sqlite3.Connection,
    target: psycopg.Connection[Any],
    lo: float | None,
    hi: float | None,
) -> int:
    where, params = source_window_clause(lo, hi)
    source_cursor = source.execute(
        f"SELECT * FROM analog_measurements{where} ORDER BY read_ts, inverter", params
    )
    columns = [column[0] for column in source_cursor.description or ()]
    required = {"read_ts", "inverter", "pv1power", "outw_a", "gridpowerw_a", "linepowerw_a", "battsoc"}
    if not required.issubset(columns):
        raise RuntimeError(f"Faltan columnas requeridas: {sorted(required - set(columns))}")

    target.execute(
        """
        CREATE TEMP TABLE IF NOT EXISTS stage_measurements (
            read_ts DOUBLE PRECISION NOT NULL,
            inverter SMALLINT NOT NULL,
            payload JSONB NOT NULL
        ) ON COMMIT PRESERVE ROWS
        """
    )
    target.commit()

    total = 0
    batch: list[tuple[float, int, dict[str, Any]]] = []

    def flush() -> None:
        if not batch:
            return
        target.execute("TRUNCATE stage_measurements")
        with target.cursor().copy(
            "COPY stage_measurements (read_ts, inverter, payload) FROM STDIN"
        ) as copy:
            for timestamp, inverter, payload in batch:
                copy.write_row((timestamp, inverter, Jsonb(payload)))
        target.execute(
            """
            INSERT INTO bronze.measurements_raw (read_ts, inverter, payload, is_simulated)
            SELECT read_ts, inverter, payload, false FROM stage_measurements
            ON CONFLICT (read_ts, inverter) DO UPDATE
            SET payload = EXCLUDED.payload
            """
        )
        target.commit()
        batch.clear()

    for sqlite_row in source_cursor:
        row = dict(sqlite_row)
        total += 1
        batch.append((float(row["read_ts"]), int(row["inverter"]), row))
        if len(batch) >= BATCH_SIZE:
            flush()
            if total % (BATCH_SIZE * 10) == 0:
                LOGGER.info("Lecturas examinadas: %s", f"{total:,}")
    flush()
    return total


def import_context(
    source: sqlite3.Connection,
    target: psycopg.Connection[Any],
    lo: float | None,
    hi: float | None,
) -> tuple[int, int]:
    clauses, params = [], []
    if lo is not None:
        clauses.append("coalesce(start, recorded) >= ?")
        params.append(lo)
    if hi is not None:
        clauses.append("coalesce(start, recorded) < ?")
        params.append(hi)
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    rows = source.execute(
        f"SELECT id, recorded, start, end, value FROM context{where} ORDER BY id", params
    )
    target.execute(
        """
        CREATE TEMP TABLE IF NOT EXISTS stage_context (
            id BIGINT NOT NULL,
            recorded DOUBLE PRECISION,
            starts DOUBLE PRECISION,
            ends DOUBLE PRECISION,
            metadata JSONB NOT NULL
        ) ON COMMIT PRESERVE ROWS
        """
    )
    target.commit()
    total = 0
    accepted = 0
    batch: list[tuple[int, float | None, float | None, float | None, dict[str, Any]]] = []

    def flush() -> None:
        if not batch:
            return
        target.execute("TRUNCATE stage_context")
        with target.cursor().copy(
            "COPY stage_context (id, recorded, starts, ends, metadata) FROM STDIN"
        ) as copy:
            for record_id, recorded, starts, ends, metadata in batch:
                copy.write_row((record_id, recorded, starts, ends, Jsonb(metadata)))
        target.execute(
            """
            INSERT INTO bronze.context_raw (id, recorded, starts, ends, metadata)
            SELECT id, recorded, starts, ends, metadata FROM stage_context
            ON CONFLICT (id) DO UPDATE
            SET recorded = EXCLUDED.recorded, starts = EXCLUDED.starts,
                ends = EXCLUDED.ends, metadata = EXCLUDED.metadata
            """
        )
        target.commit()
        batch.clear()

    for record_id, recorded, starts, ends, value in rows:
        total += 1
        metadata = sanitize_context(value)
        is_valid = (
            recorded is not None
            and math.isfinite(float(recorded))
            and MIN_SOURCE_EPOCH <= float(recorded) <= MAX_SOURCE_EPOCH
        )
        if is_valid:
            accepted += 1
        batch.append(
            (
                int(record_id),
                float(recorded) if recorded is not None else None,
                float(starts) if starts is not None else None,
                float(ends) if ends is not None else None,
                metadata,
            )
        )
        if len(batch) >= BATCH_SIZE:
            flush()
    flush()
    return total, accepted


SILVER_INSERT = """
INSERT INTO silver.measurements (
    read_ts, inverter, measured_at, measurement_date, pv_power_w, load_power_w,
    grid_power_w, line_power_w, line_voltage_v, line_freq_hz, battery_soc_pct,
    battery_voltage_v, battery_current_a, battery_power_w, is_valid, invalid_reason
)
SELECT
    read_ts, inverter, measured_at,
    CASE WHEN measured_at IS NOT NULL THEN
        ((measured_at AT TIME ZONE 'UTC') + interval '{off} hours')::date END,
    pv, outw, grid, line, line_v, line_f, soc, bv, bc, bv * bc,
    reason IS NULL, reason
FROM (
    SELECT
        r.read_ts, r.inverter,
        CASE WHEN r.read_ts BETWEEN 946684800 AND 4102444800
             THEN to_timestamp(r.read_ts) END AS measured_at,
        NULLIF(r.payload->>'pv1power', '')::double precision AS pv,
        NULLIF(r.payload->>'outw_a', '')::double precision AS outw,
        NULLIF(r.payload->>'gridpowerw_a', '')::double precision AS grid,
        NULLIF(r.payload->>'linepowerw_a', '')::double precision AS line,
        NULLIF(r.payload->>'linevolta', '')::double precision AS line_v,
        NULLIF(r.payload->>'linefreq', '')::double precision AS line_f,
        NULLIF(r.payload->>'battsoc', '')::double precision AS soc,
        NULLIF(r.payload->>'battvolt', '')::double precision AS bv,
        NULLIF(r.payload->>'battcurr', '')::double precision AS bc
    FROM bronze.measurements_raw AS r
    WHERE NOT r.is_simulated
) AS t
CROSS JOIN LATERAL (
    SELECT CASE
        WHEN read_ts NOT BETWEEN 946684800 AND 4102444800 THEN 'invalid_timestamp'
        WHEN inverter NOT IN (1, 2) THEN 'unsupported_inverter'
        WHEN pv IS NULL AND outw IS NULL AND grid IS NULL AND line IS NULL
             AND soc IS NULL AND bv IS NULL AND bc IS NULL THEN 'empty_snapshot'
        WHEN pv < 0 THEN 'negative_pv1power'
        WHEN outw < 0 THEN 'negative_outw_a'
        WHEN soc NOT BETWEEN 0 AND 100 THEN 'invalid_battery_soc'
        WHEN bv = 0 THEN 'zero_battery_voltage'
    END AS reason
) AS rule
""".format(off=OFFSET)

DIM_DATE_INSERT = """
INSERT INTO gold.dim_date (date_key, calendar_date, year, quarter, month, day, day_of_week,
                           year_month, month_name, day_name, iso_week, is_weekend, is_historical)
SELECT
    to_char(d::date, 'YYYYMMDD')::integer, d::date,
    extract(year FROM d)::smallint, extract(quarter FROM d)::smallint,
    extract(month FROM d)::smallint, extract(day FROM d)::smallint,
    extract(isodow FROM d)::smallint,
    to_char(d::date, 'YYYY-MM'),
    (ARRAY['enero','febrero','marzo','abril','mayo','junio','julio','agosto',
           'septiembre','octubre','noviembre','diciembre'])[extract(month FROM d)::integer],
    (ARRAY['lunes','martes','miercoles','jueves','viernes','sabado','domingo'])[extract(isodow FROM d)::integer],
    extract(week FROM d)::smallint,
    extract(isodow FROM d) IN (6, 7),
    true
FROM generate_series(
    (SELECT min(event_date) FROM dates)::timestamp, (SELECT max(event_date) FROM dates)::timestamp,
    interval '1 day'
) AS d
ON CONFLICT (date_key) DO UPDATE SET
    year_month = EXCLUDED.year_month, month_name = EXCLUDED.month_name,
    day_name = EXCLUDED.day_name, iso_week = EXCLUDED.iso_week,
    is_weekend = EXCLUDED.is_weekend, is_historical = true
"""

READING_INSERT = """
INSERT INTO gold.fact_energy_reading (
    measured_at, date_key, time_key, inverter_id, pv_power_w, load_power_w, grid_power_w,
    line_power_w, battery_power_w, battery_soc_pct, grid_present, interval_seconds,
    solar_kwh_interval, load_kwh_interval, grid_kwh_interval, grid_import_kwh_interval,
    battery_discharge_kwh_interval, battery_charge_kwh_interval,
    co2_avoided_kg_interval, emission_factor_kg_kwh, is_simulated
)
SELECT
    measured_at,
    to_char(local_ts, 'YYYYMMDD')::integer,
    (extract(hour FROM local_ts)::integer * 60 + extract(minute FROM local_ts)::integer),
    inverter, pv_power_w, load_power_w, grid_power_w, line_power_w, battery_power_w,
    battery_soc_pct, CASE WHEN line_voltage_v IS NOT NULL THEN line_voltage_v >= 100 END,
    CASE WHEN ok THEN dt END,
    CASE WHEN ok AND pv_power_w IS NOT NULL AND next_pv IS NOT NULL
         THEN (pv_power_w + next_pv) / 2 * dt / 3600000 END,
    CASE WHEN ok AND load_power_w IS NOT NULL AND next_load IS NOT NULL
         THEN (load_power_w + next_load) / 2 * dt / 3600000 END,
    CASE WHEN ok AND grid_power_w IS NOT NULL AND next_grid IS NOT NULL
         THEN (greatest(grid_power_w, 0) + greatest(next_grid, 0)) / 2 * dt / 3600000 END,
    CASE WHEN ok AND line_power_w IS NOT NULL AND next_line IS NOT NULL
         THEN (greatest(line_power_w, 0) + greatest(next_line, 0)) / 2 * dt / 3600000 END,
    CASE WHEN ok AND battery_power_w IS NOT NULL AND next_batt IS NOT NULL
         THEN (greatest(battery_power_w, 0) + greatest(next_batt, 0)) / 2 * dt / 3600000 END,
    CASE WHEN ok AND battery_power_w IS NOT NULL AND next_batt IS NOT NULL
         THEN (greatest(-battery_power_w, 0) + greatest(-next_batt, 0)) / 2 * dt / 3600000 END,
    CASE WHEN ok AND pv_power_w IS NOT NULL AND next_pv IS NOT NULL
         THEN (pv_power_w + next_pv) / 2 * dt / 3600000 * %(factor)s END,
    %(factor)s, false
FROM (
    SELECT *,
        (measured_at AT TIME ZONE 'UTC') + interval '{off} hours' AS local_ts,
        extract(epoch FROM next_at - measured_at) AS dt,
        next_at - measured_at <= make_interval(secs => %(gap)s) AS ok
    FROM (
        SELECT *,
            lead(measured_at) OVER w AS next_at,
            lead(pv_power_w) OVER w AS next_pv,
            lead(load_power_w) OVER w AS next_load,
            lead(grid_power_w) OVER w AS next_grid,
            lead(line_power_w) OVER w AS next_line,
            lead(battery_power_w) OVER w AS next_batt
        FROM silver.measurements
        WHERE is_valid AND NOT is_simulated
        WINDOW w AS (PARTITION BY inverter ORDER BY measured_at)
    ) AS lagged
) AS ordered
WHERE measured_at IS NOT NULL
""".format(off=OFFSET)

HOUR_INSERT = """
INSERT INTO gold.fact_energy_hour (
    hour_start, date_key, time_key, inverter_id, solar_kwh, load_kwh, grid_import_kwh,
    battery_discharge_kwh, battery_charge_kwh, co2_avoided_kg, avg_soc_pct, min_soc_pct,
    max_pv_w, readings, covered_seconds, grid_present_ratio, emission_factor_kg_kwh
)
SELECT
    hour_start,
    to_char(hour_local, 'YYYYMMDD')::integer,
    extract(hour FROM hour_local)::integer * 60,
    inverter_id,
    sum(solar_kwh_interval), sum(load_kwh_interval), sum(grid_import_kwh_interval),
    sum(battery_discharge_kwh_interval), sum(battery_charge_kwh_interval),
    sum(co2_avoided_kg_interval), avg(battery_soc_pct), min(battery_soc_pct),
    max(pv_power_w), count(*), coalesce(sum(interval_seconds), 0),
    avg(CASE WHEN grid_present THEN 1.0 WHEN grid_present IS NOT NULL THEN 0.0 END),
    max(emission_factor_kg_kwh)
FROM (
    SELECT *,
        date_trunc('hour', (measured_at AT TIME ZONE 'UTC') + interval '{off} hours') AS hour_local,
        (date_trunc('hour', (measured_at AT TIME ZONE 'UTC') + interval '{off} hours')
            - interval '{off} hours') AT TIME ZONE 'UTC' AS hour_start
    FROM gold.fact_energy_reading
    WHERE NOT is_simulated
) AS r
GROUP BY hour_start, hour_local, inverter_id
""".format(off=OFFSET)

QUALITY_INSERT = """
INSERT INTO gold.fact_quality_day (
    date_key, inverter_id, rows_received, rows_valid, rows_empty, rows_other_invalid,
    covered_seconds, expected_seconds, max_gap_seconds, gaps_over_limit
)
WITH bounds AS (
    SELECT inverter, min(measured_at) AS first_seen, max(measured_at) AS last_seen
    FROM silver.measurements
    WHERE NOT is_simulated AND measured_at IS NOT NULL
    GROUP BY inverter
), days AS (
    SELECT b.inverter, d::date AS local_date,
           (d - interval '{off} hours') AT TIME ZONE 'UTC' AS day_start,
           (d + interval '1 day' - interval '{off} hours') AT TIME ZONE 'UTC' AS day_end,
           b.first_seen, b.last_seen
    FROM bounds AS b
    CROSS JOIN LATERAL generate_series(
        ((b.first_seen AT TIME ZONE 'UTC') + interval '{off} hours')::date::timestamp,
        ((b.last_seen AT TIME ZONE 'UTC') + interval '{off} hours')::date::timestamp,
        interval '1 day'
    ) AS d
), received AS (
    SELECT inverter, measurement_date,
           count(*) AS rows_received,
           count(*) FILTER (WHERE is_valid) AS rows_valid,
           count(*) FILTER (WHERE invalid_reason = 'empty_snapshot') AS rows_empty,
           count(*) FILTER (WHERE NOT is_valid AND invalid_reason <> 'empty_snapshot') AS rows_other
    FROM silver.measurements
    WHERE NOT is_simulated AND measurement_date IS NOT NULL
    GROUP BY inverter, measurement_date
), gaps AS (
    SELECT inverter,
           ((measured_at AT TIME ZONE 'UTC') + interval '{off} hours')::date AS local_date,
           max(gap) AS max_gap,
           coalesce(sum(gap) FILTER (WHERE gap <= %(gap)s), 0) AS covered,
           count(*) FILTER (WHERE gap > %(gap)s) AS gaps_over
    FROM (
        SELECT inverter, measured_at,
               extract(epoch FROM lead(measured_at) OVER (PARTITION BY inverter ORDER BY measured_at)
                                  - measured_at) AS gap
        FROM silver.measurements
        WHERE is_valid AND NOT is_simulated
    ) AS g
    GROUP BY inverter, 2
)
SELECT
    to_char(d.local_date, 'YYYYMMDD')::integer, d.inverter,
    coalesce(r.rows_received, 0), coalesce(r.rows_valid, 0),
    coalesce(r.rows_empty, 0), coalesce(r.rows_other, 0),
    coalesce(g.covered, 0),
    greatest(0, extract(epoch FROM least(d.day_end, d.last_seen) - greatest(d.day_start, d.first_seen))),
    g.max_gap, coalesce(g.gaps_over, 0)
FROM days AS d
LEFT JOIN received AS r ON r.inverter = d.inverter AND r.measurement_date = d.local_date
LEFT JOIN gaps AS g ON g.inverter = d.inverter AND g.local_date = d.local_date
""".format(off=OFFSET)

CONTEXT_EVENTS = """
INSERT INTO gold.fact_event (
    event_id, occurred_at, date_key, time_key, inverter_id, event_type, severity,
    context_source, metadata, ended_at, duration_seconds, context_type
)
SELECT
    'context:' || id::text, ts,
    to_char((ts AT TIME ZONE 'UTC') + interval '{off} hours', 'YYYYMMDD')::integer,
    extract(hour FROM (ts AT TIME ZONE 'UTC') + interval '{off} hours')::integer * 60
        + extract(minute FROM (ts AT TIME ZONE 'UTC') + interval '{off} hours')::integer,
    CASE WHEN metadata->>'inverter' ~ '^[12]$' THEN (metadata->>'inverter')::smallint END,
    'context_record', 'info', metadata->>'source', metadata, ends,
    extract(epoch FROM ends - ts), metadata->>'context_type'
FROM (SELECT id, coalesce(starts, recorded) AS ts, ends, metadata FROM silver.context) AS c
""".format(off=OFFSET)


def episode_insert(event_type: str, severity_sql: str, flag_sql: str, value_sql: str,
                   source: str, id_prefix: str, extra_where: str = "") -> str:
    """Episodios continuos de una condicion sobre las lecturas validas.

    Un episodio se corta cuando la condicion cambia o cuando hay un hueco mayor al limite, y
    necesita al menos dos lecturas seguidas para no contar parpadeos de una sola lectura."""
    return f"""
INSERT INTO gold.fact_event (
    event_id, occurred_at, date_key, time_key, inverter_id, event_type, severity,
    context_source, metadata, ended_at, duration_seconds
)
SELECT
    '{id_prefix}:' || inverter || ':' || extract(epoch FROM start_at)::bigint,
    start_at,
    to_char((start_at AT TIME ZONE 'UTC') + interval '{OFFSET} hours', 'YYYYMMDD')::integer,
    extract(hour FROM (start_at AT TIME ZONE 'UTC') + interval '{OFFSET} hours')::integer * 60
        + extract(minute FROM (start_at AT TIME ZONE 'UTC') + interval '{OFFSET} hours')::integer,
    inverter, '{event_type}', {severity_sql}, '{source}',
    jsonb_build_object('readings', n, 'value', round(metric::numeric, 2)),
    end_at, extract(epoch FROM end_at - start_at)
FROM (
    SELECT inverter, min(measured_at) AS start_at, max(measured_at) AS end_at,
           count(*) AS n, {value_sql} AS metric
    FROM (
        SELECT *, sum(brk) OVER (PARTITION BY inverter ORDER BY measured_at) AS grp
        FROM (
            SELECT inverter, measured_at, {flag_sql} AS flag, battery_soc_pct, line_voltage_v,
                   CASE WHEN lag({flag_sql}) OVER w IS DISTINCT FROM ({flag_sql})
                          OR measured_at - lag(measured_at) OVER w > make_interval(secs => %(gap)s)
                        THEN 1 ELSE 0 END AS brk
            FROM silver.measurements
            WHERE is_valid AND NOT is_simulated {extra_where}
            WINDOW w AS (PARTITION BY inverter ORDER BY measured_at)
        ) AS a
    ) AS b
    WHERE flag
    GROUP BY inverter, grp
    HAVING count(*) >= 2
) AS e
"""


LOW_SOC_EVENTS = episode_insert(
    "low_battery_soc", "CASE WHEN metric < 10 THEN 'critical' ELSE 'warning' END",
    "battery_soc_pct < 20", "min(battery_soc_pct)", "derived_from_battsoc", "derived:low-soc",
    "AND battery_soc_pct IS NOT NULL",
)
GRID_OUTAGE_EVENTS = episode_insert(
    "grid_absent", "CASE WHEN n >= 240 THEN 'warning' ELSE 'info' END",
    "line_voltage_v < 100", "min(line_voltage_v)", "derived_from_linevolta", "derived:grid-absent",
    "AND line_voltage_v IS NOT NULL",
)

GAP_EVENTS = f"""
INSERT INTO gold.fact_event (
    event_id, occurred_at, date_key, time_key, inverter_id, event_type, severity,
    context_source, metadata, ended_at, duration_seconds
)
SELECT
    'derived:gap:' || inverter || ':' || extract(epoch FROM measured_at)::bigint,
    measured_at,
    to_char((measured_at AT TIME ZONE 'UTC') + interval '{OFFSET} hours', 'YYYYMMDD')::integer,
    extract(hour FROM (measured_at AT TIME ZONE 'UTC') + interval '{OFFSET} hours')::integer * 60
        + extract(minute FROM (measured_at AT TIME ZONE 'UTC') + interval '{OFFSET} hours')::integer,
    inverter, 'data_gap',
    CASE WHEN extract(epoch FROM next_at - measured_at) > 3600 THEN 'critical' ELSE 'warning' END,
    'derived_from_read_ts', jsonb_build_object('gap_seconds', round(extract(epoch FROM next_at - measured_at)::numeric)),
    next_at, extract(epoch FROM next_at - measured_at)
FROM (
    SELECT inverter, measured_at,
           lead(measured_at) OVER (PARTITION BY inverter ORDER BY measured_at) AS next_at
    FROM silver.measurements
    WHERE is_valid AND NOT is_simulated AND measured_at IS NOT NULL
) AS g
WHERE next_at - measured_at > make_interval(secs => %(gap)s)
"""


def build_warehouse(target: psycopg.Connection[Any]) -> dict[str, int]:
    gap = float(os.getenv("MAX_INTERVAL_SECONDS", "900"))
    factor = float(required_env("CO2_FACTOR_KG_PER_KWH"))
    params = {"gap": gap, "factor": factor}

    with target.transaction():
        LOGGER.info("Silver: lecturas")
        target.execute("DELETE FROM silver.measurements WHERE NOT is_simulated")
        target.execute(SILVER_INSERT)
        LOGGER.info("Silver: contexto")
        target.execute(
            """
            TRUNCATE silver.context;
            INSERT INTO silver.context (id, recorded, starts, ends, metadata)
            SELECT id, to_timestamp(recorded), to_timestamp(starts),
                   to_timestamp(ends), metadata
            FROM bronze.context_raw
            WHERE recorded BETWEEN 946684800 AND 4102444800
            """
        )
        LOGGER.info("Gold: dimensiones")
        target.execute(
            """
            CREATE TEMP TABLE dates ON COMMIT DROP AS
            SELECT event_date FROM (
                SELECT ((measured_at AT TIME ZONE 'UTC') + interval '{off} hours')::date AS event_date
                FROM silver.measurements WHERE NOT is_simulated AND measured_at IS NOT NULL
                UNION ALL
                SELECT ((coalesce(starts, recorded) AT TIME ZONE 'UTC') + interval '{off} hours')::date
                FROM silver.context
            ) AS x
            """.format(off=OFFSET)
        )
        target.execute(DIM_DATE_INSERT)
        target.execute(
            """
            INSERT INTO gold.dim_inverter (inverter_id, inverter_name, source_system,
                                           observed_pv1_peak_w, observed_output_peak_w)
            SELECT i, 'Inversor ' || i, 'OpenCEM',
                   (SELECT max(pv_power_w) FROM silver.measurements
                    WHERE inverter = i AND is_valid AND NOT is_simulated),
                   (SELECT max(load_power_w) FROM silver.measurements
                    WHERE inverter = i AND is_valid AND NOT is_simulated)
            FROM (VALUES (1), (2)) AS v(i)
            ON CONFLICT (inverter_id) DO UPDATE SET
                inverter_name = EXCLUDED.inverter_name,
                source_system = EXCLUDED.source_system,
                observed_pv1_peak_w = EXCLUDED.observed_pv1_peak_w,
                observed_output_peak_w = EXCLUDED.observed_output_peak_w
            """
        )
        LOGGER.info("Gold: lecturas con energia")
        target.execute("DELETE FROM gold.fact_energy_reading WHERE NOT is_simulated")
        target.execute(READING_INSERT, params)
        LOGGER.info("Gold: resumen por hora y calidad por dia")
        target.execute("TRUNCATE gold.fact_energy_hour")
        target.execute(HOUR_INSERT, params)
        target.execute("TRUNCATE gold.fact_quality_day")
        target.execute(QUALITY_INSERT, params)
        LOGGER.info("Gold: eventos")
        target.execute(
            "DELETE FROM gold.fact_event "
            "WHERE context_source IS DISTINCT FROM 'simulator' "
            "AND context_source IS DISTINCT FROM 'simulator_demo'"
        )
        target.execute(CONTEXT_EVENTS)
        target.execute(LOW_SOC_EVENTS, params)
        target.execute(GRID_OUTAGE_EVENTS, params)
        target.execute(GAP_EVENTS, params)
        counts = target.execute(
            """
            SELECT
                (SELECT count(*) FROM bronze.measurements_raw WHERE NOT is_simulated)::bigint,
                (SELECT count(*) FROM silver.measurements WHERE is_valid AND NOT is_simulated)::bigint,
                (SELECT count(*) FROM silver.measurements WHERE NOT is_valid AND NOT is_simulated)::bigint,
                (SELECT count(*) FROM gold.fact_energy_reading WHERE NOT is_simulated)::bigint,
                (SELECT count(*) FROM bronze.context_raw)::bigint,
                (SELECT count(*) FROM silver.context)::bigint,
                (SELECT count(*) FROM gold.fact_event WHERE event_type = 'context_record')::bigint,
                (SELECT coalesce(sum(readings), 0) FROM gold.fact_energy_hour)::bigint,
                (SELECT coalesce(sum(rows_received), 0) FROM gold.fact_quality_day)::bigint,
                (SELECT count(*) FROM gold.fact_energy_hour)::bigint,
                (SELECT count(*) FROM gold.fact_quality_day)::bigint,
                (SELECT count(*) FROM gold.fact_event WHERE event_type <> 'context_record'
                    AND context_source LIKE 'derived%')::bigint
            """
        ).fetchone()
        if counts is None:
            raise RuntimeError("No se pudieron leer los conteos de conciliacion.")
        return {
            "bronze_measurements": counts[0],
            "silver_valid_measurements": counts[1],
            "silver_rejected_measurements": counts[2],
            "gold_historical_readings": counts[3],
            "bronze_context": counts[4],
            "silver_context": counts[5],
            "gold_context_events": counts[6],
            "gold_hour_readings_sum": counts[7],
            "gold_quality_rows_received_sum": counts[8],
            "gold_hour_rows": counts[9],
            "gold_quality_day_rows": counts[10],
            "gold_derived_events": counts[11],
        }


def rejection_counts(target: psycopg.Connection[Any]) -> dict[str, int]:
    return dict(
        target.execute(
            """
            SELECT invalid_reason, count(*) FROM silver.measurements
            WHERE NOT is_valid AND NOT is_simulated GROUP BY invalid_reason
            """
        ).fetchall()
    )


def energy_totals(target: psycopg.Connection[Any]) -> dict[str, float]:
    row = target.execute(
        """
        SELECT
            (SELECT coalesce(sum(solar_kwh_interval), 0) FROM gold.fact_energy_reading WHERE NOT is_simulated),
            (SELECT coalesce(sum(solar_kwh), 0) FROM gold.fact_energy_hour),
            (SELECT coalesce(sum(grid_import_kwh_interval), 0) FROM gold.fact_energy_reading WHERE NOT is_simulated),
            (SELECT coalesce(sum(grid_import_kwh), 0) FROM gold.fact_energy_hour),
            (SELECT coalesce(sum(co2_avoided_kg_interval), 0) FROM gold.fact_energy_reading WHERE NOT is_simulated),
            (SELECT coalesce(sum(co2_avoided_kg), 0) FROM gold.fact_energy_hour)
        """
    ).fetchone()
    names = ("solar_reading", "solar_hour", "grid_import_reading", "grid_import_hour", "co2_reading", "co2_hour")
    return {name: float(value) for name, value in zip(names, row or ())}


def write_report(
    report_path: Path,
    sqlite_path: Path,
    archive_path: Path,
    measurement_total: int,
    rejected: dict[str, int],
    context_total: int,
    context_accepted: int,
    counts: dict[str, int],
    totals: dict[str, float],
    inventory: list[dict[str, Any]],
    source_sha256: str,
    checksum_verified: bool,
    window: tuple[float | None, float | None],
) -> None:
    report_path.parent.mkdir(parents=True, exist_ok=True)
    remaining = measurement_total
    rule_counts = []
    for rule in QUALITY_RULE_ORDER:
        rejected_count = rejected.get(rule, 0)
        rule_counts.append(
            {
                "rule": rule,
                "before": remaining,
                "rejected": rejected_count,
                "after": remaining - rejected_count,
            }
        )
        remaining -= rejected_count
    report = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "source_sqlite": sqlite_path.name,
        "source_archive": archive_path.name,
        "source_archive_sha256": source_sha256,
        "checksum_verified_against_manifest": checksum_verified,
        "site_utc_offset_hours": OFFSET,
        "load_window_epoch": {"from": window[0], "to": window[1]},
        "sqlite_inventory": inventory,
        "source_measurements_before_rules": measurement_total,
        "quality_rejections_by_first_rule": dict(sorted(rejected.items())),
        "quality_counts_by_sequential_rule": rule_counts,
        "measurements_after_all_rules": measurement_total - sum(rejected.values()),
        "context_before_timestamp_rule": context_total,
        "context_after_timestamp_rule": context_accepted,
        "reconciliation": counts,
        "energy_totals_kwh_kg": totals,
        "idempotency_key": "read_ts + inverter; context id",
        "energy_integration": "trapezoidal; intervals over MAX_INTERVAL_SECONDS excluded",
        "privacy": "context free text is discarded; only an explicit metadata allowlist is retained",
        "warnings": [
            "Solar generation is the PV1 channel; the plant total and curtailed energy are not observed.",
            "Grid import uses linepowerw_a (grid active power); gridpowerw_a is kept but not used.",
            "CO2 avoided uses the configurable SIN factor and must be cited for the selected year.",
        ],
    }
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    LOGGER.info("Evidencia ETL escrita en %s", report_path)


def main() -> None:
    parser = argparse.ArgumentParser(description="Carga OpenCEM y construye el warehouse SolarBI.")
    parser.add_argument(
        "--from-bronze",
        action="store_true",
        help="Reconstruye Silver/Gold solo si Bronze ya coincide con el SQLite fuente.",
    )
    args = parser.parse_args()
    archive_path = Path(required_env("DATASET_ARCHIVE"))
    sqlite_path = Path(
        os.getenv("DATASET_SQLITE", str(archive_path.with_suffix("").with_suffix(".sqlite")))
    )
    report_path = Path(os.getenv("REPORT_PATH", "/reports/etl-report.json"))
    source_sha256, checksum_verified = verify_archive_checksum(archive_path)
    if not checksum_verified:
        raise RuntimeError("La fuente debe verificarse contra un manifiesto SHA-256.")
    ensure_sqlite(archive_path, sqlite_path)
    window = date_window()
    source = sqlite_connection(sqlite_path)
    try:
        inventory = source_inventory(source)
        with psycopg.connect(required_env("DATABASE_URL"), autocommit=False) as target:
            target.execute("SET TIME ZONE 'UTC'")
            create_schema(target)
            if args.from_bronze:
                where, params = source_window_clause(*window)
                measurement_total = source.execute(
                    f"SELECT count(*) FROM analog_measurements{where}", params
                ).fetchone()[0]
                context_total = next(
                    item["row_count"] for item in inventory if item["table"] == "context"
                )
                bronze_counts = target.execute(
                    """
                    SELECT
                        (SELECT count(*) FROM bronze.measurements_raw WHERE NOT is_simulated),
                        (SELECT count(*) FROM bronze.context_raw)
                    """
                ).fetchone()
                if bronze_counts[0] != measurement_total:
                    raise RuntimeError(
                        "Bronze no coincide con la fuente: "
                        f"PostgreSQL={bronze_counts[0]}, SQLite={measurement_total}."
                    )
                context_accepted = bronze_counts[1]
            else:
                measurement_total = import_measurements(source, target, *window)
                context_total, context_accepted = import_context(source, target, *window)
            counts = build_warehouse(target)
            rejected = rejection_counts(target)
            totals = energy_totals(target)
            write_report(
                report_path,
                sqlite_path,
                archive_path,
                measurement_total,
                rejected,
                context_total,
                context_accepted,
                counts,
                totals,
                inventory,
                source_sha256,
                checksum_verified,
                window,
            )
            LOGGER.info(
                "ETL terminado: %s lecturas, %s validas, %s contextos; conciliacion=%s",
                f"{measurement_total:,}",
                f"{counts['silver_valid_measurements']:,}",
                f"{context_total:,}",
                counts,
            )
    finally:
        source.close()


if __name__ == "__main__":
    main()
