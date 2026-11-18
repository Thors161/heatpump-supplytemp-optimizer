"""Configuration validation independent of Home Assistant and its UI."""
from copy import deepcopy
from collections.abc import Mapping
from math import isfinite
import re
from .const import DEFAULT_MODEL, DEFAULT_PARAMETERS, REQUIRED_ROLES, ROLES, SETTINGS


def numeric(value):
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
        return result if isfinite(result) else None
    except (TypeError, ValueError, OverflowError):
        return None


def validate_config(raw):
    """Reject malformed imports; normalize defaults and stable room identifiers."""
    if not isinstance(raw, Mapping):
        raise ValueError("configuration_must_be_object")
    config = {"inputs": {}, "rooms": [], "parameters": dict(DEFAULT_PARAMETERS),
              "model": dict(DEFAULT_MODEL), "settings": {k: v[0] for k, v in SETTINGS.items()},
              "pv_forecast_entities": [], "pv_forecast_entries": []}
    config["name"] = str(raw.get("name", "Heat Pump Supply Temperature Optimizer"))[:100]
    if not isinstance(raw.get("inputs",{}), Mapping):
        raise ValueError("inputs_must_be_object")
    if not isinstance(raw.get("rooms",[]),list):
        raise ValueError("rooms_must_be_list")
    for key, value in raw.get("inputs", {}).items():
        if key not in ROLES:
            raise ValueError("unknown_input_role:" + key)
        if value:
            if not isinstance(value, str) or not re.fullmatch(r"[a-z_]+\.[a-z0-9_]+", value):
                raise ValueError("invalid_entity:" + key)
            config["inputs"][key] = value
    missing = set(REQUIRED_ROLES) - config["inputs"].keys()
    if missing:
        raise ValueError("missing_inputs:" + ",".join(sorted(missing)))
    for group in ["parameters", "model", "settings"]:
        values = raw.get(group, {})
        if not isinstance(values, Mapping) or set(values) - set(config[group]):
            raise ValueError("invalid_" + group)
        config[group].update(values)
    p = config["parameters"]
    for key, default in DEFAULT_PARAMETERS.items():
        if isinstance(default, bool):
            if not isinstance(p[key], bool):
                raise ValueError("invalid_parameter:"+key)
        elif isinstance(default, (float, int)):
            if numeric(p[key]) is None:
                raise ValueError("invalid_parameter:"+key)
            p[key] = float(p[key])
        elif not isinstance(p[key], str) or not p[key]:
            raise ValueError("invalid_parameter:"+key)
    if not (-40 <= p["curve_cold_outdoor"] < p["curve_warm_outdoor"] <= 50
            and 20 <= p["target_floor"] <= p["curve_warm_water"] <= p["curve_cold_water"] <= 60
            and 0 <= p["maximum_reduction"] <= 4 and p["maximum_reduction"].is_integer()
            and 0 < p["capacity_kw"] <= 50 and 0 <= p["export_discount"] <= 1
            and p["space_mode"] != p["dhw_mode"]):
        raise ValueError("invalid_curve_or_limits")
    if p["price_format"] not in ["zonneplan", "generic"] or p["pv_forecast_format"] not in ["forecast_solar", "generic"]:
        raise ValueError("unsupported_forecast_format")
    if p["curve_rounding"] not in ["ceil", "nearest", "none"] or not 0 < p["comparison_min_heat_kw"] < p["comparison_max_heat_kw"]:
        raise ValueError("invalid_curve_rounding_or_comparison_load")
    for key, value in config["model"].items():
        if key == "confirmed":
            if not isinstance(value, bool):
                raise ValueError("invalid_model:confirmed")
        else:
            if numeric(value) is None:
                raise ValueError("invalid_model:"+key)
            config["model"][key] = float(value)
    m = config["model"]
    if not (0 < m["heat_loss"] <= 5 and m["cop_q_max"] > m["cop_q_min"] > 0
            and m["ventilation_full_w"] > m["ventilation_idle_w"] >= 0
            and m["stove_full_c"] > m["stove_idle_c"]
            and m["training_outdoor_min"] < m["training_outdoor_max"]
            and m["training_water_min"] < m["training_water_max"]
            and all(m[k] >= 0 for k in ["solar_gain", "stove_gain", "background_gain", "ventilation_heat_kw"])):
        raise ValueError("invalid_model_ranges")
    for key, (default, lo, hi, step, unit) in SETTINGS.items():
        value = numeric(config["settings"][key])
        if value is None or not lo <= value <= hi:
            raise ValueError("invalid_setting:"+key)
        config["settings"][key] = value
    if config["settings"]["gap_low"] >= config["settings"]["gap_high"] or config["settings"]["room_margin"] >= config["settings"]["room_recovery"]:
        raise ValueError("invalid_settings_combination")
    seen = set()
    for index, original in enumerate(raw.get("rooms", [])):
        if not isinstance(original, Mapping):
            raise ValueError("invalid_room")
        room = {"id": str(original.get("id", f"room_{index+1}")),
                "name": str(original.get("name", f"Room {index+1}")),
                "enabled": original.get("enabled", True),
                "climate": original.get("climate"), "actual": original.get("actual"),
                "setpoint": original.get("setpoint"), "allowance": original.get("allowance", 0.)}
        if not re.fullmatch(r"[a-z0-9_]+", room["id"]) or room["id"] in seen:
            raise ValueError("invalid_or_duplicate_room_id")
        seen.add(room["id"])
        if not isinstance(room["enabled"], bool) or not room["name"]:
            raise ValueError("invalid_room")
        if bool(room["climate"]) == bool(room["actual"] and room["setpoint"]):
            raise ValueError("room_needs_climate_or_temperature_pair")
        for key in ["climate", "actual", "setpoint"]:
            if room[key] and (not isinstance(room[key], str) or not re.fullmatch(r"[a-z_]+\.[a-z0-9_]+", room[key])):
                raise ValueError("invalid_room_entity")
        if room["climate"] and not room["climate"].startswith("climate."):
            raise ValueError("invalid_climate_entity")
        allowance = numeric(room["allowance"])
        if allowance is None or not 0 <= allowance <= 2:
            raise ValueError("invalid_room_allowance")
        room["allowance"] = allowance
        config["rooms"].append(room)
    if not config["rooms"] or not any(r["enabled"] for r in config["rooms"]):
        raise ValueError("at_least_one_enabled_room_required")
    for key in ["pv_forecast_entities", "pv_forecast_entries"]:
        values = raw.get(key, [])
        if not isinstance(values, list) or any(not isinstance(v, str) or not v for v in values):
            raise ValueError("invalid_"+key)
        config[key] = list(dict.fromkeys(values))
    return deepcopy(config)

