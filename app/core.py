from __future__ import annotations

import json
import math
import os
from collections.abc import Mapping
from typing import Any


MIN_SOURCE_EPOCH = 946_684_800
MAX_SOURCE_EPOCH = 4_102_444_800

# El sitio de OpenCEM esta en Longgang, Shenzhen (Asia/Shanghai, UTC+8 sin horario de verano).
# Las marcas de la fuente son UTC; los dias y las horas del modelo son del sitio.
SITE_UTC_OFFSET_HOURS = int(os.getenv("SITE_UTC_OFFSET_HOURS", "8"))

# Solo categorias; el texto libre (descripciones, comandos, rutas, proyectos) no pasa a la base.
ALLOWED_CONTEXT_KEYS = (
    "inverter",
    "source",
    "device",
    "context_type",
    "task_type",
    "llm_estimated_task_effort",
    "cpu_cores_active",
    "day_type",
    "gap_seconds",
    "threshold_seconds",
)

KEY_CHANNELS = ("pv1power", "outw_a", "gridpowerw_a", "linepowerw_a", "battsoc", "battvolt", "battcurr")


def _number(value: Any) -> float | None:
    if value is None:
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def measurement_error(row: Mapping[str, Any]) -> str | None:
    """Primera regla que rechaza la fila; el orden es el mismo que usa el SQL de Silver."""
    timestamp = _number(row.get("read_ts"))
    if timestamp is None or not MIN_SOURCE_EPOCH <= timestamp <= MAX_SOURCE_EPOCH:
        return "invalid_timestamp"

    inverter = _number(row.get("inverter"))
    if inverter not in (1, 2):
        return "unsupported_inverter"

    if all(_number(row.get(column)) is None for column in KEY_CHANNELS):
        return "empty_snapshot"

    for column in ("pv1power", "outw_a"):
        value = _number(row.get(column))
        if value is not None and value < 0:
            return f"negative_{column}"

    soc = _number(row.get("battsoc"))
    if soc is not None and not 0 <= soc <= 100:
        return "invalid_battery_soc"

    if _number(row.get("battvolt")) == 0:
        return "zero_battery_voltage"
    return None


def trapezoid_kwh(
    power_now_w: float | None,
    power_next_w: float | None,
    seconds: float | None,
    max_gap_seconds: float,
) -> float | None:
    if (
        power_now_w is None
        or power_next_w is None
        or seconds is None
        or seconds <= 0
        or seconds > max_gap_seconds
        or not math.isfinite(power_now_w)
        or not math.isfinite(power_next_w)
    ):
        return None
    return ((power_now_w + power_next_w) / 2) * seconds / 3_600_000


def sanitize_context(value: Any) -> dict[str, Any]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return {}
    if not isinstance(value, dict):
        return {}
    return {
        key: value[key]
        for key in ALLOWED_CONTEXT_KEYS
        if key in value and isinstance(value[key], (str, int, float, bool))
    }
