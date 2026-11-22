"""Provider adapters and a stateless six-hour, quarter-hour forecast."""
from datetime import datetime
from statistics import median
from homeassistant.util import dt as dt_util
from .model import clamp, curve, predicted_cop, heating_load, heat_cost
from .schema import numeric
from .units import convert


def timestamp(value):
    if numeric(value) is not None:
        value = float(value)
        return value/1000 if value > 100_000_000_000 else value
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed.timestamp() if parsed.tzinfo is not None else None
    except (ValueError, OverflowError, TypeError):
        return None


def price_intervals(rows, parameters):
    result = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        if parameters["price_format"] == "zonneplan":
            start, end = timestamp(row.get("start_date")), timestamp(row.get("end_date"))
            tax = row.get("price_tax_included") or {}
            value = numeric(tax.get("amount")) if isinstance(tax, dict) else None
            value = value/10_000_000 if value is not None else None
        else:
            start, end = timestamp(row.get(parameters["price_start_key"])), timestamp(row.get(parameters["price_end_key"]))
            value = numeric(row.get(parameters["price_value_key"]))
        if start is not None and end is not None and end > start and value is not None:
            result.append((start, end, value))
    return result


def solar_intervals(response, parameters):
    result = []
    if parameters["pv_forecast_format"] == "forecast_solar":
        rows = response.get("wh_period", {}) if isinstance(response, dict) else {}
        for start, wh in rows.items():
            t, energy = timestamp(start), numeric(wh)
            if t is not None and energy is not None and energy >= 0:
                result.append((t, t+3600, energy/1000))
    else:
        for row in response if isinstance(response, list) else []:
            if not isinstance(row, dict):
                continue
            t = timestamp(row.get(parameters["pv_forecast_start_key"]))
            value = numeric(row.get(parameters["pv_forecast_value_key"]))
            end = timestamp(row.get("end")) or (t+3600 if t is not None else None)
            if t is not None and value is not None and value >= 0 and end > t:
                result.append((t, end, value))
    return result


def in_interval(rows, at, *, solar=False):
    hits = [v for start, end, v in rows if start <= at < end]
    if hits:
        return hits[-1]
    if solar:
        # Providers omit night hours between successive forecast days. Compare
        # against this local day's daylight envelope, not the whole response.
        # A completely missing day or a hole inside daylight remains unknown.
        day = dt_util.as_local(dt_util.utc_from_timestamp(at)).date()
        daylight = [(s, e, v) for s, e, v in rows
                    if dt_util.as_local(dt_util.utc_from_timestamp(s)).date() == day]
        if daylight and (at < min(s for s, e, v in daylight)
                         or at >= max(e for s, e, v in daylight)):
            return 0.
    return None


def valid_forecast(forecast, now):
    return (isinstance(forecast, dict) and forecast.get("complete") is True
            and forecast.get("status") == "ok" and numeric(forecast.get("premium")) is not None
            and forecast.get("valid_until", 0) > now and bool(forecast.get("quarters"))
            and forecast["quarters"][0].get("start") == int(now//900)*900)


def predict(config, data, prices, weather, solar, now, efficiency=0, other_mean=None, provider_problems=()):
    p, m = config["parameters"], config["model"]
    problems, slots = list(provider_problems), []
    required = ["local_outdoor", "electric_power", "backup_power", "import_power", "pv_power", "electricity_price"]
    if "export_power" in config["inputs"]:
        required.append("export_power")
    for role in ["ventilation_power", "stove_temperature"]:
        if role in config["inputs"]:
            required.append(role)
    for role in required:
        if numeric(data.get(role)) is None:
            problems.append("input_missing:"+config["inputs"].get(role, role))
    for role in ["electric_power", "backup_power", "import_power", "export_power", "pv_power", "ventilation_power"]:
        if numeric(data.get(role)) is not None and data[role] < 0:
            problems.append("input_invalid:"+config["inputs"].get(role, role))
    if not data["rooms_ok"] or data["indoor"] is None:
        problems.extend("input_missing:"+entity for entity in data["room_missing"] or ["room_temperatures"])
    if not m["confirmed"]:
        problems.append("model_not_configured")
    weather_state = data.get("raw", {}).get("weather", {})
    attrs = weather_state.get("attributes", {})
    weather_now = convert(attrs.get("temperature"), attrs.get("temperature_unit"), "temperature")
    if weather_now is None:
        problems.append("input_missing:"+config["inputs"].get("weather", "weather"))
    if data.get("other_power") is None:
        problems.append("input_missing:other_household_load")
    if not solar and "pv_power" in config["inputs"]:
        problems.append("pv_forecast_missing")
    normalized_prices = price_intervals(prices, p)
    solar_rows = [solar_intervals(response, p) for response in solar]
    q0 = int(now//900)*900
    trim = clamp(int(efficiency), 0, int(p["maximum_reduction"]))
    if not problems:
        other = other_mean if numeric(other_mean) is not None else data["other_power"]
        bias = clamp(data["local_outdoor"]-weather_now, -3, 3)
        for index in range(25):
            t, mid = q0+index*900, q0+index*900+450
            price = in_interval(normalized_prices, mid)
            temp, wind = None, None
            if index == 0:
                temp = data["local_outdoor"]
            else:
                for row in weather:
                    start = timestamp(row.get("datetime"))
                    value = convert(row.get("temperature"), attrs.get("temperature_unit"), "temperature")
                    if start is not None and start <= mid < start+3600 and value is not None:
                        temp, wind = value+bias, numeric(row.get("wind_speed"))
                if temp is None and mid < now+3600:
                    temp = data["local_outdoor"]
            pv_parts = [in_interval(rows, mid, solar=True) for rows in solar_rows]
            pv = None if any(v is None for v in pv_parts) else sum(pv_parts)
            if index == 0 and pv is not None:
                pv = data["pv_power"]
            for key, value in [("price", price), ("weather", temp), ("pv", pv)]:
                if value is None:
                    problems.append(f"forecast_gap:{key}@{t}")
            if any(v is None for v in [price, temp, pv]):
                continue
            house, need = heating_load(data, temp, pv, m)
            q = clamp(need, p["comparison_min_heat_kw"], p["comparison_max_heat_kw"])
            original = curve(temp, p)
            water = max(p["target_floor"], original-trim)
            original_cop, cop = predicted_cop(temp, original, q, m), predicted_cop(temp, water, q, m)
            cost = heat_cost(price, pv, other, q/cop, cop, p["export_discount"])
            original_cost = heat_cost(price, pv, other, q/original_cop, original_cop, p["export_discount"])
            slots.append(dict(start=t, import_price=price, export_price=price-p["export_discount"],
                              outdoor=temp, water=water, original_water=original, pv_kw=pv,
                              other_kw=other, house_heat_demand_kw=house,
                              ventilation_contribution_kw=house-need, heat_demand_kw=need,
                              cop=cop, original_cop=original_cop, heat_cost=cost,
                              original_heat_cost=original_cost, wind_speed=wind,
                              extrapolated=not (m["training_outdoor_min"] <= temp <= m["training_outdoor_max"]
                                  and m["training_water_min"] <= water <= m["training_water_max"]
                                  and m["cop_q_min"] <= need <= m["cop_q_max"])))
    if len(slots) == 25 and numeric(data.get("electricity_price")) is not None and abs(slots[0]["import_price"]-data["electricity_price"]) > .01:
        problems.append("current_tariff_mismatch")
    complete = len(slots) == 25 and not problems
    reference = median(q["heat_cost"] for q in slots[1:]) if complete else None
    deadline = q0 + 900
    if data.get("pv_hold_until"):
        deadline = min(deadline, data["pv_hold_until"])
    return dict(status="ok" if complete else "incomplete", complete=complete, quarters=slots,
                problems=list(dict.fromkeys(problems)), generated_at=now, valid_until=deadline,
                current_cost=slots[0]["heat_cost"] if complete else None,
                reference_cost=reference, premium=slots[0]["heat_cost"]-reference if complete else None,
                cop=slots[0]["cop"] if complete else None,
                original_cop=slots[0]["original_cop"] if complete else None,
                original_current_cost=slots[0]["original_heat_cost"] if complete else None,
                efficiency_heat_cost_difference=slots[0]["original_heat_cost"]-slots[0]["heat_cost"] if complete else None,
                efficiency_reduction=trim, pv_source=data["pv_source"], pv_age_seconds=data["pv_age_seconds"],
                house_heat_energy_6h_kwh=sum(q["house_heat_demand_kw"] for q in slots[1:])*.25 if complete else None,
                heatpump_heat_energy_6h_kwh=sum(q["heat_demand_kw"] for q in slots[1:])*.25 if complete else None,
                extrapolation=any(q["extrapolated"] for q in slots))

