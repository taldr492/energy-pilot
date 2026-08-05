
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal, Optional
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from zoneinfo import ZoneInfo
import json
import math
import os
import re
import tempfile
import threading
import time
from copy import deepcopy

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

VERSION = "0.2.96"
APP_DIR = Path(__file__).resolve().parent
STATIC_DIR = APP_DIR / "static"
CONFIG_DIR = Path("/config")
CONFIG_FILE = CONFIG_DIR / "energy_pilot_config.json"
LEARNING_FILE = CONFIG_DIR / "energy_pilot_learning.json"
OVERRIDES_FILE = CONFIG_DIR / "energy_pilot_planner_overrides.json"
BENEFITS_FILE = CONFIG_DIR / "energy_pilot_benefits.json"
HISTORY_FILE = CONFIG_DIR / "energy_pilot_measurement_history.json"
INSIGHTS_FILE = CONFIG_DIR / "energy_pilot_insights.json"
HA_API = "http://supervisor/core/api"
SUPERVISOR_TOKEN = os.environ.get("SUPERVISOR_TOKEN", "")
WEATHER_FORECAST_CACHE: dict = {"fetched_monotonic": 0.0, "result": None, "error": None}
HA_CONFIG_CACHE: dict = {"fetched_monotonic": 0.0, "result": None}
HA_STATES_CACHE: dict = {"fetched_monotonic": 0.0, "result": []}
BENEFITS_CACHE: dict = {"loaded": False, "data": {}, "last_saved_monotonic": 0.0}
HISTORY_CACHE: dict = {"loaded": False, "data": {}, "last_backfill_monotonic": 0.0, "last_saved_monotonic": 0.0}
HISTORY_LOCK = threading.RLock()
INSIGHTS_CACHE: dict = {"loaded": False, "data": {}, "last_saved_monotonic": 0.0}
INSIGHTS_LOCK = threading.RLock()

app = FastAPI(title="Energy Pilot", version=VERSION)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

class SiteConfig(BaseModel):
    name: str = Field(default="Home", min_length=1, max_length=80)
    timezone: str = Field(default="Europe/Tallinn", min_length=1, max_length=80)
    country: str = Field(default="EE", min_length=2, max_length=2)
    currency: str = Field(default="EUR", min_length=3, max_length=3)

class PlanningConfig(BaseModel):
    slot_minutes: Literal[15] = 15
    horizon_hours: Literal[12, 24, 36, 48] = 36
    strategy: Literal["balanced","export_value","cost_minimization","reserve_first","self_consumption"] = "export_value"
    behavior_profile: Literal["value_first", "balanced", "resilience_first", "custom"] = "balanced"
    minimum_sell_profit_cents_kwh: float = Field(default=2.0, ge=0, le=100)
    minimum_horizon_gain_eur: float = Field(default=0.50, ge=0, le=10000)
    custom_solar_buffer_percent: float = Field(default=7.0, ge=0, le=50)
    custom_min_forecast_confidence_percent: int = Field(default=70, ge=0, le=100)
    custom_curtailment_penalty_cents_kwh: float = Field(default=8.0, ge=0, le=100)
    custom_wear_cost_multiplier: float = Field(default=1.15, ge=0.1, le=10)

class GridPolicyConfig(BaseModel):
    max_import_kw: float = Field(default=17.0, gt=0, le=1000)
    max_export_kw: float = Field(default=15.0, ge=0, le=1000)

class BatteryPolicyConfig(BaseModel):
    min_operational_soc_percent: float = Field(default=15.0, ge=0, le=100)
    reserve_soc_percent: float = Field(default=15.0, ge=0, le=100)
    max_planned_soc_percent: float = Field(default=100.0, ge=0, le=100)
    max_charge_kw: float = Field(default=15.0, gt=0, le=1000)
    max_discharge_kw: float = Field(default=15.0, gt=0, le=1000)
    roundtrip_efficiency_percent: float = Field(default=90.0, gt=50, le=100)
    degradation_cost_cents_kwh: float = Field(default=0.0, ge=0, le=100)
    system_cost_eur: float = Field(default=0.0, ge=0, le=1000000)
    warranted_cycles: int = Field(default=6000, gt=0, le=100000)

class ForecastConnectorConfig(BaseModel):
    pv_forecast_entity: str = ""
    load_forecast_entity: str = ""
    fallback_load_kw: Optional[float] = Field(default=None, ge=0, le=1000)
    weather_entity: str = ""
    solar_peak_kw: float = Field(default=25.48, gt=0, le=1000)
    solar_tilt_degrees: float = Field(default=22.0, ge=0, le=90)
    solar_azimuth_degrees: float = Field(default=180.0, ge=0, le=360)

class BatteryConnectorConfig(BaseModel):
    capacity_entity: str = ""
    soc_entity: str = ""
    power_entity: str = ""
    capacity_source: Literal["automatic", "manual_override"] = "automatic"
    manual_capacity_kwh: Optional[float] = Field(default=None, gt=0, le=5000)
    cycle_count_entity: str = ""
    temperature_entity: str = ""

class PowerConnectorConfig(BaseModel):
    pv_power_entity: str = ""
    load_power_entity: str = ""
    grid_power_entity: str = ""
    inverter_temperature_entity: str = ""
    stale_after_seconds: int = Field(default=120, ge=10, le=3600)

class PriceConnectorConfig(BaseModel):
    entity: str = ""
    price_unit: Literal["auto", "eur_per_kwh", "cents_per_kwh", "eur_per_mwh"] = "auto"
    source_includes_vat: bool = False
    include_vat: bool = True
    vat_percent: float = Field(default=24.0, ge=0, le=100)
    stale_after_seconds: int = Field(default=7200, ge=60, le=86400)

class EnergyTariffConfig(BaseModel):
    import_margin_cents_kwh: float = Field(default=0.0, ge=-100, le=100)
    export_margin_cents_kwh: float = Field(default=-0.81, ge=-100, le=100)
    electricity_excise_cents_kwh: float = Field(default=0.21, ge=0, le=100)
    renewable_fee_cents_kwh: float = Field(default=0.84, ge=0, le=100)
    security_of_supply_fee_cents_kwh: float = Field(default=0.758, ge=0, le=100)
    import_balancing_cost_cents_kwh: float = Field(default=0.373, ge=-100, le=100)
    export_balancing_cost_cents_kwh: float = Field(default=-0.373, ge=-100, le=100)
    grid_weekday_day_cents_kwh: float = Field(default=3.69, ge=0, le=100)
    grid_weekday_night_cents_kwh: float = Field(default=2.10, ge=0, le=100)
    grid_weekend_holiday_cents_kwh: float = Field(default=2.10, ge=0, le=100)
    grid_day_start_hour: int = Field(default=7, ge=0, le=23)
    grid_day_end_hour: int = Field(default=22, ge=1, le=24)
    holiday_dates: list[str] = Field(default_factory=list)
    renewable_support_enabled: bool = False
    renewable_support_cents_kwh: float = Field(default=5.37, ge=0, le=100)

class RuntimeConfig(BaseModel):
    mode: Literal["simulation", "approval", "live"] = "simulation"

class QilowattConfig(BaseModel):
    mode: Literal["disabled", "ha_dispatch"] = "disabled"
    mode_entity: str = ""
    source_entity: str = ""
    power_limit_entity: str = ""
    connected_entity: str = ""

class SetupConfig(BaseModel):
    completed: bool = False
    completed_at: str = ""
    auto_discovery: bool = True

class EnergyPilotConfig(BaseModel):
    version: int = 15
    revision: int = Field(default=1, ge=1)
    site: SiteConfig = SiteConfig()
    planning: PlanningConfig = PlanningConfig()
    grid_policy: GridPolicyConfig = GridPolicyConfig()
    battery_policy: BatteryPolicyConfig = BatteryPolicyConfig()
    battery_connector: BatteryConnectorConfig = BatteryConnectorConfig()
    power_connector: PowerConnectorConfig = PowerConnectorConfig()
    forecast_connector: ForecastConnectorConfig = ForecastConnectorConfig()
    price_connector: PriceConnectorConfig = PriceConnectorConfig()
    energy_tariff: EnergyTariffConfig = EnergyTariffConfig()
    qilowatt: QilowattConfig = QilowattConfig()
    runtime: RuntimeConfig = RuntimeConfig()
    setup: SetupConfig = SetupConfig()

    def validate_cross_fields(self):
        p = self.battery_policy
        if p.reserve_soc_percent < p.min_operational_soc_percent:
            raise ValueError("Reserve SOC may not be lower than minimum operational SOC.")
        if p.min_operational_soc_percent > p.max_planned_soc_percent:
            raise ValueError("Minimum operational SOC may not be higher than maximum planned SOC.")
        if self.battery_connector.capacity_source == "manual_override" and not self.battery_connector.manual_capacity_kwh:
            raise ValueError("Manual battery capacity is required when manual override is enabled.")
        if self.energy_tariff.grid_day_start_hour >= self.energy_tariff.grid_day_end_hour:
            raise ValueError("Grid day start must be earlier than grid day end.")
        return self

def now():
    return datetime.now(timezone.utc).isoformat()

def atomic_write(path: Path, payload: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=".energy-pilot-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
            f.write("\n")
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def load_planner_overrides() -> dict:
    try:
        raw = json.loads(OVERRIDES_FILE.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            return raw
    except (OSError, ValueError):
        pass
    return {}


def save_planner_overrides(overrides: dict) -> None:
    allowed_actions = {"NORMAL", "BUY", "SELL", "SAVE BATTERY", "LIMIT EXPORT", "PV SELL"}
    cleaned = {}
    cutoff = datetime.now(timezone.utc) - timedelta(hours=1)
    for stamp, value in overrides.items():
        parsed = parse_time(stamp)
        if not parsed or parsed < cutoff or not isinstance(value, dict):
            continue
        action = str(value.get("action") or "").upper()
        if action not in allowed_actions:
            continue
        cleaned[parsed.isoformat()] = {
            "action": action,
            "power_kw": max(0.0, float(value["power_kw"])) if value.get("power_kw") not in (None, "") else None,
            "target_soc_percent": max(0.0, min(100.0, float(value["target_soc_percent"]))) if value.get("target_soc_percent") not in (None, "") else None,
        }
    atomic_write(OVERRIDES_FILE, cleaned)


def load_learning() -> dict:
    try:
        raw = json.loads(LEARNING_FILE.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            raw.setdefault("load_profiles", {})
            raw.setdefault("pv_factors", {})
            return raw
    except (OSError, ValueError):
        pass
    return {"version": 1, "last_sample_at": None, "load_profiles": {}, "pv_factors": {}}


def learning_bucket(stamp: datetime, timezone_name: str) -> str:
    local = stamp.astimezone(ZoneInfo(timezone_name))
    day_type = "weekend" if local.weekday() >= 5 else "weekday"
    return f"{day_type}:{local.hour:02d}:{(local.minute // 15) * 15:02d}"


def learned_load_points(cfg: EnergyPilotConfig, slots: list[dict]) -> tuple[list[Optional[float]], dict]:
    learning = load_learning()
    profiles = learning.get("load_profiles", {})
    values, counts = [], []
    for slot in slots:
        stamp = parse_time(slot.get("start"))
        profile = profiles.get(learning_bucket(stamp, cfg.site.timezone), {}) if stamp else {}
        count = int(profile.get("count") or 0)
        values.append(round(float(profile["value"]), 3) if count >= 3 and profile.get("value") is not None else None)
        counts.append(count)
    usable = [count for value, count in zip(values, counts) if value is not None]
    return values, {
        "available": bool(usable), "mode": "learned_history",
        "points": len(usable), "samples": sum(usable),
        "average_samples_per_slot": round(sum(usable) / len(usable), 1) if usable else 0,
    }


def pv_learning_factor(cfg: EnergyPilotConfig, stamp: Optional[datetime]) -> tuple[float, int]:
    if stamp is None:
        return 1.0, 0
    local = stamp.astimezone(ZoneInfo(cfg.site.timezone))
    profile = load_learning().get("pv_factors", {}).get(f"{local.month:02d}:{local.hour:02d}", {})
    return max(0.35, min(1.65, float(profile.get("value") or 1.0))), int(profile.get("count") or 0)


def update_learning(cfg: EnergyPilotConfig, snapshot: dict, price: dict) -> None:
    """Persist one bounded learning sample every five minutes."""
    observed = parse_time(snapshot.get("observed_at")) or datetime.now(timezone.utc)
    learning = load_learning()
    previous = parse_time(learning.get("last_sample_at"))
    if previous and (observed - previous).total_seconds() < 300:
        return
    load_kw = snapshot.get("load", {}).get("power_kw", {}).get("value")
    pv_kw = snapshot.get("pv", {}).get("power_kw", {}).get("value")
    if load_kw is not None and load_kw >= 0:
        key = learning_bucket(observed, cfg.site.timezone)
        profile = learning["load_profiles"].setdefault(key, {"value": float(load_kw), "count": 0})
        count = int(profile.get("count") or 0)
        alpha = 0.18 if count < 12 else 0.06
        profile["value"] = round((1 - alpha) * float(profile.get("value") or load_kw) + alpha * float(load_kw), 4)
        profile["count"] = min(10000, count + 1)
    slots = price.get("slots") or []
    current = slots[0] if slots else {}
    raw_pv = current.get("pv_raw_forecast_kw")
    if pv_kw is not None and raw_pv is not None and raw_pv >= 0.5:
        local = observed.astimezone(ZoneInfo(cfg.site.timezone))
        key = f"{local.month:02d}:{local.hour:02d}"
        profile = learning["pv_factors"].setdefault(key, {"value": 1.0, "count": 0})
        count = int(profile.get("count") or 0)
        ratio = max(0.35, min(1.65, float(pv_kw) / float(raw_pv)))
        alpha = 0.12 if count < 10 else 0.04
        profile["value"] = round((1 - alpha) * float(profile.get("value") or 1.0) + alpha * ratio, 4)
        profile["count"] = min(10000, count + 1)
    learning["last_sample_at"] = observed.isoformat()
    atomic_write(LEARNING_FILE, learning)


def load_measurement_history() -> dict:
    if HISTORY_CACHE["loaded"]:
        return HISTORY_CACHE["data"]
    try:
        raw = json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            raw.setdefault("version", 1)
            raw.setdefault("slots", {})
            raw.setdefault("last_live_sample_at", None)
            HISTORY_CACHE.update({"loaded": True, "data": raw})
            return raw
    except (OSError, ValueError):
        pass
    data = {"version": 1, "last_live_sample_at": None, "slots": {}}
    HISTORY_CACHE.update({"loaded": True, "data": data})
    return data


def history_bucket(stamp: datetime) -> datetime:
    utc = stamp.astimezone(timezone.utc)
    return utc.replace(minute=(utc.minute // 15) * 15, second=0, microsecond=0)


def history_metric(slot: dict, key: str, value: Optional[float], weight_seconds: float, source: str) -> None:
    if value is None or not math.isfinite(float(value)) or weight_seconds <= 0:
        return
    metric = slot.setdefault("metrics", {}).setdefault(key, {"weighted_sum": 0.0, "weight_seconds": 0.0})
    metric["weighted_sum"] = float(metric.get("weighted_sum") or 0.0) + float(value) * weight_seconds
    metric["weight_seconds"] = float(metric.get("weight_seconds") or 0.0) + weight_seconds
    slot.setdefault("sources", {})[key] = source


def history_metric_value(slot: dict, key: str) -> Optional[float]:
    metric = slot.get("metrics", {}).get(key, {})
    weight = float(metric.get("weight_seconds") or 0.0)
    return float(metric.get("weighted_sum") or 0.0) / weight if weight > 0 else None


def replace_history_metric(slot: dict, key: str, weighted_sum: float, weight_seconds: float, source: str) -> None:
    if weight_seconds <= 0:
        return
    slot.setdefault("metrics", {})[key] = {
        "weighted_sum": round(float(weighted_sum), 8),
        "weight_seconds": round(float(weight_seconds), 3),
    }
    slot.setdefault("sources", {})[key] = source


def history_slot(data: dict, bucket: datetime) -> dict:
    key = bucket.isoformat()
    return data.setdefault("slots", {}).setdefault(key, {
        "start": key,
        "end": (bucket + timedelta(minutes=15)).isoformat(),
        "metrics": {},
        "sources": {},
        "sample_count": 0,
        "last_observed_at": None,
    })


def power_history_value(raw_value, unit: str) -> Optional[float]:
    try:
        value = float(raw_value)
    except (TypeError, ValueError):
        return None
    normalized = str(unit or "W").strip().lower()
    if normalized == "kw":
        return value
    if normalized == "mw":
        return value * 1000.0
    return value / 1000.0


def recorder_history(cfg: EnergyPilotConfig, start: datetime, end: datetime) -> tuple[dict, Optional[str]]:
    """Return time-weighted 15-minute measurements from Home Assistant Recorder."""
    if not SUPERVISOR_TOKEN:
        return {}, "Supervisor token unavailable"
    entities = {
        cfg.power_connector.pv_power_entity: "pv_kw",
        cfg.power_connector.load_power_entity: "load_kw",
        cfg.power_connector.grid_power_entity: "grid_kw",
        cfg.battery_connector.power_entity: "battery_kw",
        cfg.battery_connector.soc_entity: "soc_percent",
    }
    entities = {entity_id: key for entity_id, key in entities.items() if entity_id}
    if not entities:
        return {}, "No measurement entities configured"
    query = urlencode({
        "filter_entity_id": ",".join(entities),
        "end_time": end.isoformat(),
        "minimal_response": "",
    })
    url = f"{HA_API}/history/period/{quote(start.isoformat(), safe='')}?{query}"
    request = Request(
        url,
        headers={"Authorization": f"Bearer {SUPERVISOR_TOKEN}", "Content-Type": "application/json"},
    )
    try:
        with urlopen(request, timeout=15) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (HTTPError, URLError, TimeoutError, ValueError) as exc:
        return {}, str(exc)
    if not isinstance(payload, list):
        return {}, "Recorder returned an unexpected response"
    aggregated: dict = {}
    for series in payload:
        if not isinstance(series, list) or not series:
            continue
        entity_id = next((str(row.get("entity_id")) for row in series if row.get("entity_id")), "")
        metric_key = entities.get(entity_id)
        if not metric_key:
            continue
        unit = next((
            str((row.get("attributes") or {}).get("unit_of_measurement") or "")
            for row in series if (row.get("attributes") or {}).get("unit_of_measurement")
        ), "%" if metric_key == "soc_percent" else "W")
        points = []
        for row in series:
            stamp = parse_time(row.get("last_updated") or row.get("last_changed"))
            if not stamp:
                continue
            value = (
                power_history_value(row.get("state"), unit)
                if metric_key != "soc_percent"
                else price_number(row.get("state"))
            )
            if value is not None:
                points.append((max(start, stamp.astimezone(timezone.utc)), value))
        points.sort(key=lambda pair: pair[0])
        for index, (point_start, value) in enumerate(points):
            point_end = min(end, points[index + 1][0] if index + 1 < len(points) else end)
            if point_end <= point_start:
                continue
            cursor = history_bucket(point_start)
            while cursor < point_end:
                overlap_start = max(point_start, cursor)
                overlap_end = min(point_end, cursor + timedelta(minutes=15))
                seconds = max(0.0, (overlap_end - overlap_start).total_seconds())
                if seconds > 0:
                    bucket_data = aggregated.setdefault(cursor.isoformat(), {})
                    target_key = metric_key
                    target_value = value
                    if metric_key == "grid_kw":
                        for grid_key, grid_value in (
                            ("grid_import_kw", max(0.0, value)),
                            ("grid_export_kw", max(0.0, -value)),
                        ):
                            metric = bucket_data.setdefault(grid_key, {"weighted_sum": 0.0, "weight_seconds": 0.0})
                            metric["weighted_sum"] += grid_value * seconds
                            metric["weight_seconds"] += seconds
                    else:
                        metric = bucket_data.setdefault(target_key, {"weighted_sum": 0.0, "weight_seconds": 0.0})
                        metric["weighted_sum"] += target_value * seconds
                        metric["weight_seconds"] += seconds
                cursor += timedelta(minutes=15)
    return aggregated, None


def backfill_measurement_history(cfg: EnergyPilotConfig, data: dict, observed: datetime) -> bool:
    """Refresh today's elapsed slots from Recorder at most once every 15 minutes."""
    monotonic_now = time.monotonic()
    previous_backfill = float(HISTORY_CACHE.get("last_backfill_monotonic") or 0.0)
    if previous_backfill and monotonic_now - previous_backfill < 900:
        return False
    HISTORY_CACHE["last_backfill_monotonic"] = monotonic_now
    local_zone = ZoneInfo(cfg.site.timezone)
    local_now = observed.astimezone(local_zone)
    local_start = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
    start = local_start.astimezone(timezone.utc)
    recorder, error = recorder_history(cfg, start, observed)
    data["recorder"] = {
        "last_attempt_at": observed.isoformat(),
        "error": error,
        "available": not bool(error),
    }
    if error:
        return True
    for bucket_key, metrics in recorder.items():
        bucket = parse_time(bucket_key)
        if not bucket:
            continue
        slot = history_slot(data, bucket)
        for metric_key, metric in metrics.items():
            replace_history_metric(
                slot, metric_key,
                float(metric.get("weighted_sum") or 0.0),
                float(metric.get("weight_seconds") or 0.0),
                "home_assistant_recorder",
            )
        slot["last_observed_at"] = observed.isoformat()
    return True


def record_live_measurement(data: dict, snapshot: dict) -> bool:
    """Persist a lightweight fallback sample so history survives without Recorder."""
    observed = parse_time(snapshot.get("observed_at")) or datetime.now(timezone.utc)
    previous = parse_time(data.get("last_live_sample_at"))
    if previous and (observed - previous).total_seconds() < 60:
        return False
    pv_kw = snapshot.get("pv", {}).get("power_kw", {}).get("value")
    load_kw = snapshot.get("load", {}).get("power_kw", {}).get("value")
    grid_kw = snapshot.get("grid", {}).get("power_kw", {}).get("value")
    battery_kw = snapshot.get("battery", {}).get("power_kw", {}).get("value")
    soc = snapshot.get("battery", {}).get("soc_pct", {}).get("value")
    measurements = (
        ("pv_kw", pv_kw), ("load_kw", load_kw), ("battery_kw", battery_kw),
        ("soc_percent", soc),
        ("grid_import_kw", max(0.0, grid_kw) if grid_kw is not None else None),
        ("grid_export_kw", max(0.0, -grid_kw) if grid_kw is not None else None),
    )
    if not any(value is not None for _, value in measurements):
        return False
    slot = history_slot(data, history_bucket(observed))
    for key, value in measurements:
        history_metric(slot, key, value, 60.0, "energy_pilot_storage")
    slot["sample_count"] = int(slot.get("sample_count") or 0) + 1
    slot["last_observed_at"] = observed.isoformat()
    data["last_live_sample_at"] = observed.isoformat()
    return True


def public_measurement_history(cfg: EnergyPilotConfig, data: dict, observed: datetime) -> list[dict]:
    local_zone = ZoneInfo(cfg.site.timezone)
    local_now = observed.astimezone(local_zone)
    local_start = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
    start = local_start.astimezone(timezone.utc)
    end = (local_start + timedelta(days=1)).astimezone(timezone.utc)
    result = []
    for slot in data.get("slots", {}).values():
        stamp = parse_time(slot.get("start"))
        if not stamp or stamp < start or stamp >= end or stamp > observed or not slot.get("metrics"):
            continue
        pv_kw = history_metric_value(slot, "pv_kw")
        load_kw = history_metric_value(slot, "load_kw")
        battery_kw = history_metric_value(slot, "battery_kw")
        grid_import_kw = history_metric_value(slot, "grid_import_kw")
        grid_export_kw = history_metric_value(slot, "grid_export_kw")
        grid_kw = (
            (grid_import_kw or 0.0) - (grid_export_kw or 0.0)
            if grid_import_kw is not None or grid_export_kw is not None else None
        )
        inferred_load = (
            max(0.0, pv_kw + battery_kw + grid_kw)
            if pv_kw is not None and battery_kw is not None and grid_kw is not None else None
        )
        load_derived = bool(inferred_load is not None and inferred_load > 0.05 and (load_kw is None or abs(load_kw) <= 0.02))
        if load_derived:
            load_kw = inferred_load
        weights = [
            float(metric.get("weight_seconds") or 0.0)
            for metric in slot.get("metrics", {}).values()
        ]
        source_values = set(slot.get("sources", {}).values())
        result.append({
            "start": slot.get("start"),
            "end": slot.get("end"),
            "actual": True,
            "pv_actual_kw": round(pv_kw, 3) if pv_kw is not None else None,
            "load_actual_kw": round(load_kw, 3) if load_kw is not None else None,
            "load_derived": load_derived,
            "soc_actual_percent": round(history_metric_value(slot, "soc_percent"), 1) if history_metric_value(slot, "soc_percent") is not None else None,
            "grid_import_actual_kw": round(grid_import_kw, 3) if grid_import_kw is not None else None,
            "grid_export_actual_kw": round(grid_export_kw, 3) if grid_export_kw is not None else None,
            "coverage_percent": round(min(100.0, max(weights, default=0.0) / 900.0 * 100.0), 1),
            "sample_count": int(slot.get("sample_count") or 0),
            "source": "home_assistant_recorder" if "home_assistant_recorder" in source_values else "energy_pilot_storage",
        })
    result.sort(key=lambda slot: slot["start"])
    return result


def measurement_history(cfg: EnergyPilotConfig, snapshot: dict) -> dict:
    observed = parse_time(snapshot.get("observed_at")) or datetime.now(timezone.utc)
    with HISTORY_LOCK:
        data = load_measurement_history()
        changed = backfill_measurement_history(cfg, data, observed)
        changed = record_live_measurement(data, snapshot) or changed
        cutoff = observed - timedelta(days=30)
        data["slots"] = {
            key: slot for key, slot in data.get("slots", {}).items()
            if (parse_time(slot.get("start")) or observed) >= cutoff
        }
        previous_save = float(HISTORY_CACHE.get("last_saved_monotonic") or 0.0)
        if changed and (
            not HISTORY_FILE.exists()
            or not previous_save
            or time.monotonic() - previous_save >= 30
        ):
            try:
                atomic_write(HISTORY_FILE, data)
                HISTORY_CACHE["last_saved_monotonic"] = time.monotonic()
            except OSError:
                pass
        return {
            "slots": public_measurement_history(cfg, data, observed),
            "retention_days": 30,
            "recorder": data.get("recorder", {}),
            "storage_file": HISTORY_FILE.name,
        }


def load_insights_ledger() -> dict:
    if INSIGHTS_CACHE["loaded"]:
        return INSIGHTS_CACHE["data"]
    try:
        raw = json.loads(INSIGHTS_FILE.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            raw.setdefault("version", 1)
            raw.setdefault("tracking_since", datetime.now(timezone.utc).isoformat())
            raw.setdefault("last_observed_at", None)
            raw.setdefault("slots", {})
            INSIGHTS_CACHE.update({"loaded": True, "data": raw})
            return raw
    except (OSError, ValueError, TypeError):
        pass
    now = datetime.now(timezone.utc).isoformat()
    data = {"version": 1, "tracking_since": now, "last_observed_at": None, "slots": {}}
    INSIGHTS_CACHE.update({"loaded": True, "data": data})
    return data


def insight_slot(data: dict, bucket: datetime) -> dict:
    key = bucket.isoformat()
    return data.setdefault("slots", {}).setdefault(key, {
        "start": key,
        "end": (bucket + timedelta(minutes=15)).isoformat(),
        "pv_kwh": 0.0,
        "load_kwh": 0.0,
        "solar_self_consumed_kwh": 0.0,
        "grid_import_kwh": 0.0,
        "grid_export_kwh": 0.0,
        "battery_throughput_kwh": 0.0,
        "import_cost_cents": 0.0,
        "export_revenue_cents": 0.0,
        "battery_wear_cents": 0.0,
        "negative_price_protection_minutes": 0.0,
        "coverage_seconds": 0.0,
    })


def record_insights(cfg: EnergyPilotConfig, snapshot: dict, price: dict) -> None:
    """Accumulate a durable 15-minute owner ledger from live measurements."""
    observed = parse_time(snapshot.get("observed_at")) or datetime.now(timezone.utc)
    with INSIGHTS_LOCK:
        data = load_insights_ledger()
        previous = parse_time(data.get("last_observed_at"))
        if not previous:
            data["last_observed_at"] = observed.isoformat()
            try:
                atomic_write(INSIGHTS_FILE, data)
                INSIGHTS_CACHE["last_saved_monotonic"] = time.monotonic()
            except OSError:
                pass
            return
        elapsed_seconds = max(0.0, min(60.0, (observed - previous).total_seconds()))
        if elapsed_seconds <= 0:
            return
        hours = elapsed_seconds / 3600.0
        pv_kw = snapshot.get("pv", {}).get("power_kw", {}).get("value")
        load_kw = snapshot.get("load", {}).get("power_kw", {}).get("value")
        grid_kw = snapshot.get("grid", {}).get("power_kw", {}).get("value")
        battery_kw = snapshot.get("battery", {}).get("power_kw", {}).get("value")
        routes = snapshot.get("flow", {}).get("routes") or []
        solar_home_kw = next((
            float(route.get("power_kw") or 0.0)
            for route in routes
            if route.get("from") == "pv" and route.get("to") == "load"
        ), None)
        if solar_home_kw is None and pv_kw is not None and load_kw is not None:
            solar_home_kw = min(max(0.0, float(pv_kw)), max(0.0, float(load_kw)))
        slot = insight_slot(data, history_bucket(observed))
        additions = {
            "pv_kwh": max(0.0, float(pv_kw or 0.0)) * hours,
            "load_kwh": max(0.0, float(load_kw or 0.0)) * hours,
            "solar_self_consumed_kwh": max(0.0, float(solar_home_kw or 0.0)) * hours,
            "grid_import_kwh": max(0.0, float(grid_kw or 0.0)) * hours,
            "grid_export_kwh": max(0.0, -float(grid_kw or 0.0)) * hours,
            "battery_throughput_kwh": abs(float(battery_kw or 0.0)) * hours,
        }
        for key, value in additions.items():
            slot[key] = float(slot.get(key) or 0.0) + value
        import_price = price.get("import_cents_kwh")
        export_price = price.get("export_cents_kwh")
        if import_price is not None:
            slot["import_cost_cents"] = float(slot.get("import_cost_cents") or 0.0) + additions["grid_import_kwh"] * float(import_price)
        if export_price is not None:
            slot["export_revenue_cents"] = float(slot.get("export_revenue_cents") or 0.0) + additions["grid_export_kwh"] * float(export_price)
        wear_rate = float(battery_wear_model(cfg, snapshot).get("effective_rate_cents_kwh") or 0.0)
        slot["battery_wear_cents"] = float(slot.get("battery_wear_cents") or 0.0) + additions["battery_throughput_kwh"] * wear_rate
        current_slot = next((item for item in price.get("slots") or [] if item.get("is_current")), None)
        if current_slot and current_slot.get("action") == "LIMIT EXPORT":
            slot["negative_price_protection_minutes"] = float(slot.get("negative_price_protection_minutes") or 0.0) + elapsed_seconds / 60.0
        slot["coverage_seconds"] = min(900.0, float(slot.get("coverage_seconds") or 0.0) + elapsed_seconds)
        slot["last_observed_at"] = observed.isoformat()
        data["last_observed_at"] = observed.isoformat()
        if not INSIGHTS_FILE.exists() or time.monotonic() - float(INSIGHTS_CACHE["last_saved_monotonic"] or 0.0) >= 30:
            try:
                atomic_write(INSIGHTS_FILE, data)
                INSIGHTS_CACHE["last_saved_monotonic"] = time.monotonic()
            except OSError:
                pass


def insight_period_bounds(cfg: EnergyPilotConfig, period: str, start: Optional[str], end: Optional[str], data: dict) -> tuple[datetime, datetime]:
    zone = ZoneInfo(cfg.site.timezone)
    now = datetime.now(zone)
    if period == "week":
        local_start = (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
        local_end = local_start + timedelta(days=7)
    elif period == "year":
        local_start = now.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
        local_end = local_start.replace(year=local_start.year + 1)
    elif period == "lifetime":
        first = parse_time(data.get("tracking_since")) or now.astimezone(timezone.utc)
        return first.astimezone(timezone.utc), (now + timedelta(seconds=1)).astimezone(timezone.utc)
    elif period == "custom":
        try:
            local_start = datetime.fromisoformat(str(start)).replace(tzinfo=zone)
            local_end = datetime.fromisoformat(str(end)).replace(tzinfo=zone) + timedelta(days=1)
        except (TypeError, ValueError):
            raise HTTPException(422, "Custom period requires valid start and end dates.")
        if local_end <= local_start:
            raise HTTPException(422, "Custom period end must be on or after its start.")
    else:
        local_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        local_end = (local_start.replace(day=28) + timedelta(days=4)).replace(day=1)
    return local_start.astimezone(timezone.utc), local_end.astimezone(timezone.utc)


def insights_summary(cfg: EnergyPilotConfig, period: str = "month", start: Optional[str] = None, end: Optional[str] = None) -> dict:
    with INSIGHTS_LOCK:
        data = load_insights_ledger()
        period_start, period_end = insight_period_bounds(cfg, period, start, end, data)
        selected = [
            slot for slot in data.get("slots", {}).values()
            if period_start <= (parse_time(slot.get("start")) or period_end) < period_end
        ]
    metric_keys = (
        "pv_kwh", "load_kwh", "solar_self_consumed_kwh", "grid_import_kwh",
        "grid_export_kwh", "battery_throughput_kwh", "import_cost_cents",
        "export_revenue_cents", "battery_wear_cents", "negative_price_protection_minutes",
    )
    totals = {key: sum(float(slot.get(key) or 0.0) for slot in selected) for key in metric_keys}
    bill_result = totals["export_revenue_cents"] - totals["import_cost_cents"]
    after_wear = bill_result - totals["battery_wear_cents"]
    zone = ZoneInfo(cfg.site.timezone)
    span_days = max(1.0, (period_end - period_start).total_seconds() / 86400.0)
    group_monthly = span_days > 62
    grouped: dict[str, dict] = {}
    for slot in selected:
        local = (parse_time(slot.get("start")) or period_start).astimezone(zone)
        key = local.strftime("%Y-%m" if group_monthly else "%Y-%m-%d")
        row = grouped.setdefault(key, {"period": key})
        for metric in metric_keys:
            row[metric] = float(row.get(metric) or 0.0) + float(slot.get(metric) or 0.0)
    series = []
    for key in sorted(grouped):
        row = grouped[key]
        row["bill_result_cents"] = row.get("export_revenue_cents", 0.0) - row.get("import_cost_cents", 0.0)
        row["result_after_wear_cents"] = row["bill_result_cents"] - row.get("battery_wear_cents", 0.0)
        series.append({name: round(value, 4) if isinstance(value, float) else value for name, value in row.items()})
    lifetime_slots = list(data.get("slots", {}).values())
    lifetime_import = sum(float(slot.get("import_cost_cents") or 0.0) for slot in lifetime_slots)
    lifetime_export = sum(float(slot.get("export_revenue_cents") or 0.0) for slot in lifetime_slots)
    tracking_since = data.get("tracking_since")
    return {
        "period": period,
        "period_start": period_start.isoformat(),
        "period_end": period_end.isoformat(),
        "tracking_since": tracking_since,
        "aggregation": "month" if group_monthly else "day",
        "totals": {
            **{key: round(value, 4) for key, value in totals.items()},
            "bill_result_cents": round(bill_result, 2),
            "result_after_wear_cents": round(after_wear, 2),
            "self_sufficiency_percent": round(max(0.0, min(100.0, (1.0 - totals["grid_import_kwh"] / totals["load_kwh"]) * 100.0)), 1) if totals["load_kwh"] > 0 else None,
            "solar_self_consumption_percent": round(totals["solar_self_consumed_kwh"] / totals["pv_kwh"] * 100.0, 1) if totals["pv_kwh"] > 0 else None,
        },
        "series": series,
        "lifetime": {
            "bill_result_cents": round(lifetime_export - lifetime_import, 2),
            "import_cost_cents": round(lifetime_import, 2),
            "export_revenue_cents": round(lifetime_export, 2),
            "slot_count": len(lifetime_slots),
        },
        "statement": {
            "import_cost_cents": round(totals["import_cost_cents"], 2),
            "export_revenue_cents": round(totals["export_revenue_cents"], 2),
            "bill_result_cents": round(bill_result, 2),
            "battery_wear_cents_excluded": round(totals["battery_wear_cents"], 2),
            "note": "Estimated energy statement. Battery wear is excluded from the bill result.",
        },
        "data_quality": {
            "slot_count": len(selected),
            "coverage_hours": round(sum(float(slot.get("coverage_seconds") or 0.0) for slot in selected) / 3600.0, 2),
            "storage_file": INSIGHTS_FILE.name,
        },
    }

def nordpool_entity_vat_percent(entity_id: str) -> Optional[float]:
    """Infer the HACS Nord Pool VAT setting encoded at the end of its entity id."""
    match = re.search(r"_0(\d{1,2})$", str(entity_id or "").lower())
    if not match:
        return None
    digits = match.group(1)
    return float(int(digits) * (10 if len(digits) == 1 else 1))


def apply_detected_price_vat(raw: dict) -> bool:
    """Apply HACS Nord Pool VAT metadata once during configuration migration."""
    connector = raw.setdefault("price_connector", PriceConnectorConfig().model_dump())
    inferred = nordpool_entity_vat_percent(connector.get("entity", ""))
    if inferred is None or inferred <= 0 or connector.get("source_includes_vat"):
        return False
    connector["source_includes_vat"] = True
    connector["vat_percent"] = inferred
    return True


def migrate_legacy(raw: dict) -> dict:
    version = raw.get("version")
    raw.pop("_meta", None)
    if version == 15:
        raw.setdefault("revision", 1)
        return raw
    if version == 14:
        raw["version"] = 15
        return raw
    if version == 13:
        raw["version"] = 15
        qilowatt = raw.setdefault("qilowatt", QilowattConfig().model_dump())
        if qilowatt.get("mode") == "physical_monitor":
            qilowatt["mode"] = "disabled"
        return raw
    if version == 12:
        raw["version"] = 15
        raw.setdefault("qilowatt", QilowattConfig().model_dump())
        return raw
    if version == 11:
        raw["version"] = 12
        # Existing installations were already configured before onboarding existed.
        raw.setdefault("setup", {
            "completed": True,
            "completed_at": now(),
            "auto_discovery": True,
        })
        return raw
    if version == 10:
        raw["version"] = 12
        raw.setdefault("setup", {
            "completed": True,
            "completed_at": now(),
            "auto_discovery": True,
        })
        return raw
    if version == 9:
        raw["version"] = 10
        raw.setdefault("battery_connector", BatteryConnectorConfig().model_dump()).setdefault("temperature_entity", "")
        raw.setdefault("power_connector", PowerConnectorConfig().model_dump()).setdefault("inverter_temperature_entity", "")
        return raw
    if version == 8:
        raw["version"] = 9
        battery_policy = raw.setdefault("battery_policy", BatteryPolicyConfig().model_dump())
        policy_defaults = BatteryPolicyConfig().model_dump()
        for key in ("system_cost_eur", "warranted_cycles"):
            battery_policy.setdefault(key, policy_defaults[key])
        battery_connector = raw.setdefault("battery_connector", BatteryConnectorConfig().model_dump())
        battery_connector.setdefault("cycle_count_entity", "")
        return raw
    if version == 7:
        raw["version"] = 8
        forecast = raw.setdefault("forecast_connector", ForecastConnectorConfig().model_dump())
        defaults = ForecastConnectorConfig().model_dump()
        for key in ("weather_entity", "solar_peak_kw", "solar_tilt_degrees", "solar_azimuth_degrees"):
            forecast.setdefault(key, defaults[key])
        return raw
    if version == 6:
        raw["version"] = 7
        raw.setdefault("forecast_connector", ForecastConnectorConfig().model_dump())
        battery_policy = raw.setdefault("battery_policy", BatteryPolicyConfig().model_dump())
        defaults = BatteryPolicyConfig().model_dump()
        for key in ("max_charge_kw", "max_discharge_kw", "roundtrip_efficiency_percent", "degradation_cost_cents_kwh"):
            battery_policy.setdefault(key, defaults[key])
        return raw
    if version == 5:
        tariff = raw.setdefault("energy_tariff", EnergyTariffConfig().model_dump())
        legacy_balancing = tariff.pop("balancing_cost_cents_kwh", 0.373)
        tariff.setdefault("import_balancing_cost_cents_kwh", legacy_balancing)
        tariff.setdefault("export_balancing_cost_cents_kwh", -abs(legacy_balancing))
        raw["version"] = 7
        raw.setdefault("forecast_connector", ForecastConnectorConfig().model_dump())
        return raw
    if version == 4:
        raw["version"] = 7
        raw.setdefault("energy_tariff", EnergyTariffConfig().model_dump())
        raw.setdefault("forecast_connector", ForecastConnectorConfig().model_dump())
        return raw
    if version in (2, 3):
        raw["version"] = 7
        raw.setdefault("power_connector", PowerConnectorConfig().model_dump())
        raw.setdefault("price_connector", PriceConnectorConfig().model_dump())
        raw.setdefault("energy_tariff", EnergyTariffConfig().model_dump())
        raw.setdefault("forecast_connector", ForecastConnectorConfig().model_dump())
        # Previous releases defaulted to EUR/kWh. Auto is safer because Nord Pool
        # integrations expose either EUR/kWh, c/kWh or EUR/MWh depending on setup.
        raw["price_connector"].setdefault("price_unit", "auto")
        return raw
    battery = raw.get("battery", {})
    grid = raw.get("grid", {})
    return {
        "version": 7,
        "revision": 1,
        "site": raw.get("site", {}),
        "planning": raw.get("planning", {}),
        "grid_policy": {
            "max_import_kw": grid.get("max_import_kw", 17.0),
            "max_export_kw": grid.get("max_export_kw", 15.0),
        },
        "battery_policy": {
            "min_operational_soc_percent": battery.get("min_soc_percent", 15.0),
            "reserve_soc_percent": battery.get("min_soc_percent", 15.0),
            "max_planned_soc_percent": battery.get("max_soc_percent", 100.0),
        },
        "battery_connector": BatteryConnectorConfig().model_dump(),
        "power_connector": PowerConnectorConfig().model_dump(),
        "forecast_connector": ForecastConnectorConfig().model_dump(),
        "price_connector": PriceConnectorConfig().model_dump(),
        "energy_tariff": EnergyTariffConfig().model_dump(),
        "runtime": raw.get("runtime", {"mode": "simulation"}),
    }

def load_config():
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    if not CONFIG_FILE.exists():
        cfg = EnergyPilotConfig()
        save_config(cfg)
        return cfg
    try:
        raw = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        stored_version = raw.get("version")
        migrated = migrate_legacy(raw)
        migrated["version"] = 15
        price_vat_detected = apply_detected_price_vat(migrated)
        cfg = EnergyPilotConfig.model_validate(migrated).validate_cross_fields()
        if stored_version != 15 or price_vat_detected:
            save_config(cfg)
        return cfg
    except Exception as exc:
        raise HTTPException(500, f"Stored configuration is invalid: {exc}")

def save_config(cfg: EnergyPilotConfig, *, bump_revision: bool = False) -> EnergyPilotConfig:
    cfg.validate_cross_fields()
    if bump_revision:
        cfg = cfg.model_copy(update={"revision": max(1, cfg.revision + 1)})
    payload = cfg.model_dump(mode="json")
    payload["_meta"] = {
        "saved_at": now(),
        "app_version": VERSION,
        "config_revision": cfg.revision,
    }
    atomic_write(CONFIG_FILE, payload)
    return cfg

def ha_state(entity_id: str):
    if not SUPERVISOR_TOKEN:
        return {"available": False, "entity_id": entity_id, "error": "Supervisor token unavailable"}
    req = Request(
        f"{HA_API}/states/{entity_id}",
        headers={"Authorization": f"Bearer {SUPERVISOR_TOKEN}", "Content-Type": "application/json"},
    )
    try:
        with urlopen(req, timeout=4) as response:
            data = json.loads(response.read().decode("utf-8"))
            return {
                "available": data.get("state") not in (None, "unknown", "unavailable"),
                "entity_id": entity_id,
                "state": data.get("state"),
                "attributes": data.get("attributes", {}),
                "last_updated": data.get("last_updated"),
                "last_reported": data.get("last_reported") or data.get("last_updated"),
            }
    except HTTPError as exc:
        return {"available": False, "entity_id": entity_id, "error": f"HTTP {exc.code}"}
    except (URLError, TimeoutError, ValueError) as exc:
        return {"available": False, "entity_id": entity_id, "error": str(exc)}

def ha_states(force: bool = False):
    """Read all HA states once for connector discovery and diagnostics."""
    age = time.monotonic() - float(HA_STATES_CACHE.get("fetched_monotonic") or 0.0)
    if not force and age < 5 and HA_STATES_CACHE.get("result"):
        return HA_STATES_CACHE["result"]
    if not SUPERVISOR_TOKEN:
        return []
    req = Request(
        f"{HA_API}/states",
        headers={"Authorization": f"Bearer {SUPERVISOR_TOKEN}", "Content-Type": "application/json"},
    )
    try:
        with urlopen(req, timeout=6) as response:
            data = json.loads(response.read().decode("utf-8"))
            result = data if isinstance(data, list) else []
            HA_STATES_CACHE.update({"fetched_monotonic": time.monotonic(), "result": result})
            return result
    except (HTTPError, URLError, TimeoutError, ValueError):
        return []

def ha_core_config() -> tuple[Optional[dict], Optional[str]]:
    """Read Home Assistant location metadata used by the solar geometry model."""
    age = time.monotonic() - float(HA_CONFIG_CACHE.get("fetched_monotonic") or 0.0)
    if age < 3600 and HA_CONFIG_CACHE.get("result") is not None:
        return HA_CONFIG_CACHE["result"], None
    if not SUPERVISOR_TOKEN:
        return None, "Supervisor token unavailable"
    req = Request(
        f"{HA_API}/config",
        headers={"Authorization": f"Bearer {SUPERVISOR_TOKEN}", "Content-Type": "application/json"},
    )
    try:
        with urlopen(req, timeout=6) as response:
            result = json.loads(response.read().decode("utf-8"))
        HA_CONFIG_CACHE.update({"fetched_monotonic": time.monotonic(), "result": result})
        return result, None
    except (HTTPError, URLError, TimeoutError, ValueError) as exc:
        return None, str(exc)


def weather_entities(configured_entity: str = "") -> list[dict]:
    """Return configured or automatically ranked Home Assistant weather entities."""
    candidates = []
    for raw in ha_states():
        entity_id = str(raw.get("entity_id") or "")
        if not entity_id.startswith("weather."):
            continue
        attrs = raw.get("attributes") or {}
        score = 100 if configured_entity and entity_id == configured_entity else 0
        lowered = f"{entity_id} {attrs.get('friendly_name', '')}".lower()
        if "home" in lowered:
            score += 8
        if "forecast" in lowered:
            score += 5
        if attrs.get("cloud_coverage") is not None:
            score += 3
        if int(attrs.get("supported_features") or 0) & 2:
            score += 10
        candidates.append((score, {"entity_id": entity_id, "state": raw.get("state"), "attributes": attrs}))
    candidates.sort(key=lambda pair: pair[0], reverse=True)
    return [item for _, item in candidates]


def ha_hourly_weather_forecast(configured_entity: str = "") -> tuple[Optional[dict], Optional[str]]:
    """Fetch hourly forecasts from the first compatible HA weather entity."""
    age = time.monotonic() - float(WEATHER_FORECAST_CACHE.get("fetched_monotonic") or 0.0)
    cached = WEATHER_FORECAST_CACHE.get("result")
    if age < 1800 and cached is not None and (
        not configured_entity or cached.get("entity_id") == configured_entity
    ):
        return cached, WEATHER_FORECAST_CACHE.get("error")
    if age < 300 and WEATHER_FORECAST_CACHE.get("error"):
        return None, WEATHER_FORECAST_CACHE["error"]
    if not SUPERVISOR_TOKEN:
        return None, "Supervisor token unavailable"
    candidates = weather_entities(configured_entity)
    if not candidates:
        return None, "No Home Assistant weather entity found"
    errors = []
    for candidate in candidates:
        entity_id = candidate["entity_id"]
        req = Request(
            f"{HA_API}/services/weather/get_forecasts?return_response",
            data=json.dumps({"entity_id": entity_id, "type": "hourly"}).encode("utf-8"),
            headers={"Authorization": f"Bearer {SUPERVISOR_TOKEN}", "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlopen(req, timeout=10) as response:
                payload = json.loads(response.read().decode("utf-8"))
            service_response = payload.get("service_response", payload) if isinstance(payload, dict) else {}
            entity_result = service_response.get(entity_id, {}) if isinstance(service_response, dict) else {}
            forecast = entity_result.get("forecast") if isinstance(entity_result, dict) else None
            if isinstance(forecast, list) and forecast:
                result = {
                    "entity_id": entity_id,
                    "forecast": forecast,
                    "current_condition": candidate.get("state"),
                    "current_cloud_coverage": candidate.get("attributes", {}).get("cloud_coverage"),
                }
                WEATHER_FORECAST_CACHE.update({
                    "fetched_monotonic": time.monotonic(), "result": result, "error": None,
                })
                return result, None
            errors.append(f"{entity_id}: hourly forecast unavailable")
        except (HTTPError, URLError, TimeoutError, ValueError) as exc:
            errors.append(f"{entity_id}: {exc}")
    error = "; ".join(errors[:3])
    WEATHER_FORECAST_CACHE.update({
        "fetched_monotonic": time.monotonic(), "result": None, "error": error,
    })
    return None, error


def solar_geometry(at: datetime, latitude: float, longitude: float, tilt: float, panel_azimuth: float) -> tuple[float, float, float]:
    """Return solar elevation, azimuth and panel incidence cosine."""
    utc = at.astimezone(timezone.utc)
    day = utc.timetuple().tm_yday
    hour = utc.hour + utc.minute / 60.0 + utc.second / 3600.0
    gamma = 2.0 * math.pi / 365.0 * (day - 1 + (hour - 12.0) / 24.0)
    equation_of_time = 229.18 * (
        0.000075 + 0.001868 * math.cos(gamma) - 0.032077 * math.sin(gamma)
        - 0.014615 * math.cos(2 * gamma) - 0.040849 * math.sin(2 * gamma)
    )
    declination = (
        0.006918 - 0.399912 * math.cos(gamma) + 0.070257 * math.sin(gamma)
        - 0.006758 * math.cos(2 * gamma) + 0.000907 * math.sin(2 * gamma)
        - 0.002697 * math.cos(3 * gamma) + 0.00148 * math.sin(3 * gamma)
    )
    solar_minutes = (hour * 60.0 + equation_of_time + 4.0 * longitude) % 1440.0
    hour_angle = math.radians(solar_minutes / 4.0 - 180.0)
    latitude_rad = math.radians(latitude)
    cos_zenith = max(-1.0, min(1.0,
        math.sin(latitude_rad) * math.sin(declination)
        + math.cos(latitude_rad) * math.cos(declination) * math.cos(hour_angle)
    ))
    elevation = math.asin(cos_zenith)
    azimuth = (
        math.atan2(
            math.sin(hour_angle),
            math.cos(hour_angle) * math.sin(latitude_rad) - math.tan(declination) * math.cos(latitude_rad),
        )
        + math.pi
    ) % (2.0 * math.pi)
    tilt_rad = math.radians(tilt)
    panel_azimuth_rad = math.radians(panel_azimuth)
    incidence = (
        math.sin(elevation) * math.cos(tilt_rad)
        + math.cos(elevation) * math.sin(tilt_rad) * math.cos(azimuth - panel_azimuth_rad)
    )
    return math.degrees(elevation), math.degrees(azimuth), max(0.0, incidence)


def weather_factor(row: dict) -> tuple[float, Optional[float]]:
    """Convert cloud cover and condition into a conservative PV attenuation factor."""
    cloud = price_number(row.get("cloud_coverage"))
    condition = str(row.get("condition") or "").lower()
    condition_factor = {
        "sunny": 1.0, "clear-night": 0.0, "partlycloudy": 0.72,
        "cloudy": 0.34, "fog": 0.22, "rainy": 0.24,
        "pouring": 0.12, "snowy": 0.28, "snowy-rainy": 0.20,
        "lightning": 0.18, "lightning-rainy": 0.12,
    }.get(condition, 0.75)
    if cloud is None:
        factor = condition_factor
    else:
        cloud = max(0.0, min(100.0, cloud))
        factor = 1.0 - 0.78 * (cloud / 100.0) ** 2.2
        factor = min(factor, condition_factor + 0.18)
    precipitation_probability = price_number(row.get("precipitation_probability"))
    if precipitation_probability is not None:
        factor *= 1.0 - 0.18 * max(0.0, min(100.0, precipitation_probability)) / 100.0
    return max(0.05, min(1.0, factor)), cloud


def weather_adjusted_solar_points(cfg: EnergyPilotConfig, slots: list[dict]) -> tuple[list[Optional[float]], dict]:
    """Build a 15-minute PV curve from HA location, panel geometry and hourly weather."""
    fc = cfg.forecast_connector
    weather, weather_error = ha_hourly_weather_forecast(fc.weather_entity)
    location, location_error = ha_core_config()
    if not weather or not location:
        return [None] * len(slots), {
            "available": False, "mode": "weather_adjusted",
            "weather_entity": weather.get("entity_id") if weather else fc.weather_entity,
            "error": weather_error or location_error or "Weather or Home Assistant location unavailable",
        }
    latitude = price_number(location.get("latitude"))
    longitude = price_number(location.get("longitude"))
    if latitude is None or longitude is None:
        return [None] * len(slots), {
            "available": False, "mode": "weather_adjusted",
            "weather_entity": weather.get("entity_id"), "error": "Home Assistant latitude or longitude unavailable",
        }
    hourly = []
    for row in weather.get("forecast", []):
        stamp = parse_time(row.get("datetime"))
        if stamp:
            hourly.append((stamp, row))
    hourly.sort(key=lambda pair: pair[0])
    values: list[Optional[float]] = []
    cloud_values = []
    for slot in slots:
        start = parse_time(slot.get("start"))
        if not start or not hourly:
            values.append(None)
            continue
        nearest = min(hourly, key=lambda pair: abs((pair[0] - start).total_seconds()))
        if abs((nearest[0] - start).total_seconds()) > 5400:
            values.append(None)
            continue
        elevation, _, incidence = solar_geometry(
            start + timedelta(minutes=cfg.planning.slot_minutes / 2),
            latitude, longitude, fc.solar_tilt_degrees, fc.solar_azimuth_degrees,
        )
        if elevation <= 0:
            values.append(0.0)
            continue
        attenuation, cloud = weather_factor(nearest[1])
        if cloud is not None:
            cloud_values.append(cloud)
        atmospheric = 0.72 + 0.23 * math.sqrt(max(0.0, math.sin(math.radians(elevation))))
        power = fc.solar_peak_kw * incidence * atmospheric * attenuation
        values.append(round(max(0.0, min(fc.solar_peak_kw, power)), 3))
    available = any(value is not None for value in values)
    return values, {
        "available": available, "mode": "weather_adjusted",
        "weather_entity": weather.get("entity_id"), "points": sum(value is not None for value in values),
        "solar_peak_kw": fc.solar_peak_kw, "tilt_degrees": fc.solar_tilt_degrees,
        "azimuth_degrees": fc.solar_azimuth_degrees,
        "average_cloud_coverage": round(sum(cloud_values) / len(cloud_values), 1) if cloud_values else None,
        "error": None if available else "Hourly weather forecast contains no matching periods",
    }


def normalize_ha_item(data: dict) -> dict:
    return {
        "available": data.get("state") not in (None, "unknown", "unavailable"),
        "entity_id": data.get("entity_id"),
        "state": data.get("state"),
        "attributes": data.get("attributes", {}),
        "last_updated": data.get("last_updated"),
        "last_reported": data.get("last_reported") or data.get("last_updated"),
    }


def price_item_compatibility(item: dict) -> tuple[bool, int, bool]:
    """Check for the full HACS Nord Pool slot data required by Planner."""
    if not item.get("available"):
        return False, 0, False
    attrs = item.get("attributes") or {}
    today = attrs.get("raw_today")
    tomorrow = attrs.get("raw_tomorrow")
    if not isinstance(today, list):
        today = attrs.get("today")
    if not isinstance(tomorrow, list):
        tomorrow = attrs.get("tomorrow")
    today = today if isinstance(today, list) else []
    tomorrow_available = isinstance(tomorrow, list)
    timestamped = bool(
        today and isinstance(today[0], dict)
        and any(key in today[0] for key in ("start", "start_time", "time"))
    )
    return len(today) >= 24 and tomorrow_available, len(today), timestamped


def discover_price_item(configured_entity: str) -> tuple[dict, str, list[str]]:
    """Return configured Nord Pool sensor or the strongest compatible fallback."""
    configured = (
        ha_state(configured_entity)
        if configured_entity
        else {"available": False, "entity_id": "", "error": "No price entity configured"}
    )
    configured_compatible, _, _ = price_item_compatibility(configured)
    if configured_compatible:
        return configured, "configured", []

    candidates = []
    for raw in ha_states():
        entity_id = str(raw.get("entity_id", ""))
        if not entity_id.startswith("sensor."):
            continue
        item = normalize_ha_item(raw)
        a = item.get("attributes", {})
        compatible, _, timestamped = price_item_compatibility(item)
        if not compatible:
            continue
        score = 0
        lowered = entity_id.lower()
        if "nordpool" in lowered or "nord_pool" in lowered:
            score += 5
        if isinstance(a.get("raw_today"), list):
            score += 5
        if isinstance(a.get("raw_tomorrow"), list):
            score += 3
        if timestamped:
            score += 4
        if any(price_number(a.get(k)) is not None for k in ("current_price", "current", "price", "value")):
            score += 2
        unit = str(a.get("unit_of_measurement", "")).lower()
        if any(token in unit for token in ("kwh", "mwh", "eur", "cent", "s/kwh")):
            score += 1
        if score >= 6:
            candidates.append((score, item))
    candidates.sort(key=lambda pair: pair[0], reverse=True)
    if candidates:
        return candidates[0][1], "auto_discovered", [item.get("entity_id") for _, item in candidates[:5]]
    return configured, "configured_incompatible" if configured.get("available") else "configured_unavailable", []

def numeric_state(item):
    if not item.get("available"):
        return None
    try:
        return float(item.get("state"))
    except (TypeError, ValueError):
        return None

CONNECTOR_LABELS = {
    "pv_power": "Solar production",
    "load_power": "Home load",
    "grid_power": "Grid power",
    "battery_power": "Battery power",
    "battery_soc": "Battery state of charge",
    "battery_capacity": "Usable battery capacity",
    "battery_cycles": "Battery life cycles",
    "battery_temperature": "Battery temperature",
    "inverter_temperature": "Inverter temperature",
}

POWER_KINDS = {"pv_power", "load_power", "grid_power", "battery_power"}

def discovery_text(item: dict) -> tuple[str, set[str]]:
    attrs = item.get("attributes") or {}
    text = " ".join((
        str(item.get("entity_id") or ""),
        str(attrs.get("friendly_name") or ""),
        str(attrs.get("device_class") or ""),
    )).lower()
    return text, set(re.findall(r"[a-z0-9]+", text))

def connector_candidate_score(item: dict, kind: str) -> tuple[int, list[str]]:
    value = numeric_state(item)
    if value is None:
        return -100, ["not numeric"]
    attrs = item.get("attributes") or {}
    unit = str(attrs.get("unit_of_measurement") or "").strip().lower()
    device_class = str(attrs.get("device_class") or "").lower()
    text, tokens = discovery_text(item)
    score, reasons = 0, []

    def add(points: int, reason: str):
        nonlocal score
        score += points
        reasons.append(reason)

    power_unit = unit in {"w", "kw", "mw"} or device_class == "power"
    energy_unit = unit in {"wh", "kwh", "mwh"} or device_class == "energy"
    temperature_unit = unit in {"°c", "c", "°f", "f"} or device_class == "temperature"
    percent_unit = unit in {"%", "percent", "percentage"}

    if kind in POWER_KINDS:
        if not power_unit:
            return -100, ["not a power sensor"]
        add(8, "power unit")
        if any(token in tokens for token in ("forecast", "estimated", "limit", "max", "maximum")):
            add(-18, "forecast or limit sensor")
    if kind == "pv_power":
        if "pv" in tokens:
            add(15, "PV name")
        if any(token in tokens for token in ("solar", "photovoltaic")):
            add(13, "solar name")
        if any(token in tokens for token in ("production", "generation")):
            add(3, "production name")
        if "battery" in tokens or "grid" in tokens or "load" in tokens:
            add(-14, "different power channel")
    elif kind == "load_power":
        if "load" in tokens:
            add(15, "load name")
        if "home" in tokens or "house" in tokens:
            add(8, "home name")
        if "consumption" in tokens or "consuming" in tokens:
            add(5, "consumption name")
        if "battery" in tokens or "grid" in tokens or "pv" in tokens or "solar" in tokens:
            add(-12, "different power channel")
    elif kind == "grid_power":
        if "grid" in tokens:
            add(15, "grid name")
        if any(token in tokens for token in ("meter", "mains", "utility")):
            add(5, "utility meter name")
        if "battery" in tokens or "pv" in tokens or "solar" in tokens or "load" in tokens:
            add(-12, "different power channel")
    elif kind == "battery_power":
        if "battery" in tokens:
            add(15, "battery name")
        if "power" in tokens:
            add(5, "power name")
        if any(token in tokens for token in ("soc", "capacity", "temperature", "cycle", "voltage", "current")):
            add(-18, "different battery measurement")
    elif kind == "battery_soc":
        if not percent_unit and device_class != "battery":
            return -100, ["not a percentage battery sensor"]
        if "battery" in tokens:
            add(12, "battery name")
        if "soc" in tokens or ("state" in tokens and "charge" in tokens):
            add(16, "state of charge name")
        if percent_unit:
            add(7, "percentage unit")
        if any(token in tokens for token in ("health", "soh", "temperature", "cycle")):
            add(-18, "different battery percentage")
    elif kind == "battery_capacity":
        if not energy_unit:
            return -100, ["not an energy-capacity sensor"]
        if "battery" in tokens:
            add(12, "battery name")
        if "capacity" in tokens:
            add(16, "capacity name")
        if "usable" in tokens or "nominal" in tokens:
            add(6, "usable or nominal capacity")
        if any(token in tokens for token in ("stored", "remaining", "today", "charged", "discharged")):
            add(-16, "changing energy counter")
        add(7, "energy unit")
    elif kind == "battery_cycles":
        if "cycle" not in text:
            return -100, ["not a cycle sensor"]
        if "battery" in tokens:
            add(14, "battery name")
        if "life" in tokens:
            add(6, "life-cycle name")
        if "total" in tokens:
            add(4, "total counter")
        add(8, "cycle name")
    elif kind in {"battery_temperature", "inverter_temperature"}:
        if not temperature_unit and "temperature" not in tokens and "temp" not in tokens:
            return -100, ["not a temperature sensor"]
        target = "battery" if kind == "battery_temperature" else "inverter"
        opposite = "inverter" if target == "battery" else "battery"
        if target in tokens:
            add(18, f"{target} name")
        if "temperature" in tokens or "temp" in tokens:
            add(7, "temperature name")
        if temperature_unit:
            add(6, "temperature unit")
        if opposite in tokens:
            add(-22, f"{opposite} temperature")

    # A semantic suffix is stable even when the user renames the device prefix.
    stable_suffixes = {
        "pv_power": ("pv_power", "solar_power", "production_power"),
        "load_power": ("load_power", "home_load", "home_power"),
        "grid_power": ("grid_power", "mains_power"),
        "battery_power": ("battery_power",),
        "battery_soc": ("battery_soc", "state_of_charge"),
        "battery_capacity": ("battery_capacity", "usable_capacity"),
        "battery_cycles": ("total_battery_life_cycles", "battery_cycles"),
        "battery_temperature": ("battery_temperature", "battery_temp"),
        "inverter_temperature": ("inverter_temperature", "inverter_temp"),
    }
    entity_id = str(item.get("entity_id") or "").lower()
    if any(entity_id.endswith(suffix) for suffix in stable_suffixes.get(kind, ())):
        add(12, "stable entity suffix")
    return score, reasons

def connector_candidates(kind: str, states: Optional[list[dict]] = None) -> list[dict]:
    ranked = []
    for raw in states if states is not None else ha_states():
        entity_id = str(raw.get("entity_id") or "")
        if not entity_id.startswith("sensor."):
            continue
        item = normalize_ha_item(raw)
        score, reasons = connector_candidate_score(item, kind)
        if score < 12:
            continue
        ranked.append({
            "entity_id": entity_id,
            "friendly_name": str((item.get("attributes") or {}).get("friendly_name") or entity_id),
            "unit": str((item.get("attributes") or {}).get("unit_of_measurement") or ""),
            "state": item.get("state"),
            "score": score,
            "reasons": reasons,
            "_item": item,
        })
    ranked.sort(key=lambda candidate: (-candidate["score"], candidate["entity_id"]))
    return ranked

def discover_connector_item(
    configured_entity: str, kind: str, states: Optional[list[dict]] = None,
) -> tuple[dict, str, list[str], bool]:
    if configured_entity:
        configured = ha_state(configured_entity)
        score, _ = connector_candidate_score(configured, kind)
        if score >= 12:
            return configured, "configured", [], False
    candidates = connector_candidates(kind, states)
    if candidates:
        best = candidates[0]
        ambiguous = len(candidates) > 1 and candidates[1]["score"] >= best["score"] - 2
        return best["_item"], "auto_discovered", [
            candidate["entity_id"] for candidate in candidates[:5]
        ], ambiguous
    unavailable = ha_state(configured_entity) if configured_entity else {
        "available": False, "entity_id": "", "error": f"No compatible {CONNECTOR_LABELS[kind]} sensor found",
    }
    return unavailable, "unavailable", [], False

def discover_cycle_count_item(configured_entity: str) -> tuple[dict, str, list[str]]:
    item, mode, candidates, _ = discover_connector_item(configured_entity, "battery_cycles")
    return item, mode, candidates

def discover_temperature_item(configured_entity: str, kind: Literal["battery", "inverter"]) -> tuple[dict, str, list[str]]:
    sensor_kind = "battery_temperature" if kind == "battery" else "inverter_temperature"
    item, mode, candidates, _ = discover_connector_item(configured_entity, sensor_kind)
    return item, mode, candidates

QILOWATT_ENTITY_DOMAINS = {
    "mode": "sensor.",
    "source": "sensor.",
    "power_limit": "sensor.",
    "connected": "binary_sensor.",
}
QILOWATT_ENTITY_SUFFIXES = {
    "mode": ("qw_mode", "qilowatt_mode"),
    "source": ("qw_source", "qilowatt_source"),
    "power_limit": ("qw_powerlimit", "qw_power_limit", "qilowatt_powerlimit", "qilowatt_power_limit"),
    "connected": ("qw_connected", "qilowatt_connected"),
}
QILOWATT_ACTIONS = {
    "normal": "NORMAL",
    "savebattery": "SAVE BATTERY",
    "pvsell": "PV SELL",
    "sell": "SELL",
    "frrup": "SELL",
    "frrdown": "BUY",
    "buy": "BUY",
    "limitexport": "LIMIT EXPORT",
    "nobattery": "SAVE BATTERY",
}

def normalize_qilowatt_value(value) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").strip().lower())

def qilowatt_entity_candidates(kind: str, states: Optional[list[dict]] = None) -> list[dict]:
    domain = QILOWATT_ENTITY_DOMAINS[kind]
    suffixes = QILOWATT_ENTITY_SUFFIXES[kind]
    candidates = []
    for raw in states if states is not None else ha_states():
        entity_id = str(raw.get("entity_id") or "")
        if not entity_id.startswith(domain):
            continue
        attrs = raw.get("attributes") or {}
        text = f"{entity_id} {attrs.get('friendly_name', '')}".lower()
        normalized = normalize_qilowatt_value(text)
        score = 0
        if any(entity_id.lower().endswith(suffix) for suffix in suffixes):
            score += 30
        if "qilowatt" in normalized or "qw" in set(re.findall(r"[a-z0-9]+", text)):
            score += 12
        kind_words = {
            "mode": ("mode",),
            "source": ("source",),
            "power_limit": ("powerlimit", "power limit"),
            "connected": ("connected", "connection"),
        }[kind]
        if any(word in text or normalize_qilowatt_value(word) in normalized for word in kind_words):
            score += 12
        if score < 20:
            continue
        candidates.append({
            "entity_id": entity_id,
            "friendly_name": str(attrs.get("friendly_name") or entity_id),
            "state": raw.get("state"),
            "unit": str(attrs.get("unit_of_measurement") or ""),
            "score": score,
        })
    candidates.sort(key=lambda item: (-item["score"], item["entity_id"]))
    return candidates

def qilowatt_snapshot(cfg: EnergyPilotConfig, states: Optional[list[dict]] = None) -> dict:
    qcfg = cfg.qilowatt
    states = ha_states() if states is None else states
    configured = {
        "mode": qcfg.mode_entity,
        "source": qcfg.source_entity,
        "power_limit": qcfg.power_limit_entity,
        "connected": qcfg.connected_entity,
    }
    resolved, candidates = {}, {}
    by_id = {str(row.get("entity_id") or ""): row for row in states}
    for kind, entity_id in configured.items():
        options = qilowatt_entity_candidates(kind, states)
        candidates[kind] = options
        chosen = by_id.get(entity_id) if entity_id else None
        if chosen is None and options:
            chosen = by_id.get(options[0]["entity_id"])
        resolved[kind] = normalize_ha_item(chosen) if chosen else {
            "available": False, "entity_id": entity_id, "state": None,
        }

    mode_raw = resolved["mode"].get("state")
    source_raw = resolved["source"].get("state")
    mode_key = normalize_qilowatt_value(mode_raw)
    source_key = normalize_qilowatt_value(source_raw)
    connected_raw = normalize_qilowatt_value(resolved["connected"].get("state"))
    connected = connected_raw in {"on", "true", "connected", "1", "yes"}
    # Qilowatt documents qw_powerlimit in watts. Reuse the unit-aware power
    # normalizer so custom/template entities exposed in kW also remain valid.
    power_limit = power_kw(resolved["power_limit"])
    configured_mode = qcfg.mode
    enabled = configured_mode != "disabled"
    entities_found = sum(bool(item.get("entity_id")) for item in resolved.values())
    status = (
        "disabled" if not enabled
        else "connected" if connected
        else "disconnected" if resolved["connected"].get("entity_id")
        else "entities_missing"
    )
    mandatory = source_key in {"fusebox", "kratt"}
    action = QILOWATT_ACTIONS.get(mode_key)
    return {
        "enabled": enabled,
        "integration_mode": configured_mode,
        "status": status,
        "connected": connected,
        "mode": mode_key or None,
        "mode_raw": mode_raw,
        "source": source_key or None,
        "source_raw": source_raw,
        "power_limit_kw": power_limit,
        "action": action,
        "priority": "mandatory" if mandatory else ("external" if action else "none"),
        "mandatory": mandatory,
        "monitoring_only": False,
        "authority": "Qilowatt HA/MQTT" if configured_mode == "ha_dispatch" else "Energy Pilot",
        "entities_found": entities_found,
        "entities": {
            kind: item.get("entity_id") or None for kind, item in resolved.items()
        },
        "candidates": candidates,
    }

def temperature_c(item: dict) -> Optional[float]:
    value = numeric_state(item)
    if value is None:
        return None
    unit = str(item.get("attributes", {}).get("unit_of_measurement") or "°C").lower()
    if "°f" in unit or unit.strip() in ("f", "fahrenheit"):
        value = (value - 32.0) * 5.0 / 9.0
    return round(value, 1)

def temperature_status(value: Optional[float], kind: Literal["battery", "inverter"]) -> str:
    if value is None:
        return "Unavailable"
    warm, high = ((40.0, 50.0) if kind == "battery" else (60.0, 75.0))
    return "High" if value >= high else ("Warm" if value >= warm else "Normal")

def parse_time(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        return None

def age_seconds(item: dict, observed: datetime) -> Optional[float]:
    # last_updated changes only when a state/attribute changes. last_reported tracks
    # fresh reports even when the numeric value stays constant (for example PV=0 at night).
    stamp = parse_time(item.get("last_reported") or item.get("last_updated"))
    if stamp is None:
        return None
    return max(0.0, (observed - stamp.astimezone(timezone.utc)).total_seconds())

def power_kw(item: dict) -> Optional[float]:
    value = numeric_state(item)
    if value is None:
        return None
    unit = str(item.get("attributes", {}).get("unit_of_measurement", "W")).strip().lower()
    if unit == "kw":
        return round(value, 4)
    if unit == "mw":
        return round(value * 1000.0, 4)
    return round(value / 1000.0, 4)

def energy_kwh(item: dict) -> Optional[float]:
    value = numeric_state(item)
    if value is None:
        return None
    unit = str(item.get("attributes", {}).get("unit_of_measurement", "kWh")).strip().lower()
    if unit == "wh":
        return round(value / 1000.0, 4)
    if unit == "mwh":
        return round(value * 1000.0, 4)
    return round(value, 4)

def reconcile_load_power(
    measured_load: Optional[float], pv_power: Optional[float],
    battery_power: Optional[float], grid_power: Optional[float],
) -> tuple[Optional[float], Optional[float], bool]:
    """Use a physically consistent load when the dedicated meter is absent or falsely zero."""
    inferred = (
        max(0.0, pv_power + battery_power + grid_power)
        if pv_power is not None and battery_power is not None and grid_power is not None
        else None
    )
    derived = bool(
        inferred is not None
        and inferred > 0.05
        and (measured_load is None or abs(measured_load) <= 0.02)
    )
    return (round(inferred, 4) if derived else measured_load), inferred, derived

def reconcile_battery_power(
    measured_battery: Optional[float], pv_power: Optional[float],
    load_power: Optional[float], grid_power: Optional[float],
) -> tuple[Optional[float], Optional[float], bool]:
    """Recover a momentary false-zero battery charge from the live power balance."""
    inferred = (
        load_power - pv_power - grid_power
        if pv_power is not None and load_power is not None and grid_power is not None
        else None
    )
    derived = bool(
        inferred is not None
        and inferred < -0.05
        and measured_battery is not None
        and abs(measured_battery) <= 0.02
    )
    return (round(inferred, 4) if derived else measured_battery), inferred, derived

def make_field(value, item: dict, observed: datetime, stale_after: int, unit: str):
    age = age_seconds(item, observed)
    available = value is not None
    stale = bool(available and age is not None and age > stale_after)
    return {
        "value": value,
        "unit": unit,
        "available": available,
        "stale": stale,
        "age_seconds": round(age, 1) if age is not None else None,
        "source": item.get("entity_id"),
        "source_updated_at": item.get("last_updated"),
        "source_reported_at": item.get("last_reported") or item.get("last_updated"),
        "error": item.get("error"),
    }

def energy_flow_summary(battery_power: Optional[float], pv_power: Optional[float], load_power: Optional[float], grid_power: Optional[float]):
    """Normalize Deye power signs into an operator-friendly live energy-flow summary.

    Deye convention used by this installation:
    - battery > 0: discharging; battery < 0: charging
    - grid > 0: importing; grid < 0: exporting
    - PV and load are non-negative magnitudes
    """
    eps = 0.02

    def direction(value: Optional[float], positive: str, negative: str, idle: str = "idle") -> str:
        if value is None:
            return "unknown"
        if value > eps:
            return positive
        if value < -eps:
            return negative
        return idle

    battery_mode = direction(battery_power, "discharging", "charging")
    grid_mode = direction(grid_power, "importing", "exporting")
    pv_mode = "unknown" if pv_power is None else ("producing" if pv_power > eps else "idle")
    load_mode = "unknown" if load_power is None else ("consuming" if load_power > eps else "idle")

    routes = []
    # Routes are a reconciled allocation, not a second physical meter. Meter values
    # can differ slightly because they update at different moments. Preserve every
    # measured source direction and scale competing sources to the actual home load.
    pv_available = max(pv_power or 0.0, 0.0)
    battery_discharge = max(battery_power or 0.0, 0.0)
    grid_import = max(grid_power or 0.0, 0.0)
    home_load = max(load_power or 0.0, 0.0)
    battery_charge = max(-(battery_power or 0.0), 0.0)
    grid_export = max(-(grid_power or 0.0), 0.0)
    total_sources = pv_available + battery_discharge + grid_import
    total_sinks = home_load + battery_charge + grid_export
    balance_kw = total_sources - total_sinks

    # First preserve measured grid import as a visible home contribution. Scaling
    # all sources proportionally could shrink a real 82 W import below the 20 W
    # route threshold whenever PV production was much larger.
    source_values = {"pv": pv_available, "battery": battery_discharge, "grid": grid_import}
    allocated_to_load = {key: 0.0 for key in source_values}
    remaining_home = home_load
    if remaining_home > eps and grid_import > eps:
        amount = min(grid_import, remaining_home)
        allocated_to_load["grid"] = amount
        routes.append({"from": "grid", "to": "load", "power_kw": round(amount, 3)})
        remaining_home -= amount
    non_grid_total = pv_available + battery_discharge
    if remaining_home > eps and non_grid_total > eps:
        scale = min(1.0, remaining_home / non_grid_total)
        for source in ("pv", "battery"):
            amount = source_values[source] * scale
            if amount > eps:
                allocated_to_load[source] = amount
                routes.append({"from": source, "to": "load", "power_kw": round(amount, 3)})

    pv_remaining = max(0.0, pv_available - allocated_to_load["pv"])
    battery_remaining = max(0.0, battery_discharge - allocated_to_load["battery"])
    grid_remaining = max(0.0, grid_import - allocated_to_load["grid"])

    if battery_power is not None and battery_power < -eps:
        charge = -battery_power
        # Grid import left after the home is supplied must remain visible as
        # grid-to-battery flow instead of being silently attributed to PV.
        grid_to_battery = min(grid_remaining, charge)
        if grid_to_battery > eps:
            routes.append({"from": "grid", "to": "battery", "power_kw": round(grid_to_battery, 3)})
            grid_remaining -= grid_to_battery
            charge -= grid_to_battery
        pv_to_battery = min(pv_remaining, charge)
        if pv_to_battery > eps:
            routes.append({"from": "pv", "to": "battery", "power_kw": round(pv_to_battery, 3)})
            pv_remaining -= pv_to_battery
            charge -= pv_to_battery

    if grid_power is not None and grid_power < -eps:
        export = -grid_power
        pv_to_grid = min(pv_remaining, export)
        if pv_to_grid > eps:
            routes.append({"from": "pv", "to": "grid", "power_kw": round(pv_to_grid, 3)})
            pv_remaining -= pv_to_grid
            export -= pv_to_grid
        if battery_remaining > eps and export > eps:
            routes.append({"from": "battery", "to": "grid", "power_kw": round(min(battery_remaining, export), 3)})

    parts = []
    if battery_mode == "discharging":
        parts.append(f"Battery discharging {battery_power:.2f} kW")
    elif battery_mode == "charging":
        parts.append(f"Battery charging {-battery_power:.2f} kW")
    if grid_mode == "importing":
        parts.append(f"importing {grid_power:.2f} kW")
    elif grid_mode == "exporting":
        parts.append(f"exporting {-grid_power:.2f} kW")
    if pv_mode == "idle":
        parts.append("PV idle")
    elif pv_mode == "producing":
        parts.append(f"PV producing {pv_power:.2f} kW")

    return {
        "summary": "; ".join(parts) if parts else "Energy flow unavailable",
        "battery": battery_mode,
        "grid": grid_mode,
        "pv": pv_mode,
        "load": load_mode,
        "routes": routes,
        "balance_kw": round(balance_kw, 3),
        "balance_status": "balanced" if abs(balance_kw) < eps else ("unallocated_source" if balance_kw > 0 else "unallocated_demand"),
    }

def state_snapshot(cfg: EnergyPilotConfig):
    observed = datetime.now(timezone.utc)
    stale_after = cfg.power_connector.stale_after_seconds
    cc, pc = cfg.battery_connector, cfg.power_connector
    states = ha_states()
    connector_defs = {
        "capacity": (cc.capacity_entity, "battery_capacity"),
        "soc": (cc.soc_entity, "battery_soc"),
        "cycle_count": (cc.cycle_count_entity, "battery_cycles"),
        "battery_power": (cc.power_entity, "battery_power"),
        "pv_power": (pc.pv_power_entity, "pv_power"),
        "load_power": (pc.load_power_entity, "load_power"),
        "grid_power": (pc.grid_power_entity, "grid_power"),
        "battery_temperature": (cc.temperature_entity, "battery_temperature"),
        "inverter_temperature": (pc.inverter_temperature_entity, "inverter_temperature"),
    }
    discovered = {
        key: discover_connector_item(configured, kind, states)
        for key, (configured, kind) in connector_defs.items()
    }
    raw = {
        key: result[0] for key, result in discovered.items()
    }
    detected_capacity = energy_kwh(raw["capacity"])
    capacity = cc.manual_capacity_kwh if cc.capacity_source == "manual_override" else detected_capacity
    soc = numeric_state(raw["soc"])
    cycle_count = numeric_state(raw["cycle_count"])
    battery_temperature = temperature_c(raw["battery_temperature"])
    inverter_temperature = temperature_c(raw["inverter_temperature"])
    battery_power = power_kw(raw["battery_power"])
    pv_power = power_kw(raw["pv_power"])
    measured_load_power = power_kw(raw["load_power"])
    grid_power = power_kw(raw["grid_power"])
    measured_battery_power = battery_power
    battery_power, inferred_battery_power, battery_is_derived = reconcile_battery_power(
        measured_battery_power, pv_power, measured_load_power, grid_power,
    )
    # Deye's dedicated load entity can momentarily report 0 W while the inverter
    # still exposes valid PV, battery and grid meters. With the sign convention
    # used here, conservation gives: home = PV + battery discharge + grid import.
    load_power, inferred_load_power, load_is_derived = reconcile_load_power(
        measured_load_power, pv_power, battery_power, grid_power,
    )
    energy = round(capacity * soc / 100.0, 3) if capacity is not None and soc is not None else None
    flow = energy_flow_summary(battery_power, pv_power, load_power, grid_power)
    # Power measurements must be fresh. Capacity and SOC are slower-moving
    # measurements and should not degrade the live planner after only two minutes.
    fields = {
        "battery.capacity_kwh": make_field(capacity, raw["capacity"], observed, max(stale_after, 86400), "kWh"),
        "battery.soc_pct": make_field(soc, raw["soc"], observed, max(stale_after, 900), "%"),
        "battery.cycle_count": make_field(cycle_count, raw["cycle_count"], observed, max(stale_after, 86400), "cycles"),
        "battery.temperature_c": make_field(battery_temperature, raw["battery_temperature"], observed, max(stale_after, 900), "°C"),
        "inverter.temperature_c": make_field(inverter_temperature, raw["inverter_temperature"], observed, max(stale_after, 900), "°C"),
        "battery.power_kw": make_field(battery_power, raw["battery_power"], observed, stale_after, "kW"),
        "pv.power_kw": make_field(pv_power, raw["pv_power"], observed, stale_after, "kW"),
        "load.power_kw": make_field(load_power, raw["load_power"], observed, stale_after, "kW"),
        "grid.power_kw": make_field(grid_power, raw["grid_power"], observed, stale_after, "kW"),
    }
    fields["battery.capacity_kwh"]["detected_value"] = detected_capacity
    field_discovery = {
        "battery.capacity_kwh": "capacity",
        "battery.soc_pct": "soc",
        "battery.cycle_count": "cycle_count",
        "battery.power_kw": "battery_power",
        "pv.power_kw": "pv_power",
        "load.power_kw": "load_power",
        "grid.power_kw": "grid_power",
        "battery.temperature_c": "battery_temperature",
        "inverter.temperature_c": "inverter_temperature",
    }
    for field_name, discovery_key in field_discovery.items():
        configured, _ = connector_defs[discovery_key]
        _, mode, candidates, ambiguous = discovered[discovery_key]
        fields[field_name].update({
            "connector_mode": mode,
            "configured_source": configured,
            "candidates": candidates,
            "ambiguous": ambiguous,
        })
    fields["battery.temperature_c"]["status"] = temperature_status(battery_temperature, "battery")
    fields["inverter.temperature_c"]["status"] = temperature_status(inverter_temperature, "inverter")
    fields["battery.power_kw"].update({
        "derived": battery_is_derived,
        "measured_value": measured_battery_power,
        "inferred_value": round(inferred_battery_power, 4) if inferred_battery_power is not None else None,
        "calculation": "load - pv - grid" if battery_is_derived else None,
    })
    if battery_is_derived:
        fields["battery.power_kw"].update({
            "available": True,
            "stale": False,
            "age_seconds": 0.0,
            "source": "power_balance",
        })
    fields["load.power_kw"].update({
        "derived": load_is_derived,
        "measured_value": measured_load_power,
        "calculation": "pv + battery + grid" if load_is_derived else None,
    })
    if load_is_derived:
        fields["load.power_kw"].update({
            "available": True,
            "stale": False,
            "age_seconds": 0.0,
            "source": "power_balance",
        })
    if cc.capacity_source == "manual_override":
        fields["battery.capacity_kwh"].update({"source": "manual_override", "stale": False, "age_seconds": 0.0})
    missing = [name for name, data in fields.items() if not data["available"]]
    stale = [name for name, data in fields.items() if data["stale"]]
    critical_names = {"battery.soc_pct", "battery.power_kw", "pv.power_kw", "load.power_kw", "grid.power_kw"}
    critical_missing = [name for name in missing if name in critical_names]
    critical_stale = [name for name in stale if name in critical_names and not (name == "pv.power_kw" and (fields[name].get("value") or 0.0) <= 0.02)]
    if len(critical_missing) == len(critical_names):
        status = "unavailable"
    elif critical_missing or critical_stale:
        status = "degraded"
    else:
        status = "ok"
    return {
        "observed_at": observed.isoformat(),
        "health": {"status": status, "missing": missing, "stale": stale, "critical_missing": critical_missing, "critical_stale": critical_stale, "stale_after_seconds": stale_after},
        "battery": {
            "capacity_kwh": fields["battery.capacity_kwh"],
            "soc_pct": fields["battery.soc_pct"],
            "cycle_count": fields["battery.cycle_count"],
            "temperature_c": fields["battery.temperature_c"],
            "energy_kwh": {"value": energy, "unit": "kWh", "available": energy is not None},
            "power_kw": fields["battery.power_kw"],
        },
        "pv": {"power_kw": fields["pv.power_kw"]},
        "load": {"power_kw": fields["load.power_kw"]},
        "grid": {"power_kw": fields["grid.power_kw"]},
        "inverter": {"temperature_c": fields["inverter.temperature_c"]},
        "flow": flow,
        "sources": {
            "battery_capacity": fields["battery.capacity_kwh"].get("source") or cc.capacity_entity,
            "battery_soc": fields["battery.soc_pct"].get("source") or cc.soc_entity,
            "battery_cycle_count": fields["battery.cycle_count"].get("source") or cc.cycle_count_entity,
            "battery_cycle_count_configured": cc.cycle_count_entity,
            "battery_temperature": fields["battery.temperature_c"].get("source") or cc.temperature_entity,
            "inverter_temperature": fields["inverter.temperature_c"].get("source") or pc.inverter_temperature_entity,
            "battery_power": "power_balance" if battery_is_derived else (fields["battery.power_kw"].get("source") or cc.power_entity),
            "battery_power_configured": cc.power_entity,
            "pv_power": fields["pv.power_kw"].get("source") or pc.pv_power_entity,
            "load_power": "power_balance" if load_is_derived else (fields["load.power_kw"].get("source") or pc.load_power_entity),
            "load_power_configured": pc.load_power_entity,
            "grid_power": fields["grid.power_kw"].get("source") or pc.grid_power_entity,
        },
    }




def normalize_price(value: Optional[float], cfg: PriceConnectorConfig, unit_hint: Optional[str] = None) -> Optional[float]:
    if value is None:
        return None
    hint = str(unit_hint or "").strip().lower().replace("€", "eur")
    if "mwh" in hint:
        cents = value / 10.0
    elif "cent" in hint or "c/kwh" in hint or "s/kwh" in hint:
        cents = value
    elif "eur/kwh" in hint or "eur per kwh" in hint:
        cents = value * 100.0
    elif cfg.price_unit == "cents_per_kwh":
        cents = value
    elif cfg.price_unit == "eur_per_mwh":
        cents = value / 10.0
    elif cfg.price_unit == "eur_per_kwh":
        cents = value * 100.0
    else:
        # Heuristic only when no unit metadata exists. Nord Pool EUR/kWh values
        # are typically below 2, c/kWh below 200 and EUR/MWh often above 20.
        absolute = abs(value)
        if absolute <= 2.0:
            cents = value * 100.0
        elif absolute > 200.0:
            cents = value / 10.0
        else:
            cents = value
    if cfg.source_includes_vat and cfg.vat_percent:
        cents /= 1.0 + cfg.vat_percent / 100.0
    return round(cents, 4)

def grid_tariff_cents(cfg: EnergyPilotConfig, stamp: datetime) -> tuple[float, str]:
    tariff = cfg.energy_tariff
    try:
        local = stamp.astimezone(ZoneInfo(cfg.site.timezone))
    except Exception:
        local = stamp.astimezone(timezone.utc)
    holiday = local.date().isoformat() in tariff.holiday_dates
    if local.weekday() >= 5 or holiday:
        return tariff.grid_weekend_holiday_cents_kwh, "weekend_or_holiday"
    if tariff.grid_day_start_hour <= local.hour < tariff.grid_day_end_hour:
        return tariff.grid_weekday_day_cents_kwh, "weekday_day"
    return tariff.grid_weekday_night_cents_kwh, "weekday_night"

def effective_prices(cfg: EnergyPilotConfig, spot_cents: Optional[float], stamp: datetime) -> dict:
    if spot_cents is None:
        return {"spot": None, "import": None, "export": None, "components": {}}
    tariff = cfg.energy_tariff
    grid_fee, grid_period = grid_tariff_cents(cfg, stamp)
    regulated = (
        tariff.electricity_excise_cents_kwh
        + tariff.renewable_fee_cents_kwh
        + tariff.security_of_supply_fee_cents_kwh
        + tariff.import_balancing_cost_cents_kwh
    )
    import_subtotal = spot_cents + tariff.import_margin_cents_kwh + grid_fee + regulated
    vat = import_subtotal * cfg.price_connector.vat_percent / 100.0 if cfg.price_connector.include_vat else 0.0
    support = tariff.renewable_support_cents_kwh if tariff.renewable_support_enabled else 0.0
    # Estonian export settlement applies VAT only to the balancing-service
    # component. This is a tariff rule, not a user preference.
    export_balancing_vat = tariff.export_balancing_cost_cents_kwh * cfg.price_connector.vat_percent / 100.0
    export_total = spot_cents + tariff.export_margin_cents_kwh + tariff.export_balancing_cost_cents_kwh + export_balancing_vat + support
    return {
        "spot": round(spot_cents, 5),
        "import": round(import_subtotal + vat, 5),
        "export": round(export_total, 5),
        "components": {
            "spot": round(spot_cents, 5),
            "import_margin": round(tariff.import_margin_cents_kwh, 5),
            "export_margin": round(tariff.export_margin_cents_kwh, 5),
            "electricity_excise": round(tariff.electricity_excise_cents_kwh, 5),
            "renewable_fee": round(tariff.renewable_fee_cents_kwh, 5),
            "security_of_supply_fee": round(tariff.security_of_supply_fee_cents_kwh, 5),
            "import_balancing_cost": round(tariff.import_balancing_cost_cents_kwh, 5),
            "export_balancing_cost": round(tariff.export_balancing_cost_cents_kwh, 5),
            "export_balancing_vat": round(export_balancing_vat, 5),
            "grid_fee": round(grid_fee, 5),
            "grid_period": grid_period,
            "vat": round(vat, 5),
            "renewable_support": round(support, 5),
        },
    }


def price_number(value) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def parse_price_slots(item: dict, cfg: EnergyPilotConfig) -> list[dict]:
    pc = cfg.price_connector
    attributes = item.get("attributes", {}) if item else {}
    slots = []
    for source_name in ("raw_today", "raw_tomorrow"):
        raw_slots = attributes.get(source_name) or []
        if not isinstance(raw_slots, list):
            continue
        for row in raw_slots:
            if not isinstance(row, dict):
                continue
            start = row.get("start") or row.get("time")
            end = row.get("end")
            value = row.get("value", row.get("price"))
            numeric = price_number(value)
            if numeric is None:
                continue
            stamp = parse_time(start)
            if stamp is None:
                continue
            unit_hint = row.get("unit") or row.get("unit_of_measurement") or attributes.get("unit_of_measurement")
            spot = normalize_price(numeric, pc, unit_hint)
            effective = effective_prices(cfg, spot, stamp)
            slots.append({
                "start": stamp.isoformat(),
                "end": parse_time(end).isoformat() if parse_time(end) else None,
                "spot_cents_kwh": effective["spot"],
                "import_cents_kwh": effective["import"],
                "export_cents_kwh": effective["export"],
                # Preserve the exact tariff calculation for every market slot.
                # The UI uses this server-side breakdown for price explainers,
                # avoiding client-side assumptions about time-based grid fees.
                "components": effective["components"],
                "source": source_name,
            })
    slots.sort(key=lambda x: x["start"])
    return slots


def price_snapshot(cfg: EnergyPilotConfig):
    observed = datetime.now(timezone.utc)
    pc = cfg.price_connector
    item, connector_mode, candidates = discover_price_item(pc.entity)
    attributes = item.get("attributes", {}) if item else {}
    unit_hint = attributes.get("unit_of_measurement")
    current_raw = numeric_state(item)
    current_source = "state"
    if current_raw is None:
        for key in ("current_price", "current", "price", "value"):
            current_raw = price_number(attributes.get(key))
            if current_raw is not None:
                current_source = f"attribute:{key}"
                break
    reported_current = normalize_price(current_raw, pc, unit_hint)
    current = reported_current
    slots = parse_price_slots(item, cfg)
    # raw_today/raw_tomorrow are the authoritative scheduled market values. Some
    # integrations expose a lagging or differently rounded sensor state. Infer a
    # missing slot end from the next start (or configured slot length) and prefer
    # the exact active slot for all live Planner calculations.
    active_slot = None
    active_slot_end = None
    for index, slot in enumerate(slots):
        start = parse_time(slot["start"])
        end = parse_time(slot.get("end"))
        if start is None:
            continue
        if end is None:
            next_start = parse_time(slots[index + 1]["start"]) if index + 1 < len(slots) else None
            end = next_start if next_start and next_start > start else start + timedelta(minutes=cfg.planning.slot_minutes)
        if start <= observed < end:
            active_slot = slot
            active_slot_end = end
            break
    if active_slot is not None:
        current = active_slot.get("spot_cents_kwh")
        current_source = "active_slot"
    current_effective = effective_prices(cfg, current, observed)
    # Keep the currently active market slot at the front of the planning
    # horizon. This lets the UI present and override what the inverter should
    # do now instead of skipping directly to the next quarter-hour.
    future = [
        slot for slot in slots
        if parse_time(slot["start"])
        and (slot is active_slot or parse_time(slot["start"]) >= observed)
    ]
    horizon_end = observed.timestamp() + cfg.planning.horizon_hours * 3600
    horizon = [slot for slot in future if parse_time(slot["start"]).timestamp() < horizon_end]
    planner_price_key = "export_cents_kwh" if cfg.planning.strategy == "export_value" else "import_cents_kwh"
    for slot in horizon:
        start = parse_time(slot.get("start"))
        end = parse_time(slot.get("end"))
        if start is not None and end is None:
            end = start + timedelta(minutes=cfg.planning.slot_minutes)
        is_current = bool(start and end and start <= observed < end)
        progress = (
            (observed - start).total_seconds() / (end - start).total_seconds() * 100.0
            if is_current and end > start else 0.0
        )
        slot["is_current"] = is_current
        slot["progress_percent"] = round(max(0.0, min(100.0, progress)), 1)
        slot["remaining_minutes"] = round(
            max(0.0, (end - observed).total_seconds() / 60.0), 1
        ) if is_current else None
        slot["price_cents_kwh"] = slot.get(planner_price_key)
    planner_current = current_effective["export"] if cfg.planning.strategy == "export_value" else current_effective["import"]
    prices = [slot[planner_price_key] for slot in horizon if slot.get(planner_price_key) is not None]
    import_prices = [slot["import_cents_kwh"] for slot in horizon if slot.get("import_cents_kwh") is not None]
    export_prices = [slot["export_cents_kwh"] for slot in horizon if slot.get("export_cents_kwh") is not None]
    rank = None
    percentile = None
    if planner_current is not None and prices:
        rank = 1 + sum(1 for value in prices if value > planner_current)
        percentile = round(sum(1 for value in prices if value <= planner_current) / len(prices) * 100.0, 1)
    priced_horizon = [slot for slot in horizon if slot.get(planner_price_key) is not None]
    cheapest = min(priced_horizon, key=lambda x: x[planner_price_key], default=None)
    dearest = max(priced_horizon, key=lambda x: x[planner_price_key], default=None)
    age = age_seconds(item, observed)
    stale = bool(current is not None and age is not None and age > pc.stale_after_seconds)
    return {
        "observed_at": observed.isoformat(),
        "entity_id": item.get("entity_id") or pc.entity,
        "configured_entity_id": pc.entity,
        "connector_mode": connector_mode,
        "discovered_candidates": candidates,
        "available": current is not None,
        "stale": stale,
        "age_seconds": round(age, 1) if age is not None else None,
        "current_cents_kwh": planner_current,
        "spot_cents_kwh": current_effective["spot"],
        "import_cents_kwh": current_effective["import"],
        "export_cents_kwh": current_effective["export"],
        "components": current_effective["components"],
        "planner_price_kind": "export" if cfg.planning.strategy == "export_value" else "import",
        "current_source": current_source if current is not None else None,
        "reported_current_spot_cents_kwh": reported_current,
        "active_slot_start": active_slot.get("start") if active_slot else None,
        "active_slot_end": active_slot_end.isoformat() if active_slot_end else None,
        "source_unit": unit_hint,
        "source_vat_included": pc.source_includes_vat,
        "currency": cfg.site.currency,
        "vat_included": pc.include_vat,
        "vat_percent": pc.vat_percent,
        "horizon_hours": cfg.planning.horizon_hours,
        "slot_count": len(horizon),
        "rank_high_to_low": rank,
        "percentile": percentile,
        "import_percentile": round(sum(1 for value in import_prices if value <= current_effective["import"]) / len(import_prices) * 100.0, 1) if current_effective["import"] is not None and import_prices else None,
        "export_percentile": round(sum(1 for value in export_prices if value <= current_effective["export"]) / len(export_prices) * 100.0, 1) if current_effective["export"] is not None and export_prices else None,
        "cheapest": cheapest,
        "dearest": dearest,
        "slots": horizon,
        "market_slots": slots,
        "error": item.get("error"),
    }

def energy_action(
    import_price: Optional[float],
    export_price: Optional[float],
    import_percentile: Optional[float],
    export_percentile: Optional[float],
    soc: Optional[float],
    reserve: float,
    max_soc: float,
    pv_available: bool = False,
) -> tuple[str, str]:
    """Map price signals and available flexibility to the public action vocabulary."""
    if export_price is not None and export_price <= 0:
        return "LIMIT EXPORT", f"Effective export price is {export_price:.2f} c/kWh, so unprofitable grid export should be limited."
    if import_percentile is not None and import_percentile <= 20:
        if soc is not None and soc < max_soc - 3:
            return "BUY", f"Effective import price is in the cheapest {import_percentile:.0f}% of the planning horizon and the battery has room to charge."
        return "SAVE BATTERY", f"Effective import price is in the cheapest {import_percentile:.0f}% of the planning horizon; preserve battery energy for later."
    if export_percentile is not None and export_percentile >= 80:
        if soc is not None and soc > reserve + 5:
            return "SELL", f"Effective export price is in the top {100-export_percentile:.0f}% of the planning horizon and battery reserve is available."
        if pv_available:
            return "PV SELL", "The export price is strong, but battery reserve should be protected; sell only available solar surplus."
    if import_percentile is not None and import_percentile <= 35:
        return "SAVE BATTERY", f"Import price is relatively low at the {import_percentile:.0f}th percentile; avoid using stored battery energy."
    if pv_available and export_percentile is not None and export_percentile >= 65:
        return "PV SELL", "The export price is above average; sell available solar surplus without discharging the battery."
    return "NORMAL", "No exceptional buy, sell or export-limit signal is present; use normal inverter balancing."


def forecast_points(entity_id: str, slots: list[dict], value_kind: str) -> tuple[list[Optional[float]], dict]:
    """Read common timestamped HA forecast attribute shapes into slot-average kW."""
    if not entity_id:
        return [None] * len(slots), {"available": False, "entity_id": "", "mode": "not_configured"}
    item = ha_state(entity_id)
    attrs = item.get("attributes", {})
    points: list[tuple[datetime, float]] = []
    for key, scale in (("watts", 0.001), ("watt_hours_period", 0.004)):
        values = attrs.get(key)
        if isinstance(values, dict):
            for stamp, value in values.items():
                parsed, numeric = parse_time(str(stamp)), price_number(value)
                if parsed and numeric is not None:
                    points.append((parsed, max(0.0, numeric * scale)))
    for key in ("forecast", "detailedForecast", "detailed_forecast", "data"):
        values = attrs.get(key)
        if not isinstance(values, list):
            continue
        for row in values:
            if not isinstance(row, dict):
                continue
            stamp = parse_time(row.get("period_start") or row.get("start") or row.get("datetime") or row.get("time"))
            candidates = ("pv_estimate", "power", "watts", "value") if value_kind == "pv" else ("load_kw", "power", "value")
            numeric = next((price_number(row.get(name)) for name in candidates if price_number(row.get(name)) is not None), None)
            if stamp and numeric is not None:
                unit = str(row.get("unit") or attrs.get("unit_of_measurement") or "kW").lower()
                kw = numeric / 1000.0 if unit == "w" or "watt" in unit else numeric
                points.append((stamp, max(0.0, kw)))
    points.sort(key=lambda pair: pair[0])
    result = []
    for slot in slots:
        start = parse_time(slot.get("start"))
        nearest = min(points, key=lambda pair: abs((pair[0] - start).total_seconds()), default=None) if start else None
        result.append(round(nearest[1], 3) if nearest and abs((nearest[0] - start).total_seconds()) <= 1800 else None)
    available = any(value is not None for value in result)
    return result, {"available": available, "entity_id": entity_id, "mode": "forecast" if available else "unavailable", "points": sum(value is not None for value in result)}


def apply_slot_plan(cfg: EnergyPilotConfig, snapshot: dict, price: dict) -> None:
    """Planner v2: sequential look-ahead plan with SOC, PV and load simulation."""
    slots = price.get("slots", [])
    battery = snapshot["battery"]
    capacity = battery["capacity_kwh"].get("value")
    initial_soc = battery["soc_pct"].get("value")
    if not slots or capacity is None or initial_soc is None:
        price["plan"] = {"version": 2, "available": False, "reason": "Battery capacity, SOC and future price slots are required."}
        return
    fc = cfg.forecast_connector
    pv_values, pv_status = weather_adjusted_solar_points(cfg, slots)
    if not pv_status["available"] and fc.pv_forecast_entity:
        pv_values, manual_status = forecast_points(fc.pv_forecast_entity, slots, "pv")
        if manual_status["available"]:
            pv_status = manual_status
            pv_status["mode"] = "manual_entity"
    load_values, load_status = forecast_points(fc.load_forecast_entity, slots, "load")
    if not load_status["available"]:
        learned_values, learned_status = learned_load_points(cfg, slots)
        if learned_status["available"]:
            load_values, load_status = learned_values, learned_status
    live_load = snapshot["load"]["power_kw"].get("value")
    fallback_load = fc.fallback_load_kw if fc.fallback_load_kw is not None else live_load
    load_values = [value if value is not None else max(0.0, fallback_load or 0.0) for value in load_values]
    pv_raw_values = list(pv_values)
    pv_calibration = [pv_learning_factor(cfg, parse_time(slot.get("start"))) for slot in slots]
    pv_values = [
        value * pv_calibration[index][0] if value is not None else 0.0
        for index, value in enumerate(pv_values)
    ]
    calibration_samples = sum(samples for _, samples in pv_calibration)
    pv_status["calibration_samples"] = calibration_samples
    pv_status["average_calibration_factor"] = round(
        sum(factor for factor, _ in pv_calibration) / len(pv_calibration), 3
    ) if pv_calibration else 1.0
    policy = cfg.battery_policy
    reserve_kwh = capacity * policy.reserve_soc_percent / 100.0
    max_kwh = capacity * policy.max_planned_soc_percent / 100.0
    energy = max(reserve_kwh, min(max_kwh, capacity * initial_soc / 100.0))
    efficiency = (policy.roundtrip_efficiency_percent / 100.0) ** 0.5
    duration = cfg.planning.slot_minutes / 60.0
    import_values = [float(slot["import_cents_kwh"]) for slot in slots if slot.get("import_cents_kwh") is not None]
    export_values = [float(slot["export_cents_kwh"]) for slot in slots if slot.get("export_cents_kwh") is not None]
    import_sorted = sorted(import_values)
    import_cutoff = import_sorted[max(0, int(len(import_sorted) * 0.2) - 1)] if import_sorted else None
    plan_quality = "forecast" if pv_status["available"] else "price_and_live_load"
    for index, slot in enumerate(slots):
        import_price = slot.get("import_cents_kwh")
        export_price = slot.get("export_cents_kwh")
        pv_kw, load_kw = pv_values[index], load_values[index]
        soc_before = energy / capacity * 100.0
        later_export_pairs = [
            (candidate_index, float(candidate["export_cents_kwh"]))
            for candidate_index, candidate in enumerate(slots[index:], start=index)
            if candidate.get("export_cents_kwh") is not None
        ]
        later_exports = [candidate_price for _, candidate_price in later_export_pairs]
        best_later_export = max(later_exports, default=export_price or 0.0)
        best_later_index = next(
            (candidate_index for candidate_index, candidate_price in later_export_pairs if candidate_price == best_later_export),
            index,
        )
        available_kwh = max(0.0, energy - reserve_kwh)
        future_net_need = sum(max(0.0, load_values[i] - pv_values[i]) * duration for i in range(index, min(len(slots), index + 24)))
        remaining_home_need = sum(
            max(0.0, load_values[i] - pv_values[i]) * duration / efficiency
            for i in range(index, len(slots))
        )
        sellable_kwh = max(0.0, available_kwh - remaining_home_need)
        home_need_before_best_export = sum(
            max(0.0, load_values[i] - pv_values[i]) * duration / efficiency
            for i in range(index, best_later_index)
        )
        sell_window_energy = min(
            policy.max_discharge_kw * duration / efficiency,
            max(0.0, max_kwh - reserve_kwh),
        )
        energy_needed_for_home_and_sell = home_need_before_best_export + sell_window_energy
        action = "NORMAL"
        reason = "Normal balancing is the best fit after considering later prices and projected battery state."
        if export_price is not None and export_price <= 0 and pv_kw > load_kw:
            action, reason = "LIMIT EXPORT", f"Export is worth {export_price:.2f} c/kWh; limit unprofitable solar export."
        elif export_price is not None and export_price > policy.degradation_cost_cents_kwh and best_later_export > 0 and export_price >= best_later_export * 0.92 and sellable_kwh > 0.1:
            action, reason = "SELL", (
                f"This is within 8% of the best remaining export price ({best_later_export:.2f} c/kWh). "
                f"After reserving {remaining_home_need:.1f} kWh for forecast home demand, "
                f"{sellable_kwh:.1f} kWh remains available to sell."
            )
        elif pv_status["available"] and import_price is not None and import_cutoff is not None and import_price <= import_cutoff and energy + sum(pv_values[index:index + 24]) * duration * efficiency < reserve_kwh + future_net_need:
            action, reason = "BUY", "Import is among the cheapest 20% and forecast solar plus stored energy is insufficient for the next six hours."
        elif available_kwh <= 0.1 and max(0.0, load_kw - pv_kw) > 0:
            action, reason = "SAVE BATTERY", (
                f"The battery has reached the configured {policy.reserve_soc_percent:.0f}% reserve, "
                "so home demand must be covered without further battery discharge."
            )
        elif pv_kw > load_kw and export_price is not None and export_price > 0 and energy >= max_kwh - 0.1:
            action, reason = "PV SELL", "Forecast solar exceeds load and the battery is effectively full; export only the solar surplus."

        charge_kw = discharge_kw = grid_import_kw = grid_export_kw = 0.0
        pv_surplus = max(0.0, pv_kw - load_kw)
        deficit = max(0.0, load_kw - pv_kw)
        if action == "BUY":
            charge_kw = min(policy.max_charge_kw, max(0.0, (max_kwh - energy) / duration / efficiency))
            grid_import_kw = load_kw + max(0.0, charge_kw - pv_kw)
            energy += charge_kw * duration * efficiency
        elif action == "SELL":
            home_discharge_kw = min(
                deficit,
                policy.max_discharge_kw,
                available_kwh * efficiency / duration,
            )
            battery_export_kw = min(
                max(0.0, policy.max_discharge_kw - home_discharge_kw),
                sellable_kwh * efficiency / duration,
                max(0.0, cfg.grid_policy.max_export_kw - pv_surplus),
            )
            discharge_kw = home_discharge_kw + battery_export_kw
            grid_import_kw = max(0.0, deficit - home_discharge_kw)
            grid_export_kw = min(cfg.grid_policy.max_export_kw, pv_surplus + battery_export_kw)
            energy -= discharge_kw * duration / efficiency
        elif action == "SAVE BATTERY":
            charge_kw = min(pv_surplus, policy.max_charge_kw, max(0.0, (max_kwh - energy) / duration / efficiency))
            energy += charge_kw * duration * efficiency
            grid_import_kw = deficit
            grid_export_kw = min(cfg.grid_policy.max_export_kw, max(0.0, pv_surplus - charge_kw))
        elif action == "PV SELL":
            grid_import_kw = deficit
            grid_export_kw = min(cfg.grid_policy.max_export_kw, pv_surplus)
        else:
            if pv_surplus > 0:
                charge_kw = min(pv_surplus, policy.max_charge_kw, max(0.0, (max_kwh - energy) / duration / efficiency))
                energy += charge_kw * duration * efficiency
                grid_export_kw = 0.0 if action == "LIMIT EXPORT" else min(cfg.grid_policy.max_export_kw, max(0.0, pv_surplus - charge_kw))
            elif deficit > 0:
                discharge_kw = min(deficit, policy.max_discharge_kw, available_kwh * efficiency / duration)
                energy -= discharge_kw * duration / efficiency
                grid_import_kw = max(0.0, deficit - discharge_kw)
        energy = max(reserve_kwh, min(max_kwh, energy))
        slot.update({
            "action": action, "action_reason": reason,
            "pv_forecast_kw": round(pv_kw, 3) if pv_raw_values[index] is not None else None,
            "pv_raw_forecast_kw": round(pv_raw_values[index], 3) if pv_raw_values[index] is not None else None,
            "pv_calibration_factor": round(pv_calibration[index][0], 3),
            "load_forecast_kw": round(load_kw, 3), "soc_before_percent": round(soc_before, 1),
            "soc_after_percent": round(energy / capacity * 100.0, 1), "charge_kw": round(charge_kw, 2),
            "discharge_kw": round(discharge_kw, 2), "grid_import_kw": round(grid_import_kw, 2),
            "grid_export_kw": round(grid_export_kw, 2), "best_later_export_cents_kwh": round(best_later_export, 3),
            "forecast_confidence": min(
                98,
                (72 if pv_status["available"] else 35)
                + min(14, pv_calibration[index][1] * 2)
                + (10 if load_status.get("available") else 0),
            ),
        })
    summaries = {}
    for slot in slots:
        stamp = parse_time(slot.get("start"))
        day = stamp.astimezone(ZoneInfo(cfg.site.timezone)).date().isoformat() if stamp else "unknown"
        summary = summaries.setdefault(day, {
            "date": day, "pv_kwh": 0.0, "load_kwh": 0.0, "grid_import_kwh": 0.0,
            "grid_export_kwh": 0.0, "min_soc_percent": 100.0, "max_soc_percent": 0.0,
            "actions": {},
        })
        summary["pv_kwh"] += float(slot.get("pv_forecast_kw") or 0.0) * duration
        summary["load_kwh"] += float(slot.get("load_forecast_kw") or 0.0) * duration
        summary["grid_import_kwh"] += float(slot.get("grid_import_kw") or 0.0) * duration
        summary["grid_export_kwh"] += float(slot.get("grid_export_kw") or 0.0) * duration
        summary["min_soc_percent"] = min(summary["min_soc_percent"], float(slot.get("soc_after_percent") or 0.0))
        summary["max_soc_percent"] = max(summary["max_soc_percent"], float(slot.get("soc_before_percent") or 0.0))
        action_name = slot.get("action") or "NORMAL"
        summary["actions"][action_name] = summary["actions"].get(action_name, 0) + 1
    daily_summary = []
    for summary in summaries.values():
        for key in ("pv_kwh", "load_kwh", "grid_import_kwh", "grid_export_kwh", "min_soc_percent", "max_soc_percent"):
            summary[key] = round(summary[key], 1)
        daily_summary.append(summary)
    price["plan"] = {
        "version": 2, "available": True, "quality": plan_quality, "pv_forecast": pv_status,
        "load_forecast": load_status, "load_fallback_kw": round(fallback_load, 3) if fallback_load is not None else None,
        "initial_soc_percent": round(initial_soc, 1), "final_soc_percent": round(energy / capacity * 100.0, 1),
        "buy_enabled": pv_status["available"], "daily_summary": daily_summary,
    }


def _planner_v3_normal_baseline(
    slots: list[dict], pv_values: list[float], load_values: list[float],
    initial_energy: float, reserve_kwh: float, max_kwh: float, duration: float,
    efficiency: float, policy: BatteryPolicyConfig, grid_policy: GridPolicyConfig,
) -> dict:
    """Simulate ordinary self-consumption for an apples-to-apples cost baseline."""
    energy = initial_energy
    cash_cost = 0.0
    wear_cost = 0.0
    path = []
    for index, slot in enumerate(slots):
        net_kw = load_values[index] - pv_values[index]
        if net_kw >= 0:
            delivered = min(
                net_kw * duration,
                policy.max_discharge_kw * duration,
                max(0.0, energy - reserve_kwh) * efficiency,
            )
            energy -= delivered / efficiency
            grid_import = max(0.0, net_kw * duration - delivered)
            grid_export = 0.0
        else:
            surplus = -net_kw * duration
            stored = min(
                surplus * efficiency,
                policy.max_charge_kw * duration * efficiency,
                max(0.0, max_kwh - energy),
            )
            energy += stored
            grid_import = 0.0
            grid_export = min(grid_policy.max_export_kw * duration, max(0.0, surplus - stored / efficiency))
        slot_cash_cost = (
            grid_import * float(slot.get("import_cents_kwh") or 0.0)
            - grid_export * float(slot.get("export_cents_kwh") or 0.0)
        )
        before = path[-1]["after"] if path else initial_energy
        delta = energy - before
        slot_wear_cost = abs(delta) * policy.degradation_cost_cents_kwh
        cash_cost += slot_cash_cost
        wear_cost += slot_wear_cost
        path.append({
            "before": before, "after": energy, "delta": delta,
            "grid_import_kwh": grid_import, "grid_export_kwh": grid_export,
            "grid_charge_kwh": 0.0, "grid_charge_justified": False,
            "grid_charge_gain_cents_kwh": 0.0,
            "cash_cost_cents": slot_cash_cost, "wear_cost_cents": slot_wear_cost,
        })
    return {
        "path": path, "cash_cost_cents": cash_cost,
        "wear_cost_cents": wear_cost, "final_energy_kwh": energy,
    }


def planner_behavior_settings(planning: PlanningConfig) -> dict:
    """Translate a user-facing Planner personality into explicit objective weights."""
    presets = {
        "value_first": {
            "label": "Value First",
            "solar_buffer_percent": 2.0,
            "minimum_forecast_confidence_percent": 55,
            "curtailment_penalty_cents_kwh": 12.0,
            "wear_cost_multiplier": 1.0,
        },
        "balanced": {
            "label": "Balanced",
            "solar_buffer_percent": 7.0,
            "minimum_forecast_confidence_percent": 70,
            "curtailment_penalty_cents_kwh": 8.0,
            "wear_cost_multiplier": 1.15,
        },
        "resilience_first": {
            "label": "Resilience First",
            "solar_buffer_percent": 20.0,
            "minimum_forecast_confidence_percent": 80,
            "curtailment_penalty_cents_kwh": 3.0,
            "wear_cost_multiplier": 1.5,
        },
        "custom": {
            "label": "Custom",
            "solar_buffer_percent": planning.custom_solar_buffer_percent,
            "minimum_forecast_confidence_percent": planning.custom_min_forecast_confidence_percent,
            "curtailment_penalty_cents_kwh": planning.custom_curtailment_penalty_cents_kwh,
            "wear_cost_multiplier": planning.custom_wear_cost_multiplier,
        },
    }
    return {"key": planning.behavior_profile, **presets[planning.behavior_profile]}


def next_solar_window_policy(
    cfg: EnergyPilotConfig, slots: list[dict], pv_values: list[float],
    load_values: list[float], capacity: float, reserve_kwh: float,
    max_kwh: float, duration: float, efficiency: float,
    forecast_confidence: int,
) -> dict:
    """Find the next recharge window and value battery headroom before it.

    When planning starts during daylight, the current solar run is skipped so
    the anchor represents the following sunrise rather than the next 15-minute
    point. The target preserves the configured reserve and the selected profile
    buffer, but retains extra energy when forecast solar cannot refill the
    battery.
    """
    behavior = planner_behavior_settings(cfg.planning)
    threshold_kw = max(0.25, cfg.forecast_connector.solar_peak_kw * 0.02)
    active = [float(value or 0.0) >= threshold_kw for value in pv_values]
    search_from = 0
    if active and active[0]:
        while search_from < len(active) and active[search_from]:
            search_from += 1
    anchor_index = next(
        (index for index in range(search_from, len(active)) if active[index]),
        None,
    )
    window_end = anchor_index
    if anchor_index is not None:
        while window_end < len(active) and active[window_end]:
            window_end += 1
    forecast_eligible = (
        anchor_index is not None
        and forecast_confidence >= behavior["minimum_forecast_confidence_percent"]
    )
    expected_charge_kwh = 0.0
    if anchor_index is not None:
        expected_charge_kwh = sum(
            max(0.0, pv_values[index] - load_values[index]) * duration * efficiency
            for index in range(anchor_index, window_end)
        )
    configured_reserve_percent = max(
        cfg.battery_policy.min_operational_soc_percent,
        cfg.battery_policy.reserve_soc_percent,
    )
    base_target_kwh = capacity * min(
        cfg.battery_policy.max_planned_soc_percent,
        configured_reserve_percent + behavior["solar_buffer_percent"],
    ) / 100.0
    confidence_factor = max(0.0, min(1.0, forecast_confidence / 100.0))
    refill_target_kwh = max_kwh - expected_charge_kwh * confidence_factor
    target_kwh = max(reserve_kwh, min(max_kwh, max(base_target_kwh, refill_target_kwh)))
    anchor = parse_time(slots[anchor_index].get("start")) if anchor_index is not None else None
    anchor_label = (
        anchor.astimezone(ZoneInfo(cfg.site.timezone)).strftime("%d.%m %H:%M")
        if anchor else None
    )
    return {
        **behavior,
        "available": anchor_index is not None,
        "eligible": forecast_eligible,
        "anchor_index": anchor_index,
        "anchor_start": anchor.isoformat() if anchor else None,
        "anchor_label": anchor_label,
        "window_end_index": window_end,
        "threshold_kw": round(threshold_kw, 3),
        "expected_charge_kwh": round(expected_charge_kwh, 3),
        "capacity_kwh": round(capacity, 4),
        "target_kwh": round(target_kwh, 4),
        "target_soc_percent": round(target_kwh / capacity * 100.0, 1),
        "forecast_confidence_percent": forecast_confidence,
    }


def solar_headroom_cost(path: list[dict], solar_policy: Optional[dict]) -> float:
    if not solar_policy or not solar_policy.get("eligible"):
        return 0.0
    anchor_index = int(solar_policy.get("anchor_index") or 0)
    if anchor_index <= 0 or anchor_index > len(path):
        return 0.0
    anchor_state = path[anchor_index - 1]
    if anchor_state.get("after") is not None:
        energy_at_solar = float(anchor_state["after"])
    else:
        capacity = float(solar_policy.get("capacity_kwh") or 0.0)
        energy_at_solar = (
            capacity * float(anchor_state.get("soc_after_percent") or 0.0) / 100.0
        )
    excess_kwh = max(0.0, energy_at_solar - float(solar_policy["target_kwh"]))
    return excess_kwh * float(solar_policy["curtailment_penalty_cents_kwh"])


def _planner_v3_optimize(
    cfg: EnergyPilotConfig, slots: list[dict], pv_values: list[float],
    load_values: list[float], initial_energy: float, reserve_kwh: float,
    max_kwh: float, capacity: float, duration: float, efficiency: float,
    allow_grid_charging_with_pv: bool = True,
    solar_policy: Optional[dict] = None,
) -> dict:
    """Global dynamic-programming optimizer across the complete price horizon."""
    policy, grid_policy = cfg.battery_policy, cfg.grid_policy
    usable = max(0.0, max_kwh - reserve_kwh)
    step = max(0.1, usable / 100.0)
    levels = [reserve_kwh + step * index for index in range(int(usable / step) + 1)]
    if not levels or levels[-1] < max_kwh - 0.01:
        levels.append(max_kwh)
    levels.extend([initial_energy])
    levels = sorted(set(round(max(reserve_kwh, min(max_kwh, value)), 5) for value in levels))
    initial_index = min(range(len(levels)), key=lambda index: abs(levels[index] - initial_energy))
    import_prices = [float(slot["import_cents_kwh"]) for slot in slots if slot.get("import_cents_kwh") is not None]
    sorted_imports = sorted(import_prices)
    terminal_value = (
        sorted_imports[max(0, int(len(sorted_imports) * 0.25) - 1)] * efficiency
        if sorted_imports else 0.0
    )
    wear = policy.degradation_cost_cents_kwh
    future = [-energy * terminal_value for energy in levels]
    choices: list[list[Optional[int]]] = [[None] * len(levels) for _ in slots]

    for slot_index in range(len(slots) - 1, -1, -1):
        slot = slots[slot_index]
        import_price = float(slot.get("import_cents_kwh") or 0.0)
        export_price = float(slot.get("export_cents_kwh") or 0.0)
        base_grid_kwh = (load_values[slot_index] - pv_values[slot_index]) * duration
        current_costs = [math.inf] * len(levels)
        for before_index, before in enumerate(levels):
            best_cost, best_after_index = math.inf, None
            for after_index, after in enumerate(levels):
                delta = after - before
                if delta > policy.max_charge_kw * duration * efficiency + 1e-6:
                    continue
                if -delta > policy.max_discharge_kw * duration / efficiency + 1e-6:
                    continue
                if (
                    not allow_grid_charging_with_pv
                    and pv_values[slot_index] > load_values[slot_index]
                    and delta > (pv_values[slot_index] - load_values[slot_index]) * duration * efficiency + 1e-6
                ):
                    continue
                grid_kwh = base_grid_kwh + (delta / efficiency if delta >= 0 else delta * efficiency)
                if grid_kwh > grid_policy.max_import_kw * duration + 1e-6:
                    continue
                grid_import = max(0.0, grid_kwh)
                grid_export = min(grid_policy.max_export_kw * duration, max(0.0, -grid_kwh))
                cash_cost = grid_import * import_price - grid_export * export_price
                headroom_cost = 0.0
                if (
                    solar_policy
                    and solar_policy.get("eligible")
                    and slot_index == solar_policy.get("anchor_index", -1) - 1
                ):
                    excess_kwh = max(0.0, after - float(solar_policy["target_kwh"]))
                    headroom_cost = (
                        excess_kwh
                        * float(solar_policy["curtailment_penalty_cents_kwh"])
                    )
                transition_cost = cash_cost + abs(delta) * wear + headroom_cost + future[after_index]
                if transition_cost < best_cost:
                    best_cost, best_after_index = transition_cost, after_index
            current_costs[before_index] = best_cost
            choices[slot_index][before_index] = best_after_index
        future = current_costs

    path = []
    level_index = initial_index
    total_cash_cost = total_wear_cost = 0.0
    for slot_index, slot in enumerate(slots):
        after_index = choices[slot_index][level_index]
        if after_index is None:
            after_index = level_index
        before, after = levels[level_index], levels[after_index]
        delta = after - before
        base_grid_kwh = (load_values[slot_index] - pv_values[slot_index]) * duration
        grid_kwh = base_grid_kwh + (delta / efficiency if delta >= 0 else delta * efficiency)
        grid_import = max(0.0, grid_kwh)
        grid_export = min(grid_policy.max_export_kw * duration, max(0.0, -grid_kwh))
        cash_cost = (
            grid_import * float(slot.get("import_cents_kwh") or 0.0)
            - grid_export * float(slot.get("export_cents_kwh") or 0.0)
        )
        wear_cost = abs(delta) * wear
        total_cash_cost += cash_cost
        total_wear_cost += wear_cost
        path.append({
            "before": before, "after": after, "delta": delta,
            "grid_import_kwh": grid_import, "grid_export_kwh": grid_export,
            "cash_cost_cents": cash_cost, "wear_cost_cents": wear_cost,
        })
        level_index = after_index
    headroom_cost = solar_headroom_cost(path, solar_policy)
    return {
        "path": path, "cash_cost_cents": total_cash_cost, "wear_cost_cents": total_wear_cost,
        "final_energy_kwh": levels[level_index], "terminal_value_cents_kwh": terminal_value,
        "headroom_cost_cents": headroom_cost,
        "objective_cents": total_cash_cost + total_wear_cost + headroom_cost - levels[level_index] * terminal_value,
        "solar_policy": solar_policy or {},
    }


def _planner_v3_reconcile_grid_charging(
    cfg: EnergyPilotConfig, slots: list[dict], pv_values: list[float],
    load_values: list[float], optimized: dict, initial_energy: float,
    reserve_kwh: float, max_kwh: float, duration: float, efficiency: float,
    solar_policy: Optional[dict] = None,
) -> dict:
    """Remove grid charging caused only by discrete SOC levels.

    A grid charge survives only when a later import or export slot repays the
    current import price, round-trip loss and configured battery wear.
    """
    policy, grid_policy = cfg.battery_policy, cfg.grid_policy
    energy = initial_energy
    path = []
    total_cash_cost = total_wear_cost = 0.0
    minimum_gain = 0.25
    roundtrip = policy.roundtrip_efficiency_percent / 100.0
    for index, slot in enumerate(slots):
        desired_after = float(optimized["path"][index]["after"])
        delta = max(
            -policy.max_discharge_kw * duration / efficiency,
            min(policy.max_charge_kw * duration * efficiency, desired_after - energy),
        )
        pv_surplus_kwh = max(0.0, pv_values[index] - load_values[index]) * duration
        pv_storable_kwh = min(pv_surplus_kwh * efficiency, max(0.0, max_kwh - energy))
        requested_grid_charge_kwh = max(0.0, delta - pv_storable_kwh) / efficiency
        future_import = max(
            (float(candidate.get("import_cents_kwh") or 0.0) for candidate in slots[index + 1:]),
            default=0.0,
        )
        future_export = max(
            (float(candidate.get("export_cents_kwh") or 0.0) for candidate in slots[index + 1:]),
            default=0.0,
        )
        future_value = max(future_import, future_export) * roundtrip
        grid_charge_gain = (
            future_value
            - float(slot.get("import_cents_kwh") or 0.0)
            - policy.degradation_cost_cents_kwh * efficiency
        )
        grid_charge_justified = requested_grid_charge_kwh > 0.001 and grid_charge_gain >= minimum_gain
        if requested_grid_charge_kwh > 0.001 and not grid_charge_justified:
            delta = min(delta, pv_storable_kwh)
        after = max(reserve_kwh, min(max_kwh, energy + delta))
        delta = after - energy
        base_grid_kwh = (load_values[index] - pv_values[index]) * duration
        grid_kwh = base_grid_kwh + (delta / efficiency if delta >= 0 else delta * efficiency)
        grid_import = min(grid_policy.max_import_kw * duration, max(0.0, grid_kwh))
        grid_export = min(grid_policy.max_export_kw * duration, max(0.0, -grid_kwh))
        cash_cost = (
            grid_import * float(slot.get("import_cents_kwh") or 0.0)
            - grid_export * float(slot.get("export_cents_kwh") or 0.0)
        )
        wear_cost = abs(delta) * policy.degradation_cost_cents_kwh
        actual_grid_charge_kwh = max(0.0, delta - pv_storable_kwh) / efficiency
        path.append({
            "before": energy, "after": after, "delta": delta,
            "grid_import_kwh": grid_import, "grid_export_kwh": grid_export,
            "grid_charge_kwh": actual_grid_charge_kwh,
            "grid_charge_justified": actual_grid_charge_kwh > 0.001 and grid_charge_justified,
            "grid_charge_gain_cents_kwh": grid_charge_gain,
            "cash_cost_cents": cash_cost, "wear_cost_cents": wear_cost,
        })
        total_cash_cost += cash_cost
        total_wear_cost += wear_cost
        energy = after
    terminal_value = optimized["terminal_value_cents_kwh"]
    headroom_cost = solar_headroom_cost(
        path, solar_policy if solar_policy is not None else optimized.get("solar_policy")
    )
    return {
        **optimized,
        "path": path, "cash_cost_cents": total_cash_cost,
        "wear_cost_cents": total_wear_cost, "final_energy_kwh": energy,
        "headroom_cost_cents": headroom_cost,
        "objective_cents": total_cash_cost + total_wear_cost + headroom_cost - energy * terminal_value,
    }

def _planner_v3_reconcile_sell_profit(
    cfg: EnergyPilotConfig, slots: list[dict], pv_values: list[float],
    load_values: list[float], optimized: dict, initial_energy: float,
    reserve_kwh: float, max_kwh: float, duration: float, efficiency: float,
    solar_policy: Optional[dict] = None,
) -> dict:
    """Prevent uneconomic export while recognizing energy displaced by forecast PV."""
    policy, grid_policy = cfg.battery_policy, cfg.grid_policy
    terminal_value = float(optimized["terminal_value_cents_kwh"])
    minimum_profit = cfg.planning.minimum_sell_profit_cents_kwh
    energy = initial_energy
    path = []
    total_cash_cost = total_wear_cost = 0.0
    for index, slot in enumerate(slots):
        desired_after = float(optimized["path"][index]["after"])
        delta = max(
            -policy.max_discharge_kw * duration / efficiency,
            min(policy.max_charge_kw * duration * efficiency, desired_after - energy),
        )
        export_price = float(slot.get("export_cents_kwh") or 0.0)
        before_solar = bool(
            solar_policy
            and solar_policy.get("eligible")
            and index < int(solar_policy.get("anchor_index") or 0)
        )
        target_kwh = float((solar_policy or {}).get("target_kwh") or reserve_kwh)
        displaced_by_solar = before_solar and energy > target_kwh + 0.001
        stored_energy_value = 0.0 if displaced_by_solar else terminal_value
        sell_profit = export_price - (
            stored_energy_value + policy.degradation_cost_cents_kwh
        ) / efficiency
        base_grid_kwh = (load_values[index] - pv_values[index]) * duration
        projected_grid_kwh = base_grid_kwh + (
            delta / efficiency if delta >= 0 else delta * efficiency
        )
        natural_pv_export = max(0.0, -base_grid_kwh)
        projected_export = max(0.0, -projected_grid_kwh)
        battery_export = max(0.0, projected_export - natural_pv_export)
        sell_blocked = delta < 0 and battery_export > 0.001 and sell_profit < minimum_profit
        if sell_blocked:
            home_deficit_kwh = max(0.0, base_grid_kwh)
            delta = -min(-delta, home_deficit_kwh / efficiency)
        elif displaced_by_solar and delta < 0:
            # Forecast PV only lowers the opportunity value of energy above the
            # selected pre-solar target. Never let that rule breach the target.
            delta = max(delta, target_kwh - energy)
        after = max(reserve_kwh, min(max_kwh, energy + delta))
        delta = after - energy
        grid_kwh = base_grid_kwh + (delta / efficiency if delta >= 0 else delta * efficiency)
        grid_import = min(grid_policy.max_import_kw * duration, max(0.0, grid_kwh))
        grid_export = min(grid_policy.max_export_kw * duration, max(0.0, -grid_kwh))
        cash_cost = (
            grid_import * float(slot.get("import_cents_kwh") or 0.0)
            - grid_export * export_price
        )
        wear_cost = abs(delta) * policy.degradation_cost_cents_kwh
        previous = optimized["path"][index]
        path.append({
            **previous,
            "before": energy, "after": after, "delta": delta,
            "grid_import_kwh": grid_import, "grid_export_kwh": grid_export,
            "cash_cost_cents": cash_cost, "wear_cost_cents": wear_cost,
            "sell_profit_cents_kwh": sell_profit,
            "sell_blocked_by_minimum": sell_blocked,
            "stored_energy_value_cents_kwh": stored_energy_value,
            "solar_displaced_energy": displaced_by_solar,
        })
        total_cash_cost += cash_cost
        total_wear_cost += wear_cost
        energy = after
    headroom_cost = solar_headroom_cost(path, solar_policy)
    return {
        **optimized,
        "path": path, "cash_cost_cents": total_cash_cost,
        "wear_cost_cents": total_wear_cost, "final_energy_kwh": energy,
        "headroom_cost_cents": headroom_cost,
        "objective_cents": total_cash_cost + total_wear_cost + headroom_cost - energy * terminal_value,
    }


def battery_wear_model(cfg: EnergyPilotConfig, snapshot: dict) -> dict:
    policy = cfg.battery_policy
    capacity = snapshot.get("battery", {}).get("capacity_kwh", {}).get("value")
    used_cycles = snapshot.get("battery", {}).get("cycle_count", {}).get("value")
    allowed_cycles = policy.warranted_cycles
    remaining_cycles = (
        max(0.0, float(allowed_cycles) - float(used_cycles))
        if used_cycles is not None else None
    )
    automatic_available = bool(
        policy.system_cost_eur > 0
        and capacity is not None and capacity > 0
        and remaining_cycles is not None and remaining_cycles > 0
    )
    automatic_rate = (
        policy.system_cost_eur * 100.0 / (2.0 * capacity * remaining_cycles)
        if automatic_available else None
    )
    effective_rate = automatic_rate if automatic_rate is not None else policy.degradation_cost_cents_kwh
    return {
        "mode": "automatic_remaining_life" if automatic_rate is not None else "manual_fallback",
        "system_cost_eur": policy.system_cost_eur,
        "allowed_cycles": allowed_cycles,
        "used_cycles": round(used_cycles, 1) if used_cycles is not None else None,
        "remaining_cycles": round(remaining_cycles, 1) if remaining_cycles is not None else None,
        "capacity_kwh": capacity,
        "automatic_rate_cents_kwh": round(automatic_rate, 5) if automatic_rate is not None else None,
        "manual_rate_cents_kwh": policy.degradation_cost_cents_kwh,
        "effective_rate_cents_kwh": round(effective_rate, 5),
        "formula": "system cost × 100 / (2 × usable capacity × remaining cycles)",
        "error": (
            None if automatic_available
            else "Set system cost and cycle-count entity; remaining cycles and usable capacity must be above zero."
        ),
    }


def apply_manual_overrides(
    cfg: EnergyPilotConfig, slots: list[dict], capacity: float,
    initial_energy: float, reserve_kwh: float, max_kwh: float,
    duration: float, efficiency: float, wear_rate: float,
    overrides: Optional[dict] = None,
) -> dict:
    overrides = load_planner_overrides() if overrides is None else overrides
    energy = initial_energy
    policy = cfg.battery_policy
    for slot in slots:
        stamp = parse_time(slot.get("start"))
        key = stamp.isoformat() if stamp else ""
        override = overrides.get(key)
        pv_kw = float(slot.get("pv_forecast_kw") or 0.0)
        load_kw = float(slot.get("load_forecast_kw") or 0.0)
        base_grid_kwh = (load_kw - pv_kw) * duration
        action = slot.get("action") or "NORMAL"
        if override:
            action = override["action"]
            power = override.get("power_kw")
            target = override.get("target_soc_percent")
            target_kwh = capacity * target / 100.0 if target is not None else None
            pv_surplus = max(0.0, pv_kw - load_kw)
            deficit = max(0.0, load_kw - pv_kw)
            if action == "BUY":
                charge_kw = min(power if power is not None else policy.max_charge_kw, policy.max_charge_kw)
                delta = charge_kw * duration * efficiency
                if target_kwh is not None:
                    delta = min(delta, max(0.0, target_kwh - energy))
            elif action == "SELL":
                discharge_kw = min(power if power is not None else policy.max_discharge_kw, policy.max_discharge_kw)
                delta = -discharge_kw * duration / efficiency
                if target_kwh is not None:
                    delta = max(delta, min(0.0, target_kwh - energy))
            elif action == "SAVE BATTERY":
                delta = min(pv_surplus * duration * efficiency, max(0.0, max_kwh - energy))
            elif action == "PV SELL":
                delta = 0.0
            else:
                delta = (
                    min(pv_surplus * duration * efficiency, max(0.0, max_kwh - energy))
                    if pv_surplus > 0 else
                    -min(deficit * duration / efficiency, max(0.0, energy - reserve_kwh))
                )
            slot["manual_override"] = True
            slot["manual_power_kw"] = power
            slot["manual_target_soc_percent"] = target
            slot["action_reason"] = (
                f"Manual override: {action}"
                + (f" at {power:.2f} kW" if power is not None else "")
                + (f" to {target:.0f}% SOC" if target is not None else "")
                + "."
            )
        else:
            delta = (
                float(slot.get("charge_kw") or 0.0) * duration * efficiency
                - float(slot.get("discharge_kw") or 0.0) * duration / efficiency
            )
            slot["manual_override"] = False
        before = energy
        energy = max(reserve_kwh, min(max_kwh, energy + delta))
        delta = energy - before
        grid_kwh = base_grid_kwh + (delta / efficiency if delta >= 0 else delta * efficiency)
        grid_import = min(cfg.grid_policy.max_import_kw * duration, max(0.0, grid_kwh))
        grid_export_limit = 0.0 if action == "LIMIT EXPORT" else cfg.grid_policy.max_export_kw * duration
        grid_export = min(grid_export_limit, max(0.0, -grid_kwh))
        cash_cost = (
            grid_import * float(slot.get("import_cents_kwh") or 0.0)
            - grid_export * float(slot.get("export_cents_kwh") or 0.0)
        )
        slot.update({
            "action": action,
            "soc_before_percent": round(before / capacity * 100.0, 1),
            "soc_after_percent": round(energy / capacity * 100.0, 1),
            "charge_kw": round(max(0.0, delta) / duration / efficiency, 2),
            "discharge_kw": round(max(0.0, -delta) * efficiency / duration, 2),
            "grid_import_kw": round(grid_import / duration, 2),
            "grid_export_kw": round(grid_export / duration, 2),
            "slot_cash_cost_cents": round(cash_cost, 4),
            "slot_wear_cost_cents": round(abs(delta) * wear_rate, 6),
        })
    return {"count": sum(bool(slot.get("manual_override")) for slot in slots), "final_energy_kwh": energy}


def apply_slot_plan(
    cfg: EnergyPilotConfig, snapshot: dict, price: dict,
    overrides: Optional[dict] = None,
) -> None:
    """Planner v3: optimize battery value over the complete horizon."""
    slots = price.get("slots", [])
    battery = snapshot["battery"]
    capacity = battery["capacity_kwh"].get("value")
    initial_soc = battery["soc_pct"].get("value")
    if not slots or capacity is None or initial_soc is None:
        price["plan"] = {"version": 3, "available": False, "reason": "Battery capacity, SOC and future price slots are required."}
        return
    fc = cfg.forecast_connector
    pv_values, pv_status = weather_adjusted_solar_points(cfg, slots)
    if not pv_status["available"] and fc.pv_forecast_entity:
        pv_values, manual_status = forecast_points(fc.pv_forecast_entity, slots, "pv")
        if manual_status["available"]:
            pv_status = manual_status
            pv_status["mode"] = "manual_entity"
    load_values, load_status = forecast_points(fc.load_forecast_entity, slots, "load")
    if not load_status["available"]:
        learned_values, learned_status = learned_load_points(cfg, slots)
        if learned_status["available"]:
            load_values, load_status = learned_values, learned_status
    live_load = snapshot["load"]["power_kw"].get("value")
    fallback_load = fc.fallback_load_kw if fc.fallback_load_kw is not None else live_load
    load_values = [value if value is not None else max(0.0, fallback_load or 0.0) for value in load_values]
    pv_raw_values = list(pv_values)
    pv_calibration = [pv_learning_factor(cfg, parse_time(slot.get("start"))) for slot in slots]
    pv_values = [
        value * pv_calibration[index][0] if value is not None else 0.0
        for index, value in enumerate(pv_values)
    ]
    pv_status["calibration_samples"] = sum(samples for _, samples in pv_calibration)
    pv_status["average_calibration_factor"] = round(
        sum(factor for factor, _ in pv_calibration) / len(pv_calibration), 3
    ) if pv_calibration else 1.0
    policy = cfg.battery_policy
    wear_model = battery_wear_model(cfg, snapshot)
    optimizer_cfg = cfg.model_copy(deep=True)
    behavior = planner_behavior_settings(cfg.planning)
    optimizer_cfg.battery_policy.degradation_cost_cents_kwh = (
        wear_model["effective_rate_cents_kwh"] * behavior["wear_cost_multiplier"]
    )
    policy = optimizer_cfg.battery_policy
    reserve_kwh = capacity * policy.reserve_soc_percent / 100.0
    max_kwh = capacity * policy.max_planned_soc_percent / 100.0
    initial_energy = max(reserve_kwh, min(max_kwh, capacity * initial_soc / 100.0))
    efficiency = (policy.roundtrip_efficiency_percent / 100.0) ** 0.5
    duration = cfg.planning.slot_minutes / 60.0
    forecast_confidence = min(
        98,
        (72 if pv_status["available"] else 35)
        + min(14, int(pv_status["calibration_samples"]) * 2)
        + (10 if load_status.get("available") else 0),
    )
    solar_policy = next_solar_window_policy(
        optimizer_cfg, slots, pv_values, load_values, capacity, reserve_kwh,
        max_kwh, duration, efficiency, forecast_confidence,
    )
    optimized = _planner_v3_optimize(
        optimizer_cfg, slots, pv_values, load_values, initial_energy, reserve_kwh,
        max_kwh, capacity, duration, efficiency,
        solar_policy=solar_policy,
    )
    pv_only_optimized = _planner_v3_optimize(
        optimizer_cfg, slots, pv_values, load_values, initial_energy, reserve_kwh,
        max_kwh, capacity, duration, efficiency,
        allow_grid_charging_with_pv=False,
        solar_policy=solar_policy,
    )
    midday_grid_buy_advantage = pv_only_optimized["objective_cents"] - optimized["objective_cents"]
    if midday_grid_buy_advantage < 2.0:
        optimized = pv_only_optimized
    optimized = _planner_v3_reconcile_grid_charging(
        optimizer_cfg, slots, pv_values, load_values, optimized, initial_energy,
        reserve_kwh, max_kwh, duration, efficiency, solar_policy,
    )
    optimized = _planner_v3_reconcile_sell_profit(
        optimizer_cfg, slots, pv_values, load_values, optimized, initial_energy,
        reserve_kwh, max_kwh, duration, efficiency, solar_policy,
    )
    baseline = _planner_v3_normal_baseline(
        slots, pv_values, load_values, initial_energy, reserve_kwh, max_kwh,
        duration, efficiency, policy, optimizer_cfg.grid_policy,
    )
    baseline_headroom_cost = solar_headroom_cost(baseline["path"], solar_policy)
    baseline_objective = (
        baseline["cash_cost_cents"]
        + baseline["wear_cost_cents"]
        + baseline_headroom_cost
        - baseline["final_energy_kwh"] * optimized["terminal_value_cents_kwh"]
    )
    projected_savings = baseline_objective - optimized["objective_cents"]
    minimum_horizon_gain_cents = cfg.planning.minimum_horizon_gain_eur * 100.0
    used_normal_fallback = projected_savings < minimum_horizon_gain_cents
    if used_normal_fallback:
        optimized = {
            **optimized,
            "path": baseline["path"],
            "cash_cost_cents": baseline["cash_cost_cents"],
            "wear_cost_cents": baseline["wear_cost_cents"],
            "final_energy_kwh": baseline["final_energy_kwh"],
            "headroom_cost_cents": baseline_headroom_cost,
            "objective_cents": baseline_objective,
        }
        projected_savings = 0.0
    for index, slot in enumerate(slots):
        result = optimized["path"][index]
        delta = result["delta"]
        pv_kw, load_kw = pv_values[index], load_values[index]
        import_price = float(slot.get("import_cents_kwh") or 0.0)
        export_price = float(slot.get("export_cents_kwh") or 0.0)
        charge_input_kwh = max(0.0, delta) / efficiency
        grid_charge_kwh = float(result.get("grid_charge_kwh") or 0.0)
        if export_price <= 0 and result["grid_export_kwh"] > 0.001:
            action = "LIMIT EXPORT"
            if delta > 0.05:
                reason = (
                    f"Solar charges the battery by {charge_input_kwh:.2f} kWh, but export is worth "
                    f"{export_price:.2f} c/kWh. Limit the remaining unprofitable grid export."
                )
            else:
                reason = (
                    f"Export price is {export_price:.2f} c/kWh, so prevent unprofitable grid export."
                )
        elif delta > 0.05:
            if grid_charge_kwh > 0.001:
                action = "BUY"
                reason = (
                    f"Buy {grid_charge_kwh:.2f} kWh from the grid in this slot. The estimated later "
                    f"value exceeds the current {import_price:.2f} c/kWh import cost, losses and wear "
                    f"by {result.get('grid_charge_gain_cents_kwh', 0.0):.2f} c/kWh."
                )
            else:
                action = "NORMAL"
                reason = (
                    f"Solar surplus supplies this {charge_input_kwh:.2f} kWh battery charge. "
                    "No grid-buy command is planned."
                )
        elif delta < -0.05 and result["grid_export_kwh"] > 0.05:
            action = "SELL"
            if result.get("solar_displaced_energy"):
                reason = (
                    f"Release {-delta:.2f} kWh at {export_price:.2f} c/kWh before the next solar "
                    f"window. {solar_policy['label']} targets {solar_policy['target_soc_percent']:.1f}% "
                    f"SOC by {solar_policy['anchor_label']} so forecast solar has room to charge."
                )
            else:
                reason = (
                    f"Release {-delta:.2f} kWh in this slot at {export_price:.2f} c/kWh; "
                    "the optimizer has already reserved energy for later home demand and higher-value slots."
                )
        elif delta < -0.05:
            action = "NORMAL"
            reason = f"Use {-delta:.2f} kWh from the battery to cover home demand instead of importing at {import_price:.2f} c/kWh."
        elif pv_kw > load_kw and result["grid_export_kwh"] > 0.05:
            action = "PV SELL"
            reason = "Solar covers home demand and the optimized battery trajectory leaves the remaining surplus for export."
        elif load_kw > pv_kw and result["grid_import_kwh"] > 0.05 and result["before"] > reserve_kwh + 0.05:
            action = "SAVE BATTERY"
            reason = (
                f"Preserve the battery in this slot because its later horizon value exceeds the "
                f"{import_price:.2f} c/kWh import cost."
            )
        else:
            action = "NORMAL"
            reason = "Normal balancing follows the globally optimized battery trajectory in this slot."
        slot.update({
            "action": action, "action_reason": reason,
            "pv_forecast_kw": round(pv_kw, 3) if pv_raw_values[index] is not None else None,
            "pv_raw_forecast_kw": round(pv_raw_values[index], 3) if pv_raw_values[index] is not None else None,
            "pv_calibration_factor": round(pv_calibration[index][0], 3),
            "load_forecast_kw": round(load_kw, 3),
            "soc_before_percent": round(result["before"] / capacity * 100.0, 1),
            "soc_after_percent": round(result["after"] / capacity * 100.0, 1),
            "charge_kw": round(max(0.0, delta) / duration / efficiency, 2),
            "discharge_kw": round(max(0.0, -delta) * efficiency / duration, 2),
            "grid_import_kw": round(result["grid_import_kwh"] / duration, 2),
            "grid_export_kw": round(result["grid_export_kwh"] / duration, 2),
            "grid_charge_kwh": round(grid_charge_kwh, 4),
            "grid_charge_kw": round(grid_charge_kwh / duration, 3),
            "grid_charge_gain_cents_kwh": round(float(result.get("grid_charge_gain_cents_kwh") or 0.0), 3),
            "slot_cash_cost_cents": round(result["cash_cost_cents"], 3),
            "slot_wear_cost_cents": round(result["wear_cost_cents"], 6),
            "wear_rate_cents_kwh": policy.degradation_cost_cents_kwh,
            "forecast_confidence": min(
                forecast_confidence,
                98,
            ),
        })
    override_result = apply_manual_overrides(
        optimizer_cfg, slots, capacity, initial_energy, reserve_kwh, max_kwh,
        duration, efficiency, policy.degradation_cost_cents_kwh, overrides,
    )
    summaries = {}
    for slot in slots:
        stamp = parse_time(slot.get("start"))
        day = stamp.astimezone(ZoneInfo(cfg.site.timezone)).date().isoformat() if stamp else "unknown"
        summary = summaries.setdefault(day, {
            "date": day, "pv_kwh": 0.0, "load_kwh": 0.0, "grid_import_kwh": 0.0,
            "grid_export_kwh": 0.0, "cost_cents": 0.0, "min_soc_percent": 100.0,
            "max_soc_percent": 0.0, "actions": {},
        })
        summary["pv_kwh"] += float(slot.get("pv_forecast_kw") or 0.0) * duration
        summary["load_kwh"] += float(slot.get("load_forecast_kw") or 0.0) * duration
        summary["grid_import_kwh"] += float(slot.get("grid_import_kw") or 0.0) * duration
        summary["grid_export_kwh"] += float(slot.get("grid_export_kw") or 0.0) * duration
        summary["cost_cents"] += float(slot.get("slot_cash_cost_cents") or 0.0)
        summary["min_soc_percent"] = min(summary["min_soc_percent"], float(slot.get("soc_after_percent") or 0.0))
        summary["max_soc_percent"] = max(summary["max_soc_percent"], float(slot.get("soc_before_percent") or 0.0))
        action_name = slot.get("action") or "NORMAL"
        summary["actions"][action_name] = summary["actions"].get(action_name, 0) + 1
    daily_summary = []
    for summary in summaries.values():
        for key in ("pv_kwh", "load_kwh", "grid_import_kwh", "grid_export_kwh", "cost_cents", "min_soc_percent", "max_soc_percent"):
            summary[key] = round(summary[key], 1)
        daily_summary.append(summary)
    today_key = datetime.now(ZoneInfo(cfg.site.timezone)).date().isoformat()
    today_slots = []
    for slot in slots:
        stamp = parse_time(slot.get("start"))
        if stamp and stamp.astimezone(ZoneInfo(cfg.site.timezone)).date().isoformat() == today_key:
            today_slots.append(slot)
    today_import_cost = sum(
        float(slot.get("grid_import_kw") or 0.0) * duration * float(slot.get("import_cents_kwh") or 0.0)
        for slot in today_slots
    )
    today_export_revenue = sum(
        float(slot.get("grid_export_kw") or 0.0) * duration * float(slot.get("export_cents_kwh") or 0.0)
        for slot in today_slots
    )
    today_wear = sum(float(slot.get("slot_wear_cost_cents") or 0.0) for slot in today_slots)
    today_opening_energy = (
        capacity * float(today_slots[0].get("soc_before_percent") or 0.0) / 100.0
        if today_slots else initial_energy
    )
    today_closing_energy = (
        capacity * float(today_slots[-1].get("soc_after_percent") or 0.0) / 100.0
        if today_slots else initial_energy
    )
    today_inventory_change = (
        (today_closing_energy - today_opening_energy)
        * float(optimized.get("terminal_value_cents_kwh") or 0.0)
    )
    today_operating_result = today_export_revenue - today_import_cost - today_wear
    today_financial = {
        "date": today_key,
        "export_revenue_cents": round(today_export_revenue, 2),
        "import_cost_cents": round(today_import_cost, 2),
        "wear_cost_cents": round(today_wear, 2),
        "stored_energy_value_change_cents": round(today_inventory_change, 2),
        "operating_result_cents": round(today_operating_result, 2),
        "net_cents": round(today_operating_result + today_inventory_change, 2),
        "negative_price_protection_slots": sum(
            (slot.get("action") or "") == "LIMIT EXPORT" for slot in today_slots
        ),
        "arbitrage_slots": sum(
            (slot.get("action") or "") in {"BUY", "SELL", "SAVE BATTERY"} for slot in today_slots
        ),
        "slot_count": len(today_slots),
        "manual_override_count": sum(bool(slot.get("manual_override")) for slot in today_slots),
    }
    horizon_import_cost = sum(
        float(slot.get("grid_import_kw") or 0.0) * duration * float(slot.get("import_cents_kwh") or 0.0)
        for slot in slots
    )
    horizon_export_revenue = sum(
        float(slot.get("grid_export_kw") or 0.0) * duration * float(slot.get("export_cents_kwh") or 0.0)
        for slot in slots
    )
    horizon_wear = sum(float(slot.get("slot_wear_cost_cents") or 0.0) for slot in slots)
    terminal_value = float(optimized.get("terminal_value_cents_kwh") or 0.0)
    final_energy = float(override_result["final_energy_kwh"])
    horizon_inventory_change = (final_energy - initial_energy) * terminal_value
    horizon_operating_result = horizon_export_revenue - horizon_import_cost - horizon_wear
    horizon_headroom_cost = solar_headroom_cost(slots, solar_policy)
    horizon_cash_and_inventory_result = horizon_operating_result + horizon_inventory_change
    horizon_financial = {
        "export_revenue_cents": round(horizon_export_revenue, 2),
        "import_cost_cents": round(horizon_import_cost, 2),
        "cash_cost_cents": round(horizon_import_cost - horizon_export_revenue, 2),
        "wear_cost_cents": round(horizon_wear, 2),
        "stored_energy_value_change_cents": round(horizon_inventory_change, 2),
        "solar_headroom_cost_cents": round(horizon_headroom_cost, 2),
        "operating_result_cents": round(horizon_operating_result, 2),
        "cash_and_inventory_result_cents": round(horizon_cash_and_inventory_result, 2),
        "net_cents": round(horizon_cash_and_inventory_result - horizon_headroom_cost, 2),
        "initial_soc_percent": round(initial_energy / capacity * 100.0, 1),
        "final_soc_percent": round(final_energy / capacity * 100.0, 1),
        "slot_count": len(slots),
        "manual_override_count": override_result["count"],
    }
    normal_inventory_change = (baseline["final_energy_kwh"] - initial_energy) * terminal_value
    normal_horizon_net = (
        -baseline["cash_cost_cents"]
        - baseline["wear_cost_cents"]
        + normal_inventory_change
        - baseline_headroom_cost
    )
    actual_horizon_gain = horizon_financial["net_cents"] - normal_horizon_net
    price["plan"] = {
        "version": 3, "available": True,
        "quality": "forecast" if pv_status["available"] else "price_and_live_load",
        "optimizer": "global_dynamic_programming", "pv_forecast": pv_status,
        "behavior": solar_policy,
        "load_forecast": load_status,
        "load_fallback_kw": round(fallback_load, 3) if fallback_load is not None else None,
        "initial_soc_percent": round(initial_soc, 1),
        "final_soc_percent": round(override_result["final_energy_kwh"] / capacity * 100.0, 1),
        "normal_final_soc_percent": round(baseline["final_energy_kwh"] / capacity * 100.0, 1),
        "projected_cash_cost_cents": horizon_financial["cash_cost_cents"],
        "normal_cash_cost_cents": round(baseline["cash_cost_cents"], 2),
        "normal_battery_wear_cost_cents": round(baseline["wear_cost_cents"], 2),
        "projected_savings_cents": round(actual_horizon_gain, 2),
        "normal_fallback_used": used_normal_fallback,
        "minimum_sell_profit_cents_kwh": cfg.planning.minimum_sell_profit_cents_kwh,
        "minimum_horizon_gain_eur": cfg.planning.minimum_horizon_gain_eur,
        "battery_wear_cost_cents": horizon_financial["wear_cost_cents"],
        "battery_wear_rate_cents_kwh": policy.degradation_cost_cents_kwh,
        "battery_wear_base_rate_cents_kwh": wear_model["effective_rate_cents_kwh"],
        "battery_wear": wear_model,
        "manual_override_count": override_result["count"],
        "today_financial": today_financial,
        "horizon_financial": horizon_financial,
        "terminal_energy_value_cents_kwh": round(optimized["terminal_value_cents_kwh"], 3),
        "solar_headroom_cost_cents": round(float(optimized.get("headroom_cost_cents") or 0.0), 2),
        "normal_solar_headroom_cost_cents": round(baseline_headroom_cost, 2),
        "midday_grid_buy_advantage_cents": round(midday_grid_buy_advantage, 3),
        "midday_grid_buy_minimum_cents": 2.0,
        "midday_grid_buy_allowed": midday_grid_buy_advantage >= 2.0,
        "buy_enabled": pv_status["available"], "daily_summary": daily_summary,
    }


def planner_snapshot(
    cfg: EnergyPilotConfig,
    snapshot: dict,
    price: Optional[dict] = None,
    qilowatt: Optional[dict] = None,
):
    """Transparent, flow-aware recommendation layer. Never controls hardware."""
    health_data = snapshot["health"]
    health = health_data["status"]
    battery = snapshot["battery"]
    flow = snapshot["flow"]
    soc = battery["soc_pct"].get("value")
    reserve = cfg.battery_policy.reserve_soc_percent
    mode = cfg.runtime.mode
    strategy = cfg.planning.strategy
    behavior = planner_behavior_settings(cfg.planning)
    current_price = (price or {}).get("current_cents_kwh")
    price_kind = (price or {}).get("planner_price_kind", "market")
    percentile = (price or {}).get("percentile")

    critical_missing = len(health_data.get("critical_missing", []))
    critical_stale = len(health_data.get("critical_stale", []))
    price_available = bool((price or {}).get("available"))
    price_stale = bool((price or {}).get("stale"))
    data_score = 100 - critical_missing * 18 - critical_stale * 12
    if strategy in ("export_value", "cost_minimization"):
        if not price_available:
            data_score -= 18
        elif price_stale:
            data_score -= 10
    data_score = max(10, min(98, data_score))

    if health == "unavailable":
        action, tone, reason, base_confidence = "NORMAL", "critical", "Live power measurements are unavailable, so no exceptional inverter action can be recommended.", 10
    elif soc is not None and soc <= reserve:
        action, tone, base_confidence = "SAVE BATTERY", "warning", 96
        reason = f"Battery is at {soc:.0f}%, close to the {reserve:.0f}% reserve target; cover demand from the grid and preserve the battery."
    elif (price or {}).get("plan", {}).get("available") and (price or {}).get("slots"):
        planned = price["slots"][0]
        action = planned.get("action", "NORMAL")
        savings = float((price or {}).get("plan", {}).get("projected_savings_cents") or 0.0)
        slot_label = "current slot" if planned.get("is_current") else "next slot"
        reason = f"Planner v3 {slot_label}: {planned.get('action_reason', 'globally optimized horizon plan')} Projected SOC {planned.get('soc_before_percent', soc):.0f}% → {planned.get('soc_after_percent', soc):.0f}%. Horizon value improvement versus NORMAL: {savings / 100:.2f} €."
        tone = {"SELL": "accent", "PV SELL": "accent", "BUY": "positive", "SAVE BATTERY": "positive", "LIMIT EXPORT": "warning", "NORMAL": "neutral"}[action]
        base_confidence = 94 if price["plan"].get("quality") == "forecast" else 84
    elif price_available:
        action, reason = energy_action(
            (price or {}).get("import_cents_kwh"),
            (price or {}).get("export_cents_kwh"),
            (price or {}).get("import_percentile"),
            (price or {}).get("export_percentile"),
            soc,
            reserve,
            cfg.battery_policy.max_planned_soc_percent,
            flow["pv"] == "producing",
        )
        tone = {"SELL": "accent", "PV SELL": "accent", "BUY": "positive", "SAVE BATTERY": "positive", "LIMIT EXPORT": "warning", "NORMAL": "neutral"}[action]
        base_confidence = 96 if action in ("SELL", "LIMIT EXPORT") else 92
    elif strategy == "export_value" and not price_available:
        action, tone, base_confidence = "NORMAL", "neutral", 76
        reason = "Live power data is usable, but market price data is unavailable. Continue normal operation until price intelligence returns."
    else:
        action, tone, base_confidence = "NORMAL", "neutral", 82
        reason = "Continue normal inverter balancing while respecting battery reserve and grid limits."

    external = qilowatt or {}
    if (
        external.get("integration_mode") == "ha_dispatch"
        and external.get("connected")
        and external.get("action")
    ):
        action = external["action"]
        tone = {
            "SELL": "accent", "PV SELL": "accent", "BUY": "positive",
            "SAVE BATTERY": "warning", "LIMIT EXPORT": "warning", "NORMAL": "neutral",
        }[action]
        source_label = str(external.get("source_raw") or external.get("source") or "Qilowatt")
        priority_label = "mandatory mFRR dispatch" if external.get("mandatory") else "external dispatch"
        limit = external.get("power_limit_kw")
        limit_text = f" Power limit: {limit:g} kW." if limit is not None else ""
        reason = (
            f"Qilowatt {priority_label} from {source_label}: {action}.{limit_text} "
            "Energy Pilot keeps its own plan visible for comparison but does not override this command."
        )
        base_confidence = 98 if external.get("mandatory") else 94

    confidence = int(round(min(base_confidence, data_score)))
    if health == "degraded":
        reason += " Recommendation confidence is reduced because one or more live measurements need attention."
    if external.get("integration_mode") == "ha_dispatch" and external.get("connected"):
        execution = "Qilowatt HA/MQTT dispatch"
    else:
        execution = {"simulation": "Simulation only", "approval": "Approval required", "live": "Live control enabled"}[mode]
    return {
        "action": action, "tone": tone, "reason": reason, "execution": execution,
        "confidence": confidence, "strategy": strategy,
        "behavior_profile": behavior["key"], "behavior_label": behavior["label"], "mode": mode,
        "flow_context": flow["summary"],
        "price_context": {"current_cents_kwh": current_price, "kind": price_kind, "percentile": percentile},
        "external_dispatch": external,
        "quality": {"score": data_score, "critical_missing": critical_missing, "critical_stale": critical_stale, "price_available": price_available, "price_stale": price_stale},
    }


def overview_metrics(snapshot: dict):
    """Derived, user-facing balance metrics for the Overview."""
    flow = snapshot["flow"]
    load = snapshot["load"]["power_kw"].get("value") or 0.0
    routes = flow.get("routes", [])
    supplied = {"pv": 0.0, "battery": 0.0, "grid": 0.0}
    for route in routes:
        if route.get("to") == "load" and route.get("from") in supplied:
            supplied[route["from"]] += float(route.get("power_kw") or 0.0)
    denominator = load if load > 0 else sum(supplied.values())
    shares = {key: round((value / denominator * 100.0) if denominator else 0.0, 1) for key, value in supplied.items()}
    # Derive autonomy from the reconciled grid contribution so any measured import
    # remains visible even when asynchronous meters temporarily over-report supply.
    grid_dependency = max(0.0, min(100.0, shares["grid"]))
    autonomy = round(100.0 - grid_dependency, 1) if denominator else 100.0
    return {
        "home_supply_kw": {k: round(v, 3) for k, v in supplied.items()},
        "home_supply_percent": shares,
        "autonomy_percent": autonomy,
        "grid_dependency_percent": round(grid_dependency, 1),
    }


def pv_curtailment_notification(
    cfg: EnergyPilotConfig,
    snapshot: dict,
    price: dict,
) -> dict:
    """Warn before the next user-actionable full-battery solar curtailment event."""
    slots = price.get("slots") or []
    current_index = next(
        (index for index, item in enumerate(slots) if item.get("is_current")),
        None,
    )
    if current_index is not None:
        slot = slots[current_index + 1] if current_index + 1 < len(slots) else None
    else:
        now = datetime.now(timezone.utc)
        slot = next(
            (
                item for item in slots
                if (parse_time(item.get("start")) or now) > now
            ),
            None,
        )

    live_soc = snapshot.get("battery", {}).get("soc_pct", {}).get("value")
    live_pv = snapshot.get("pv", {}).get("power_kw", {}).get("value")
    live_load = snapshot.get("load", {}).get("power_kw", {}).get("value")
    action = str((slot or {}).get("action") or "")
    soc = (slot or {}).get("soc_after_percent")
    if soc is None:
        soc = live_soc
    pv_kw = (slot or {}).get("pv_forecast_kw")
    if pv_kw is None:
        pv_kw = live_pv
    load_kw = (slot or {}).get("load_forecast_kw")
    if load_kw is None:
        load_kw = live_load
    export_price = (slot or {}).get("export_cents_kwh")
    if export_price is None:
        export_price = price.get("export_cents_kwh")

    full_threshold = max(95.0, cfg.battery_policy.max_planned_soc_percent - 1.0)
    surplus_kw = max(0.0, float(pv_kw or 0.0) - float(load_kw or 0.0))
    active = bool(
        slot
        and action == "LIMIT EXPORT"
        and soc is not None
        and float(soc) >= full_threshold
        and surplus_kw >= 0.25
    )

    slot_start = str((slot or {}).get("start") or "")
    slot_end = str((slot or {}).get("end") or "")
    start_at = parse_time(slot_start)
    end_at = parse_time(slot_end)
    slot_key = re.sub(r"[^0-9]", "", slot_start) or datetime.now(timezone.utc).strftime("%Y%m%d%H%M")
    slot_label = "the next 15-minute slot"
    if start_at:
        local_zone = ZoneInfo(cfg.site.timezone)
        local_start = start_at.astimezone(local_zone)
        local_end = (end_at or (start_at + timedelta(minutes=15))).astimezone(local_zone)
        slot_label = f"{local_start:%H:%M}–{local_end:%H:%M}"
    body = None
    if active:
        price_text = (
            f"{float(export_price):.2f} c/kWh"
            if export_price is not None
            else "too low"
        )
        body = (
            f"At {slot_label}, the battery is forecast to be {float(soc):.0f}% full "
            f"and export is worth {price_text}. About {surplus_kw:.1f} kW of solar may be curtailed. "
            "Consider charging the car, heating the sauna or starting another flexible load."
        )

    return {
        "active": active,
        "id": f"pv-curtailment-{slot_key}",
        "type": "pv_curtailment",
        "title": "Solar production may be limited",
        "body": body,
        "slot_start": slot_start or None,
        "slot_end": slot_end or None,
        "slot_label": slot_label if slot else None,
        "action": action or None,
        "battery_soc_percent": round(float(soc), 1) if soc is not None else None,
        "effective_export_cents_kwh": round(float(export_price), 3) if export_price is not None else None,
        "available_surplus_kw": round(surplus_kw, 2),
    }

SETUP_ENTITY_PATHS = {
    "pv_power": ("power_connector", "pv_power_entity"),
    "load_power": ("power_connector", "load_power_entity"),
    "grid_power": ("power_connector", "grid_power_entity"),
    "battery_power": ("battery_connector", "power_entity"),
    "battery_soc": ("battery_connector", "soc_entity"),
    "battery_capacity": ("battery_connector", "capacity_entity"),
    "battery_cycles": ("battery_connector", "cycle_count_entity"),
    "battery_temperature": ("battery_connector", "temperature_entity"),
    "inverter_temperature": ("power_connector", "inverter_temperature_entity"),
}

def public_connector_candidates(kind: str, states: list[dict]) -> list[dict]:
    result = []
    candidates = connector_candidates(kind, states)
    for index, candidate in enumerate(candidates[:12]):
        clean = {key: value for key, value in candidate.items() if key != "_item"}
        clean["recommended"] = index == 0
        clean["ambiguous"] = bool(
            index == 0 and len(candidates) > 1
            and candidates[1]["score"] >= candidate["score"] - 2
        )
        result.append(clean)
    return result

def setup_discovery(cfg: EnergyPilotConfig, force: bool = False) -> dict:
    states = ha_states(force=force)
    if force:
        WEATHER_FORECAST_CACHE.update({
            "fetched_monotonic": 0.0, "result": None, "error": None,
        })
    entities = {
        kind: public_connector_candidates(kind, states)
        for kind in SETUP_ENTITY_PATHS
    }
    price_candidates = []
    for raw in states:
        entity_id = str(raw.get("entity_id") or "")
        if not entity_id.startswith("sensor."):
            continue
        item = normalize_ha_item(raw)
        attrs = item.get("attributes") or {}
        raw_today = attrs.get("raw_today")
        raw_tomorrow = attrs.get("raw_tomorrow")
        today = attrs.get("today")
        tomorrow = attrs.get("tomorrow")
        compatible, slot_count_today, has_timestamps = price_item_compatibility(item)
        text = f"{entity_id} {attrs.get('friendly_name', '')}".lower()
        if "nordpool" not in text and "nord_pool" not in text and not has_timestamps:
            continue
        score = (20 if compatible else 0) + (8 if has_timestamps else 0)
        if "nordpool" in text or "nord_pool" in text:
            score += 8
        price_candidates.append({
            "entity_id": entity_id,
            "friendly_name": str(attrs.get("friendly_name") or entity_id),
            "unit": str(attrs.get("unit_of_measurement") or ""),
            "source_vat_percent": nordpool_entity_vat_percent(entity_id),
            "score": score,
            "compatible": compatible,
            "has_timestamped_slots": has_timestamps,
            "slot_count_today": slot_count_today,
            "slot_count_tomorrow": len(raw_tomorrow or tomorrow or []),
        })
    price_candidates.sort(key=lambda candidate: (-candidate["score"], candidate["entity_id"]))
    if price_candidates:
        price_candidates[0]["recommended"] = bool(price_candidates[0]["compatible"])

    weather = weather_entities(cfg.forecast_connector.weather_entity)
    weather_probe, weather_probe_error = ha_hourly_weather_forecast(
        cfg.forecast_connector.weather_entity,
    )
    compatible_weather_entity = (weather_probe or {}).get("entity_id")
    if compatible_weather_entity:
        weather.sort(key=lambda item: item.get("entity_id") != compatible_weather_entity)
    weather_candidates = [{
        "entity_id": item["entity_id"],
        "friendly_name": str((item.get("attributes") or {}).get("friendly_name") or item["entity_id"]),
        "condition": item.get("state"),
        "hourly_forecast_verified": item["entity_id"] == compatible_weather_entity,
    } for item in weather[:10]]
    location, location_error = ha_core_config()
    compatible_prices = [candidate for candidate in price_candidates if candidate["compatible"]]
    hacs_detected = bool(compatible_prices) or any(
        "hacs" in str(raw.get("entity_id") or "").lower()
        or "hacs" in str((raw.get("attributes") or {}).get("friendly_name") or "").lower()
        for raw in states
    )
    required_kinds = ("pv_power", "load_power", "grid_power", "battery_power", "battery_soc")
    unresolved = [
        kind for kind in required_kinds
        if not entities.get(kind)
    ]
    ambiguous = [
        kind for kind in required_kinds
        if entities.get(kind) and entities[kind][0].get("ambiguous")
    ]
    qilowatt = qilowatt_snapshot(cfg, states)
    return {
        "setup": cfg.setup.model_dump(),
        "home_assistant": {
            "connected": bool(SUPERVISOR_TOKEN and states),
            "entity_count": len(states),
            "location_available": bool(location),
            "location_error": location_error,
            "timezone": (location or {}).get("time_zone"),
            "country": (location or {}).get("country"),
            "currency": (location or {}).get("currency"),
        },
        "dependencies": {
            "hacs": {
                "status": "detected" if hacs_detected else "not_detected",
                "required_for": "HACS Nord Pool custom integration",
                "url": "https://www.hacs.xyz/docs/use/download/download/",
            },
            "nordpool_hacs": {
                "status": "ready" if compatible_prices else ("incompatible" if price_candidates else "missing"),
                "required_attributes": ["raw_today", "raw_tomorrow", "current_price"],
                "repository": "https://github.com/custom-components/nordpool",
                "note": "Energy Pilot needs the HACS Nord Pool sensor with timestamped today and tomorrow price slots.",
            },
            "weather": {
                "status": "ready" if compatible_weather_entity else ("incompatible" if weather_candidates else "missing"),
                "note": (
                    f"Hourly forecasts verified from {compatible_weather_entity}."
                    if compatible_weather_entity
                    else weather_probe_error or "Add a Home Assistant weather integration that provides hourly forecasts."
                ),
            },
            "qilowatt_ha": {
                "status": (
                    "ready" if qilowatt["entities_found"] == 4
                    else "detected" if qilowatt["entities_found"]
                    else "optional"
                ),
                "repository": "https://github.com/qilowatt/qilowatt-ha",
                "note": (
                    f"{qilowatt['entities_found']} of 4 Qilowatt dispatch entities detected."
                    if qilowatt["entities_found"]
                    else "Optional: connect Qilowatt through its physical controller or the HACS HA/MQTT integration."
                ),
            },
        },
        "qilowatt": qilowatt,
        "entities": entities,
        "price_entities": price_candidates,
        "weather_entities": weather_candidates,
        "unresolved_required": unresolved,
        "ambiguous_required": ambiguous,
        "ready": bool(
            SUPERVISOR_TOKEN and states and compatible_prices and compatible_weather_entity
            and not unresolved and not ambiguous
        ),
        "scanned_at": now(),
    }

@app.on_event("startup")
def startup():
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    load_config()

@app.get("/api/health")
def health():
    cfg = load_config()
    snapshot = state_snapshot(cfg)
    qilowatt = qilowatt_snapshot(cfg)
    return {
        "status": snapshot["health"]["status"], "version": VERSION,
        "runtime": "healthy", "api": "online", "state_engine": "online",
        "planner": "bootstrap", "connectors": 1, "configuration": "configured",
        "mode": cfg.runtime.mode, "missing": snapshot["health"]["missing"],
        "stale": snapshot["health"]["stale"],
        "qilowatt": qilowatt,
    }

@app.get("/api/config")
def get_config():
    cfg = load_config()
    payload = cfg.model_dump()
    payload["_meta"] = {
        "app_version": VERSION,
        "config_revision": cfg.revision,
    }
    return payload

@app.get("/api/setup/discovery")
def get_setup_discovery(rescan: bool = False):
    return setup_discovery(load_config(), force=rescan)

@app.post("/api/setup/complete")
def complete_setup(payload: dict):
    cfg = load_config()
    raw = cfg.model_dump()
    entities = payload.get("entities") or {}
    states = ha_states(force=True)
    for kind, path in SETUP_ENTITY_PATHS.items():
        entity_id = str(entities.get(kind) or "").strip()
        if not entity_id:
            continue
        item = next((normalize_ha_item(row) for row in states if row.get("entity_id") == entity_id), None)
        score, _ = connector_candidate_score(item or {}, kind)
        if score < 12:
            raise HTTPException(422, f"{entity_id} is not compatible with {CONNECTOR_LABELS[kind]}.")
        raw[path[0]][path[1]] = entity_id

    price_entity = str(payload.get("price_entity") or "").strip()
    if price_entity:
        price_item = next(
            (normalize_ha_item(row) for row in states if row.get("entity_id") == price_entity),
            None,
        )
        if not price_item_compatibility(price_item or {})[0]:
            raise HTTPException(
                422,
                f"{price_entity} does not provide compatible today and tomorrow price slots.",
            )
        raw["price_connector"]["entity"] = price_entity
        inferred_vat = nordpool_entity_vat_percent(price_entity)
        if inferred_vat is not None:
            raw["price_connector"]["source_includes_vat"] = inferred_vat > 0
            if inferred_vat > 0:
                raw["price_connector"]["vat_percent"] = inferred_vat
    weather_entity = str(payload.get("weather_entity") or "").strip()
    if weather_entity:
        weather_result, weather_error = ha_hourly_weather_forecast(weather_entity)
        if not weather_result or weather_result.get("entity_id") != weather_entity:
            raise HTTPException(
                422,
                f"{weather_entity} does not provide an hourly forecast: {weather_error or 'verification failed'}",
            )
        raw["forecast_connector"]["weather_entity"] = weather_entity

    site = payload.get("site") or {}
    for key in ("timezone", "country", "currency"):
        if site.get(key):
            raw["site"][key] = str(site[key]).strip()
    solar = payload.get("solar") or {}
    for key, config_key in (
        ("peak_kw", "solar_peak_kw"),
        ("tilt_degrees", "solar_tilt_degrees"),
        ("azimuth_degrees", "solar_azimuth_degrees"),
    ):
        if solar.get(key) is not None:
            raw["forecast_connector"][config_key] = solar[key]
    manual_capacity = payload.get("manual_capacity_kwh")
    if manual_capacity is not None:
        raw["battery_connector"]["capacity_source"] = "manual_override"
        raw["battery_connector"]["manual_capacity_kwh"] = manual_capacity
    qilowatt = payload.get("qilowatt") or {}
    qilowatt_mode = str(qilowatt.get("mode") or "disabled")
    if qilowatt_mode not in {"disabled", "ha_dispatch"}:
        raise HTTPException(422, "Unknown Qilowatt integration mode.")
    raw["qilowatt"]["mode"] = qilowatt_mode
    for key in ("mode_entity", "source_entity", "power_limit_entity", "connected_entity"):
        raw["qilowatt"][key] = str(qilowatt.get(key) or "").strip()
    raw["setup"] = {
        "completed": True,
        "completed_at": now(),
        "auto_discovery": True,
    }
    raw["version"] = 15
    completed = EnergyPilotConfig.model_validate(raw).validate_cross_fields()
    completed = save_config(completed, bump_revision=True)
    HA_STATES_CACHE.update({"fetched_monotonic": 0.0, "result": []})
    return {
        "status": "completed",
        "config": completed.model_dump(),
        "discovery": setup_discovery(completed, force=True),
    }

@app.put("/api/config")
def put_config(config: EnergyPilotConfig):
    try:
        config.validate_cross_fields()
        config = save_config(config, bump_revision=True)
        WEATHER_FORECAST_CACHE.update({"fetched_monotonic": 0.0, "result": None, "error": None})
        return {"status": "saved", "saved_at": now(), "config": config.model_dump()}
    except ValueError as exc:
        raise HTTPException(422, str(exc))

@app.post("/api/config/reset")
def reset_config():
    cfg = EnergyPilotConfig()
    cfg = save_config(cfg, bump_revision=True)
    WEATHER_FORECAST_CACHE.update({"fetched_monotonic": 0.0, "result": None, "error": None})
    return {"status": "reset", "config": cfg.model_dump()}

@app.get("/api/planner/overrides")
def get_planner_overrides():
    return {"overrides": load_planner_overrides()}

@app.put("/api/planner/overrides")
def put_planner_overrides(payload: dict):
    current = load_planner_overrides()
    for stamp in payload.get("remove", []):
        parsed = parse_time(stamp)
        if parsed:
            current.pop(parsed.isoformat(), None)
    action = payload.get("action")
    if action:
        for stamp in payload.get("slots", []):
            parsed = parse_time(stamp)
            if parsed:
                current[parsed.isoformat()] = {
                    "action": action,
                    "power_kw": payload.get("power_kw"),
                    "target_soc_percent": payload.get("target_soc_percent"),
                }
    save_planner_overrides(current)
    return {"status": "saved", "overrides": load_planner_overrides()}

def planner_preview_overrides(payload: dict) -> dict:
    preview = deepcopy(load_planner_overrides())
    allowed_actions = {"NORMAL", "BUY", "SELL", "SAVE BATTERY", "LIMIT EXPORT", "PV SELL"}
    action = str(payload.get("action") or "").upper()
    if action not in allowed_actions:
        raise HTTPException(422, "Choose a valid manual action.")
    slots = payload.get("slots")
    if not isinstance(slots, list) or not slots:
        raise HTTPException(422, "Select at least one plan slot.")
    power = payload.get("power_kw")
    target = payload.get("target_soc_percent")
    if power is not None and (not isinstance(power, (int, float)) or power < 0):
        raise HTTPException(422, "Power must be zero or greater.")
    if target is not None and (not isinstance(target, (int, float)) or target < 0 or target > 100):
        raise HTTPException(422, "Target SOC must be between 0 and 100%.")
    for stamp in slots:
        parsed = parse_time(stamp)
        if not parsed:
            raise HTTPException(422, f"Invalid slot timestamp: {stamp}")
        preview[parsed.isoformat()] = {
            "action": action,
            "power_kw": power,
            "target_soc_percent": target,
        }
    return preview

@app.post("/api/planner/preview")
def preview_planner_overrides(payload: dict):
    cfg = load_config()
    snapshot = state_snapshot(cfg)
    base_price = price_snapshot(cfg)
    candidate_price = deepcopy(base_price)
    apply_slot_plan(cfg, snapshot, base_price)
    apply_slot_plan(cfg, snapshot, candidate_price, planner_preview_overrides(payload))
    baseline = (base_price.get("plan") or {}).get("horizon_financial") or {}
    candidate_plan = candidate_price.get("plan") or {}
    candidate = candidate_plan.get("horizon_financial") or {}
    if not baseline or not candidate:
        raise HTTPException(422, "Planner financial preview is unavailable for these slots.")
    delta_cents = float(candidate.get("net_cents") or 0.0) - float(baseline.get("net_cents") or 0.0)
    issue_threshold_cents = 5.0
    warnings = []
    final_soc = candidate_plan.get("final_soc_percent")
    if final_soc is not None and final_soc <= cfg.battery_policy.reserve_soc_percent + 0.1:
        warnings.append("The preview ends at the configured battery reserve.")
    if abs(delta_cents) < 0.005:
        warnings.append("This manual command has no material effect on the full planning-horizon result.")
    planner_issue = delta_cents >= issue_threshold_cents
    return {
        "baseline": {
            **baseline,
            "final_soc_percent": (base_price.get("plan") or {}).get("final_soc_percent"),
        },
        "candidate": {
            **candidate,
            "final_soc_percent": final_soc,
        },
        "delta_cents": round(delta_cents, 2),
        "assessment": (
            "planner_optimization_issue" if planner_issue
            else "better" if delta_cents > 0.005
            else "worse" if delta_cents < -0.005
            else "unchanged"
        ),
        "planner_optimization_issue": planner_issue,
        "issue_threshold_cents": issue_threshold_cents,
        "comparison_scope": "planning_horizon",
        "warnings": warnings,
        "saved": False,
    }

@app.get("/api/state")
def get_state():
    return state_snapshot(load_config())

@app.get("/api/flow")
def get_flow():
    snapshot = state_snapshot(load_config())
    return {"observed_at": snapshot["observed_at"], "health": snapshot["health"], "flow": snapshot["flow"]}

@app.get("/api/price")
def get_price():
    return price_snapshot(load_config())

def monthly_benefits(cfg: EnergyPilotConfig, snapshot: dict, price: dict) -> dict:
    if not BENEFITS_CACHE["loaded"]:
        try:
            BENEFITS_CACHE["data"] = json.loads(BENEFITS_FILE.read_text())
        except (OSError, ValueError, TypeError):
            BENEFITS_CACHE["data"] = {}
        BENEFITS_CACHE["loaded"] = True
    now = datetime.now(timezone.utc)
    local_now = now.astimezone(ZoneInfo(cfg.site.timezone))
    month_key = local_now.strftime("%Y-%m")
    data = BENEFITS_CACHE["data"]
    month = data.get("month")
    energy_now = snapshot.get("battery", {}).get("energy_kwh", {}).get("value")
    if month != month_key:
        data.clear()
        data.update({
            "month": month_key, "tracking_since": now.isoformat(),
            "last_observed_at": now.isoformat(), "opening_energy_kwh": energy_now,
            "current_energy_kwh": energy_now, "import_kwh": 0.0, "export_kwh": 0.0,
            "import_cost_cents": 0.0, "export_revenue_cents": 0.0,
            "battery_throughput_kwh": 0.0, "wear_cost_cents": 0.0,
            "negative_price_protection_minutes": 0.0,
        })
    previous = parse_time(data.get("last_observed_at"))
    elapsed_hours = max(0.0, min(60.0, (now - previous).total_seconds()) / 3600.0) if previous else 0.0
    grid_kw = snapshot.get("grid", {}).get("power_kw", {}).get("value")
    battery_kw = snapshot.get("battery", {}).get("power_kw", {}).get("value")
    import_price = price.get("import_cents_kwh")
    export_price = price.get("export_cents_kwh")
    if elapsed_hours > 0 and grid_kw is not None:
        if grid_kw >= 0 and import_price is not None:
            amount = grid_kw * elapsed_hours
            data["import_kwh"] += amount
            data["import_cost_cents"] += amount * import_price
        elif grid_kw < 0 and export_price is not None:
            amount = -grid_kw * elapsed_hours
            data["export_kwh"] += amount
            data["export_revenue_cents"] += amount * export_price
    wear_rate = battery_wear_model(cfg, snapshot)["effective_rate_cents_kwh"]
    if elapsed_hours > 0 and battery_kw is not None:
        throughput = abs(battery_kw) * elapsed_hours
        data["battery_throughput_kwh"] += throughput
        data["wear_cost_cents"] += throughput * wear_rate
    current_slot = (price.get("slots") or [{}])[0]
    if elapsed_hours > 0 and current_slot.get("action") == "LIMIT EXPORT":
        data["negative_price_protection_minutes"] += elapsed_hours * 60.0
    data["last_observed_at"] = now.isoformat()
    data["current_energy_kwh"] = energy_now
    terminal_value = float((price.get("plan") or {}).get("terminal_energy_value_cents_kwh") or 0.0)
    opening = data.get("opening_energy_kwh")
    inventory_change = (
        (energy_now - opening) * terminal_value
        if energy_now is not None and opening is not None else 0.0
    )
    operating = data["export_revenue_cents"] - data["import_cost_cents"] - data["wear_cost_cents"]
    if time.monotonic() - BENEFITS_CACHE["last_saved_monotonic"] >= 60:
        try:
            atomic_write(BENEFITS_FILE, data)
            BENEFITS_CACHE["last_saved_monotonic"] = time.monotonic()
        except OSError:
            pass
    return {
        "period": month_key,
        "tracking_since": data["tracking_since"],
        "import_kwh": round(data["import_kwh"], 3),
        "export_kwh": round(data["export_kwh"], 3),
        "import_cost_cents": round(data["import_cost_cents"], 2),
        "export_revenue_cents": round(data["export_revenue_cents"], 2),
        "battery_throughput_kwh": round(data["battery_throughput_kwh"], 3),
        "wear_cost_cents": round(data["wear_cost_cents"], 2),
        "stored_energy_value_change_cents": round(inventory_change, 2),
        "operating_result_cents": round(operating, 2),
        "net_cents": round(operating + inventory_change, 2),
        "negative_price_protection_minutes": round(data["negative_price_protection_minutes"], 1),
        "bill_result_cents": round(data["export_revenue_cents"] - data["import_cost_cents"], 2),
        "result_after_wear_cents": round(data["export_revenue_cents"] - data["import_cost_cents"] - data["wear_cost_cents"], 2),
    }

@app.get("/api/insights")
def get_insights(period: str = "month", start: Optional[str] = None, end: Optional[str] = None):
    if period not in {"week", "month", "year", "custom", "lifetime"}:
        raise HTTPException(422, "Unknown insights period.")
    return insights_summary(load_config(), period, start, end)

@app.get("/api/overview")
def get_overview():
    cfg = load_config()
    snapshot = state_snapshot(cfg)
    price = price_snapshot(cfg)
    history = measurement_history(cfg, snapshot)
    price["history_slots"] = history["slots"]
    price["history_status"] = {
        "retention_days": history["retention_days"],
        "recorder": history["recorder"],
        "storage_file": history["storage_file"],
    }
    apply_slot_plan(cfg, snapshot, price)
    record_insights(cfg, snapshot, price)
    qilowatt = qilowatt_snapshot(cfg)
    benefits = monthly_benefits(cfg, snapshot, price)
    update_learning(cfg, snapshot, price)
    planner = planner_snapshot(cfg, snapshot, price, qilowatt)
    return {
        "version": VERSION,
        "observed_at": snapshot["observed_at"],
        "state": snapshot,
        "planner": planner,
        "notifications": {
            "pv_curtailment": pv_curtailment_notification(cfg, snapshot, price),
        },
        "qilowatt": qilowatt,
        "price": price,
        "benefits_month": benefits,
        "metrics": overview_metrics(snapshot),
        "runtime": {
            "mode": cfg.runtime.mode,
            "strategy": cfg.planning.strategy,
            "behavior_profile": cfg.planning.behavior_profile,
        },
    }

@app.get("/")
def index():
    return FileResponse(
        STATIC_DIR / "index.html",
        media_type="text/html",
        headers={"Cache-Control": "no-cache"},
    )
