"""Read configured entities into a normalized, provider-independent snapshot."""
from statistics import mean
from .const import ROLES, PV_HOLD_SECONDS
from .model import clamp
from .schema import numeric
from .units import convert


def usable(state):
    return isinstance(state, dict) and state.get("state") not in [None, "unknown", "unavailable", ""]


def read_inputs(config, states, timestamp, pv_cache, allowances=None):
    values, problems, raw, room_values = {}, [], {}, {}
    allowances = allowances or {}
    def read(role):
        entity = config["inputs"].get(role)
        if not entity:
            return None
        state = states.get(entity, {})
        raw[role] = state
        if not usable(state):
            return None
        kind = ROLES[role]
        if kind in ["mode", "weather"]:
            return state.get("state")
        return convert(state.get("state"), state.get("attributes", {}).get("unit_of_measurement"), kind)
    for role in ROLES:
        values[role] = read(role)
    # Current PV uses a bounded cache of real readings, never cached-fallback time.
    pv = values["pv_power"]
    age = timestamp-pv_cache.get("at", timestamp+1)
    if "pv_power" not in config["inputs"]:
        pv, source, age = 0., "not_configured", None
    elif pv is not None and pv >= 0:
        pv_cache.update(kw=pv, at=timestamp)
        source, age = "live", 0
    elif usable(states.get("sun.sun")) and states["sun.sun"]["state"] == "below_horizon":
        pv, source = 0., "night_zero"
    elif numeric(pv_cache.get("kw")) is not None and pv_cache["kw"] >= 0 and 0 <= age <= PV_HOLD_SECONDS:
        pv, source = pv_cache["kw"], "last_valid"
    else:
        pv, source = None, "expired_or_missing"
    values.update(pv_power=pv, pv_source=source, pv_age_seconds=age if "at" in pv_cache else None)
    values["pv_hold_until"] = pv_cache.get("at", 0)+PV_HOLD_SECONDS if source == "last_valid" else None
    actuals, deficits, missing_rooms, active = [], [], [], 0
    for room in config["rooms"]:
        entity = room["climate"] or room["actual"]
        state = states.get(entity, {})
        off = bool(room["climate"]) and state.get("state") == "off"
        attrs = state.get("attributes", {})
        unit = attrs.get("temperature_unit", attrs.get("unit_of_measurement"))
        if room["climate"]:
            actual = convert(attrs.get("current_temperature"), unit, "temperature") if usable(state) else None
            target = convert(attrs.get("temperature"), unit, "temperature") if usable(state) else None
        else:
            actual = convert(state.get("state"), unit, "temperature") if usable(state) else None
            target_state = states.get(room["setpoint"], {})
            target = convert(target_state.get("state"), target_state.get("attributes", {}).get("unit_of_measurement"), "temperature") if usable(target_state) else None
        if actual is not None:
            actuals.append(actual)
        allowance = clamp(allowances.get(room["id"], room["allowance"]), 0, 2)
        valid = actual is not None and (off or target is not None)
        adjusted = round(max(0., target-actual-allowance), 6) if valid and not off else 0. if valid else None
        room_values[room["id"]] = dict(actual=actual, target=target, allowance=allowance,
                                      raw=max(0., target-actual) if valid and not off else 0. if valid else None,
                                      adjusted=adjusted, included=room["enabled"], off=off)
        if room["enabled"]:
            if not valid:
                missing_rooms.append(entity if actual is None else (room["climate"] or room["setpoint"])+":setpoint")
            elif not off:
                active += 1
                deficits.append(adjusted)
    values.update(rooms=room_values, rooms_ok=not missing_rooms, room_missing=missing_rooms,
                  indoor=mean(actuals) if actuals else None, active_rooms=active,
                  max_deficit=max([0.]+deficits), raw_max_deficit=max([0.]+[
                      r["raw"] for r in room_values.values() if r["included"] and r["raw"] is not None]))
    p = config["parameters"]
    mode = values["heating_mode"]
    if mode not in [p["space_mode"], p["dhw_mode"]] and p["outlet_dhw_fallback"] and values["supply_temperature"] is not None:
        mode = p["dhw_mode"] if values["supply_temperature"] > 40 else p["space_mode"]
    values["space_mode"] = mode == p["space_mode"]
    values["dhw_mode"] = mode == p["dhw_mode"]
    backup = values["backup_power"] if "backup_power" in config["inputs"] else 0.
    values["backup_power"] = backup
    values["electric_total"] = values["electric_power"]+backup if values["electric_power"] is not None and backup is not None else None
    water_roles = ["local_outdoor", "electric_power", "backup_power", "heat_power",
                   "supply_temperature", "primary_return", "secondary_return"]
    values["water_ok"] = all(values[k] is not None for k in water_roles)
    values["space_ok"] = (values["water_ok"] and values["space_mode"] and values["electric_power"] >= 0
                          and backup >= 0 and 0 <= values["secondary_return"] <= 60)
    values["return_gap"] = values["primary_return"]-values["secondary_return"] if values["water_ok"] else None
    values["cop_ok"] = values["space_mode"] and all(values[k] is not None for k in ["heat_power", "electric_total"]) and values["electric_total"] >= 0
    values["learning_ok"] = (values["space_ok"] and values["rooms_ok"] and active > 0
                             and values["primary_flow"] is not None and values["primary_flow"] > 0
                             and values["compressor_frequency"] is not None and values["compressor_frequency"] > 0 and values["heat_power"] > 0)
    grid_in = values["import_power"] if "import_power" in config["inputs"] else None
    grid_out = values["export_power"] if "export_power" in config["inputs"] else 0.
    other = max(0., grid_in-grid_out+pv-values["electric_total"]) if all(v is not None and v >= 0 for v in [grid_in, grid_out, pv, values["electric_total"]]) else None
    values["other_power"] = other
    for role in ["local_outdoor", "curve_outdoor"]:
        if values[role] is not None and not -40 < values[role] < 50:
            values[role] = None
    values["raw"] = raw
    return values

