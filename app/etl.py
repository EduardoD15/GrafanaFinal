from __future__ import annotations

import json
import argparse
import hashlib
import logging
import math
import os
import re
import sqlite3
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import psycopg
from psycopg.types.json import Jsonb
import zstandard

from app.core import (
    MAX_SOURCE_EPOCH,
    MIN_SOURCE_EPOCH,
    measurement_error,
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
    "negative_pv1power",
    "negative_outw_a",
    "invalid_battery_soc",
)


def required_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Falta la variable obligatoria {name}.")
    return value


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
    connection.execute(
        "ALTER TABLE silver.measurements "
        "ADD COLUMN IF NOT EXISTS measurement_date DATE"
    )
    connection.commit()


def import_measurements(
    source: sqlite3.Connection, target: psycopg.Connection[Any]
) -> tuple[int, Counter[str]]:
    source_cursor = source.execute(
        "SELECT * FROM analog_measurements ORDER BY read_ts, inverter"
    )
    columns = [column[0] for column in source_cursor.description or ()]
    required = {"read_ts", "inverter", "pv1power", "outw_a", "gridpowerw_a", "battsoc"}
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
    rejected: Counter[str] = Counter()
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
        error = measurement_error(row)
        if error:
            rejected[error] += 1
        timestamp = float(row["read_ts"])
        inverter = int(row["inverter"])
        batch.append((timestamp, inverter, row))
        if len(batch) >= BATCH_SIZE:
            flush()
            if total % (BATCH_SIZE * 10) == 0:
                LOGGER.info("Lecturas examinadas: %s", f"{total:,}")
    flush()
    return total, rejected


def import_context(
    source: sqlite3.Connection, target: psycopg.Connection[Any]
) -> tuple[int, int]:
    rows = source.execute("SELECT id, recorded, start, end, value FROM context ORDER BY id")
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


def build_warehouse(target: psycopg.Connection[Any]) -> dict[str, int]:
    max_gap = float(os.getenv("MAX_INTERVAL_SECONDS", "300"))
    emission_factor = float(required_env("CO2_FACTOR_KG_PER_KWH"))

    with target.transaction():
        target.execute("DELETE FROM silver.measurements WHERE NOT is_simulated")
        target.execute(
            """
            INSERT INTO silver.measurements (
                read_ts, inverter, measured_at, measurement_date, pv_power_w,
                load_power_w, grid_power_w, battery_soc_pct, battery_voltage_v,
                battery_current_a, is_valid, invalid_reason
            )
            SELECT
                read_ts, inverter,
                CASE WHEN read_ts BETWEEN 946684800 AND 4102444800
                    THEN to_timestamp(read_ts) END,
                CASE WHEN read_ts BETWEEN 946684800 AND 4102444800
                    THEN to_timestamp(read_ts)::date END,
                NULLIF(payload->>'pv1power', '')::double precision,
                NULLIF(payload->>'outw_a', '')::double precision,
                NULLIF(payload->>'gridpowerw_a', '')::double precision,
                NULLIF(payload->>'battsoc', '')::double precision,
                NULLIF(payload->>'battvolt', '')::double precision,
                NULLIF(payload->>'battcurr', '')::double precision,
                (
                    read_ts BETWEEN 946684800 AND 4102444800
                    AND inverter IN (1, 2)
                    AND (payload->>'pv1power' IS NULL OR
                         (payload->>'pv1power')::double precision >= 0)
                    AND (payload->>'outw_a' IS NULL OR
                         (payload->>'outw_a')::double precision >= 0)
                    AND (payload->>'battsoc' IS NULL OR
                         (payload->>'battsoc')::double precision BETWEEN 0 AND 100)
                ),
                CASE
                    WHEN read_ts NOT BETWEEN 946684800 AND 4102444800
                        THEN 'invalid_timestamp'
                    WHEN inverter NOT IN (1, 2) THEN 'unsupported_inverter'
                    WHEN payload->>'pv1power' IS NOT NULL AND
                         (payload->>'pv1power')::double precision < 0
                        THEN 'negative_pv1power'
                    WHEN payload->>'outw_a' IS NOT NULL AND
                         (payload->>'outw_a')::double precision < 0
                        THEN 'negative_outw_a'
                    WHEN payload->>'battsoc' IS NOT NULL AND
                         (payload->>'battsoc')::double precision NOT BETWEEN 0 AND 100
                        THEN 'invalid_battery_soc'
                    ELSE NULL
                END
            FROM bronze.measurements_raw
            WHERE NOT is_simulated
            """
        )
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
        target.execute(
            """
            INSERT INTO gold.dim_date (date_key, calendar_date, year, quarter, month,
                                      day, day_of_week)
            SELECT
                to_char(d::date, 'YYYYMMDD')::integer,
                d::date,
                extract(year FROM d)::smallint,
                extract(quarter FROM d)::smallint,
                extract(month FROM d)::smallint,
                extract(day FROM d)::smallint,
                extract(isodow FROM d)::smallint
            FROM generate_series(
                (SELECT min(event_date) FROM (
                    SELECT (measured_at AT TIME ZONE 'UTC')::date AS event_date
                    FROM silver.measurements
                    WHERE is_valid AND NOT is_simulated
                    UNION ALL
                    SELECT (recorded AT TIME ZONE 'UTC')::date
                    FROM silver.context
                ) AS event_dates),
                (SELECT max(event_date) FROM (
                    SELECT (measured_at AT TIME ZONE 'UTC')::date AS event_date
                    FROM silver.measurements
                    WHERE is_valid AND NOT is_simulated
                    UNION ALL
                    SELECT (recorded AT TIME ZONE 'UTC')::date
                    FROM silver.context
                ) AS event_dates),
                interval '1 day'
            ) AS d
            ON CONFLICT (date_key) DO NOTHING
            """
        )
        target.execute(
            """
            INSERT INTO gold.dim_inverter
                (inverter_id, inverter_name, rated_power_kw, pv_capacity_kwp, source_system)
            VALUES
                (1, 'Inversor 1', 8, 12.48, 'OpenCEM'),
                (2, 'Inversor 2', 8, 12.48, 'OpenCEM')
            ON CONFLICT (inverter_id) DO UPDATE SET
                inverter_name = EXCLUDED.inverter_name,
                rated_power_kw = EXCLUDED.rated_power_kw,
                pv_capacity_kwp = EXCLUDED.pv_capacity_kwp,
                source_system = EXCLUDED.source_system
            """
        )
        target.execute(
            """
            INSERT INTO gold.fact_energy_reading (
                measured_at, date_key, time_key, inverter_id, pv_power_w,
                load_power_w, grid_power_w, battery_soc_pct,
                solar_kwh_interval, load_kwh_interval, grid_kwh_interval,
                co2_avoided_kg_interval, emission_factor_kg_kwh, is_simulated
            )
            SELECT
                measured_at,
                to_char(measured_at, 'YYYYMMDD')::integer,
                extract(hour FROM measured_at)::integer * 60 +
                    extract(minute FROM measured_at)::integer,
                inverter,
                pv_power_w,
                load_power_w,
                grid_power_w,
                battery_soc_pct,
                CASE WHEN next_at - measured_at <= make_interval(secs => %s)
                      AND pv_power_w IS NOT NULL AND next_pv_w IS NOT NULL
                     THEN ((pv_power_w + next_pv_w) / 2) *
                          extract(epoch FROM next_at - measured_at) / 3600000 END,
                CASE WHEN next_at - measured_at <= make_interval(secs => %s)
                      AND load_power_w IS NOT NULL AND next_load_w IS NOT NULL
                     THEN ((load_power_w + next_load_w) / 2) *
                          extract(epoch FROM next_at - measured_at) / 3600000 END,
                CASE WHEN next_at - measured_at <= make_interval(secs => %s)
                      AND grid_power_w IS NOT NULL AND next_grid_w IS NOT NULL
                     THEN ((greatest(grid_power_w, 0) + greatest(next_grid_w, 0)) / 2) *
                          extract(epoch FROM next_at - measured_at) / 3600000 END,
                CASE WHEN next_at - measured_at <= make_interval(secs => %s)
                      AND pv_power_w IS NOT NULL AND next_pv_w IS NOT NULL
                     THEN ((pv_power_w + next_pv_w) / 2) *
                          extract(epoch FROM next_at - measured_at) / 3600000 * %s END,
                %s,
                false
            FROM (
                SELECT *,
                    lead(measured_at) OVER (PARTITION BY inverter ORDER BY measured_at) AS next_at,
                    lead(pv_power_w) OVER (PARTITION BY inverter ORDER BY measured_at) AS next_pv_w,
                    lead(load_power_w) OVER (PARTITION BY inverter ORDER BY measured_at) AS next_load_w,
                    lead(grid_power_w) OVER (PARTITION BY inverter ORDER BY measured_at) AS next_grid_w
                FROM silver.measurements
                WHERE is_valid AND NOT is_simulated
            ) AS ordered
            WHERE measured_at IS NOT NULL
            ON CONFLICT (measured_at, inverter_id) DO UPDATE SET
                date_key = EXCLUDED.date_key, time_key = EXCLUDED.time_key,
                pv_power_w = EXCLUDED.pv_power_w, load_power_w = EXCLUDED.load_power_w,
                grid_power_w = EXCLUDED.grid_power_w,
                battery_soc_pct = EXCLUDED.battery_soc_pct,
                solar_kwh_interval = EXCLUDED.solar_kwh_interval,
                load_kwh_interval = EXCLUDED.load_kwh_interval,
                grid_kwh_interval = EXCLUDED.grid_kwh_interval,
                co2_avoided_kg_interval = EXCLUDED.co2_avoided_kg_interval,
                emission_factor_kg_kwh = EXCLUDED.emission_factor_kg_kwh
            """,
            (
                max_gap,
                max_gap,
                max_gap,
                max_gap,
                emission_factor,
                emission_factor,
            ),
        )
        target.execute(
            """
            INSERT INTO gold.fact_event (
                event_id, occurred_at, date_key, time_key, inverter_id,
                event_type, severity, context_source, metadata
            )
            SELECT
                'context:' || id::text, recorded,
                to_char(recorded, 'YYYYMMDD')::integer,
                extract(hour FROM recorded)::integer * 60 +
                    extract(minute FROM recorded)::integer,
                CASE WHEN metadata->>'inverter' ~ '^[12]$'
                     THEN (metadata->>'inverter')::smallint END,
                'context_record', 'info',
                metadata->>'source', metadata
            FROM silver.context
            ON CONFLICT (event_id) DO UPDATE SET
                occurred_at = EXCLUDED.occurred_at,
                date_key = EXCLUDED.date_key, time_key = EXCLUDED.time_key,
                inverter_id = EXCLUDED.inverter_id,
                context_source = EXCLUDED.context_source, metadata = EXCLUDED.metadata
            """
        )
        target.execute(
            """
            INSERT INTO gold.fact_event (
                event_id, occurred_at, date_key, time_key, inverter_id,
                event_type, severity, context_source, metadata
            )
            SELECT
                'low-soc:' || measured_at::text || ':' || inverter,
                measured_at, to_char(measured_at, 'YYYYMMDD')::integer,
                extract(hour FROM measured_at)::integer * 60 +
                    extract(minute FROM measured_at)::integer,
                inverter, 'low_battery_soc',
                CASE WHEN battery_soc_pct < 10 THEN 'critical' ELSE 'warning' END,
                'derived_from_battsoc',
                jsonb_build_object('battery_soc_pct', battery_soc_pct)
            FROM silver.measurements
            WHERE is_valid AND NOT is_simulated AND battery_soc_pct < 20
            ON CONFLICT (event_id) DO UPDATE SET
                occurred_at = EXCLUDED.occurred_at, date_key = EXCLUDED.date_key,
                time_key = EXCLUDED.time_key, severity = EXCLUDED.severity,
                metadata = EXCLUDED.metadata
            """
        )
        counts = target.execute(
            """
            SELECT
                (SELECT count(*) FROM bronze.measurements_raw WHERE NOT is_simulated)::bigint,
                (SELECT count(*) FROM silver.measurements WHERE is_valid AND NOT is_simulated)::bigint,
                (SELECT count(*) FROM silver.measurements WHERE NOT is_valid AND NOT is_simulated)::bigint,
                (SELECT count(*) FROM gold.fact_energy_reading WHERE NOT is_simulated)::bigint,
                (SELECT count(*) FROM bronze.context_raw)::bigint,
                (SELECT count(*) FROM silver.context)::bigint,
                (SELECT count(*) FROM gold.fact_event WHERE event_type = 'context_record')::bigint
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
        }


def write_report(
    report_path: Path,
    sqlite_path: Path,
    archive_path: Path,
    measurement_total: int,
    rejected: Counter[str],
    context_total: int,
    context_accepted: int,
    counts: dict[str, int],
    inventory: list[dict[str, Any]],
    source_sha256: str,
    checksum_verified: bool,
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
        "sqlite_inventory": inventory,
        "source_measurements_before_rules": measurement_total,
        "quality_rejections_by_first_rule": dict(sorted(rejected.items())),
        "quality_counts_by_sequential_rule": rule_counts,
        "measurements_after_all_rules": measurement_total - sum(rejected.values()),
        "context_before_timestamp_rule": context_total,
        "context_after_timestamp_rule": context_accepted,
        "reconciliation": counts,
        "idempotency_key": "read_ts + inverter; context id",
        "energy_integration": "trapezoidal; intervals over MAX_INTERVAL_SECONDS excluded",
        "privacy": "context free text is discarded; only an explicit metadata allowlist is retained",
        "warnings": [
            "Solar generation is a PV1 power proxy; export/curtailment is not observed.",
            "CO2 avoided uses the configurable SIN factor and must be cited for the selected year.",
            "Load output uses outw_a (phase A); the dataset dictionary does not certify outsumw units.",
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
    source = sqlite_connection(sqlite_path)
    try:
        inventory = source_inventory(source)
        with psycopg.connect(required_env("DATABASE_URL"), autocommit=False) as target:
            target.execute("SET TIME ZONE 'UTC'")
            create_schema(target)
            if args.from_bronze:
                measurement_total = next(
                    item["row_count"] for item in inventory
                    if item["table"] == "analog_measurements"
                )
                context_total = next(
                    item["row_count"] for item in inventory
                    if item["table"] == "context"
                )
                bronze_counts = target.execute(
                    """
                    SELECT
                        (SELECT count(*) FROM bronze.measurements_raw WHERE NOT is_simulated),
                        (SELECT count(*) FROM bronze.context_raw)
                    """
                ).fetchone()
                if bronze_counts != (measurement_total, context_total):
                    raise RuntimeError(
                        "Bronze no coincide con la fuente completa: "
                        f"PostgreSQL={bronze_counts}, SQLite="
                        f"({measurement_total}, {context_total})."
                    )
                counts = build_warehouse(target)
                rejected = Counter(dict(target.execute(
                    """
                    SELECT invalid_reason, count(*)
                    FROM silver.measurements
                    WHERE NOT is_valid AND NOT is_simulated
                    GROUP BY invalid_reason
                    """
                ).fetchall()))
                context_accepted = counts["silver_context"]
            else:
                measurement_total, rejected = import_measurements(source, target)
                context_total, context_accepted = import_context(source, target)
                counts = build_warehouse(target)
            write_report(
                report_path,
                sqlite_path,
                archive_path,
                measurement_total,
                rejected,
                context_total,
                context_accepted,
                counts,
                inventory,
                source_sha256,
                checksum_verified,
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
