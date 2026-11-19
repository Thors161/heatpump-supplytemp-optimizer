"""Advisory state machine. Never sends commands to heating equipment."""
from copy import deepcopy
from math import floor
from .const import MAX_SAMPLE_GAP, MIN_STEP_SECONDS, ROOM_SHORTFALL_RISE, SETTINGS, SNAPSHOT_VERSION
from .forecast import valid_forecast
from .inputs import read_inputs
from .model import clamp, curve, price_level
from .rolling import Rolling
from .schema import numeric


class Engine:
    def __init__(self, config, saved=None, now=0):
        self.config = config
        saved = saved if isinstance(saved, dict) and saved.get("version") == SNAPSHOT_VERSION else {}
        default_rise, rise_low, rise_high, _, _ = ROOM_SHORTFALL_RISE
        default_rises = {room["id"]: room.get("max_shortfall_rise", default_rise) for room in config["rooms"]}
        self.state = dict(version=SNAPSHOT_VERSION, enabled=False, price_enabled=False,
                          efficiency_enabled=True, reduction=0, efficiency=0,
                          effective_reduction=0, target=None, baseline=None,
                          changed=0., efficiency_changed=0., hold_until=0.,
                          reason="startup_waiting_for_decision", efficiency_reason="startup_waiting_for_observations",
                          pv_cache={}, settings=dict(config["settings"]), allowances={},
                          shortfall_rise_limits={}, limited_shortfalls={},
                          forecast={}, power_history={}, predictions={},
                          default_settings=dict(config["settings"]),
                          default_allowances={r["id"]: r["allowance"] for r in config["rooms"]})
        for key in self.state:
            if key in saved:
                self.state[key] = deepcopy(saved[key])
        for flag in ["enabled", "price_enabled", "efficiency_enabled"]:
            self.state[flag] = self.state[flag] is True
        for key in ["reduction", "efficiency", "effective_reduction"]:
            self.state[key] = int(clamp(numeric(self.state[key]) or 0, 0, config["parameters"]["maximum_reduction"]))
        for key in ["changed", "efficiency_changed", "hold_until"]:
            self.state[key] = numeric(self.state[key]) or 0.
        for key in ["pv_cache", "settings", "allowances", "shortfall_rise_limits", "limited_shortfalls",
                    "forecast", "power_history", "predictions"]:
            if not isinstance(self.state[key], dict):
                self.state[key] = {}
        for key, default in config["settings"].items():
            if saved.get("default_settings", {}).get(key, default) != default:
                self.state["settings"][key] = default
            if numeric(self.state["settings"].get(key)) is None:
                self.state["settings"][key] = default
        self.state["default_settings"] = dict(config["settings"])
        for room in config["rooms"]:
            rid, default = room["id"], room["allowance"]
            if saved.get("default_allowances", {}).get(rid, default) != default or numeric(self.state["allowances"].get(rid)) is None:
                self.state["allowances"][rid] = default
            self.state["allowances"][rid] = clamp(self.state["allowances"][rid], 0, 2)
        self.state["default_allowances"] = {r["id"]: r["allowance"] for r in config["rooms"]}
        self.state["allowances"] = {rid:self.state["allowances"][rid] for rid in self.state["default_allowances"]}
        saved_rise_defaults = saved.get("default_shortfall_rise_limits", {})
        if not isinstance(saved_rise_defaults, dict):
            saved_rise_defaults = {}
        rates = {}
        limited = {}
        for rid, default in default_rises.items():
            saved_rate = numeric(self.state["shortfall_rise_limits"].get(rid))
            if saved_rate is None or saved_rise_defaults.get(rid, default) != default:
                saved_rate = default
            rates[rid] = clamp(saved_rate, rise_low, rise_high)
            value = numeric(self.state["limited_shortfalls"].get(rid))
            if value is not None and value >= 0:
                limited[rid] = value
        self.state.update(shortfall_rise_limits=rates, limited_shortfalls=limited,
                          default_shortfall_rise_limits=default_rises)
        self._shortfall_sample_times = {}
        self.general = Rolling(saved.get("samples", []))
        # An outage must not be interpolated as a measured heating interval.
        self.power = Rolling()
        self.started = now
        self.data = {}
        self.forecast_attempt = 0.
        self.last_sample_minute = None
        self.last_decision_quarter = None
        self.space_since = self.learning_since = None
        self.comfort_since = self.comfort_clear_since = None
        self.demand_since = self.demand_clear_since = None
        self.comfort = self.demand_rising = False
        self.hydraulics_ready = self.observations_ready = False
        self.observations = {}
        # Preserve completed quarters and pre-quarter predictions, invalidate an open one.
        if self.state["power_history"].get("interval"):
            self.state["power_history"]["interval"]["valid"] = False

    def serialize(self):
        return deepcopy(self.state | {"samples": self.general.samples})

    def sample(self, states, now):
        d = self.data = read_inputs(self.config, states, now, self.state["pv_cache"], self.state["allowances"])
        self._limit_room_shortfalls(now)
        self.space_since = (self.space_since if self.space_since is not None else now) if d["space_ok"] else None
        self.learning_since = (self.learning_since if self.learning_since is not None else now) if d["learning_ok"] else None
        self.power.add(now, {"heat": d["heat_power"] if d["cop_ok"] else None,
                             "electric": d["electric_total"] if d["cop_ok"] else None})
        self._update_power_history(now)
        minute = int(now//60)
        if minute != self.last_sample_minute:
            self.general.add(now, dict(flow=d["secondary_flow"] if d["space_ok"] else None,
                gap=d["return_gap"] if d["space_ok"] else None,
                eligible_gap=d["return_gap"] if d["learning_ok"] else None,
                eligible_flow=d["secondary_flow"] if d["learning_ok"] else None,
                deficit=d["limited_max_deficit"] if d["rooms_ok"] and d["space_mode"] else None,
                other=d["other_power"]))
            self.last_sample_minute = minute
        self.observations = {k: self.general.changes(k, now) for k in ["flow", "gap", "eligible_gap", "eligible_flow", "deficit"]}
        self.hydraulics_ready = (self.space_since is not None and now-self.space_since >= 1800
                                and all(self.observations[k]["ready"] for k in ["flow", "gap"]))
        self.observations_ready = (self.learning_since is not None and now-self.learning_since >= 300
                                  and all(self.observations[k]["ready"] for k in ["eligible_gap", "eligible_flow", "deficit"]))
        shortfall = not d["rooms_ok"] or d["limited_max_deficit"] >= .5
        if shortfall:
            self.comfort_clear_since = None
            self.comfort_since = self.comfort_since if self.comfort_since is not None else now
            if now-self.comfort_since >= 1800:
                self.comfort = True
        else:
            self.comfort_since = None
            self.comfort_clear_since = self.comfort_clear_since if self.comfort_clear_since is not None else now
            if now-self.comfort_clear_since >= 600:
                self.comfort = False
        growing = (self.hydraulics_ready and self.observations["flow"]["change"] >= 5/6
                   and self.observations["gap"]["change"] <= -.3)
        if growing:
            self.demand_clear_since = None
            self.demand_since = self.demand_since if self.demand_since is not None else now
            if now-self.demand_since >= 1800:
                self.demand_rising = True
        else:
            self.demand_since = None
            self.demand_clear_since = self.demand_clear_since if self.demand_clear_since is not None else now
            if now-self.demand_clear_since >= 300:
                self.demand_rising = False

    def _limit_room_shortfalls(self, now):
        values = self.state["limited_shortfalls"]
        rates = self.state["shortfall_rise_limits"]
        times = self._shortfall_sample_times
        maximum = 0.
        for rid, room in self.data["rooms"].items():
            adjusted = room["adjusted"]
            room["limited"] = adjusted
            if adjusted is None:
                times.pop(rid, None)
                continue
            previous_time = times.get(rid, now)
            elapsed = now - previous_time
            times[rid] = max(now, previous_time)
            if rates[rid] > 0 and rid in values:
                # Only credit time between consecutive valid samples.
                hours = elapsed / 3600 if 0 <= elapsed <= MAX_SAMPLE_GAP else 0.
                room["limited"] = min(adjusted, values[rid] + rates[rid] * hours)
            values[rid] = room["limited"]
            if room["included"]:
                maximum = max(maximum, room["limited"])
        self.data["limited_max_deficit"] = round(maximum, 6)

    def _update_power_history(self, now):
        h, d = self.state["power_history"], self.data
        q0 = int(now//900)*900
        old = h.get("interval")
        if old:
            dt = now-h.get("observed_at", now)
            valid = old["valid"] and d["cop_ok"] and h.get("ok", False) and 0 <= dt <= 90
            area = old["area"]+(h.get("power", 0)*dt if valid else 0)
            old.update(area=area, valid=valid)
            if old["start"] < q0:
                matched = valid and q0 == old["start"]+900 and now-q0 <= 10 and numeric(old.get("predicted")) is not None
                h.update(matched=matched, predicted=old.get("predicted") if matched else None,
                         measured=area/900 if matched else None, completed=old["start"],
                         reason="matched_quarter" if matched else "DHW_or_invalid_power_interval")
        if not old or old["start"] != q0:
            prediction = self.state["predictions"].get(str(q0), {})
            pred = prediction.get("kw") if prediction.get("at", now) < q0 else None
            h["interval"] = dict(start=q0, area=d["heat_power"]*(now-q0) if d["cop_ok"] and now-q0 <= 10 else 0.,
                                 valid=d["cop_ok"] and now-q0 <= 10 and now > self.started,
                                 predicted=pred)
        h.update(observed_at=now, power=d["heat_power"], ok=d["cop_ok"])
        self.state["predictions"] = {k: v for k, v in self.state["predictions"].items() if float(k) >= q0}

    def set_forecast(self, forecast):
        self.state["forecast"] = forecast
        if forecast.get("complete"):
            for q in forecast["quarters"]:
                if q["start"] <= forecast["generated_at"]:
                    continue
                # Freeze the latest independent prediction made before its interval.
                self.state["predictions"][str(q["start"])] = dict(kw=q["heat_demand_kw"], at=forecast["generated_at"])

    def price_request(self, now):
        return min(int(self.config["parameters"]["maximum_reduction"]), price_level(self.state["forecast"].get("premium"))) if self.state["enabled"] and self.state["price_enabled"] and valid_forecast(self.state["forecast"], now) else 0

    def price_state(self, now):
        s, f = self.state, self.state["forecast"]
        if not s["enabled"]:
            return "advice_disabled"
        if not s["price_enabled"]:
            return "price_disabled"
        if valid_forecast(f, now):
            return f"price_level_{self.price_request(now)}"
        return "forecast_stale" if f.get("valid_until", 0) and f["valid_until"] <= now else "forecast_unavailable" if f.get("problems") else "forecast_missing"

    def estimated_capacity_reached(self, now):
        forecast = self.state["forecast"]
        if not valid_forecast(forecast, now):
            return None
        return forecast["quarters"][0]["heat_demand_kw"] >= self.config["parameters"]["capacity_kw"]

    def decide(self, now, *, immediate=False):
        # A normal sample/forecast update cannot move the held command output.
        quarter = int(now//900)
        if not immediate:
            if now-quarter*900 > 10 or self.last_decision_quarter == quarter:
                return False
            self.last_decision_quarter = quarter
        if not self.data:
            return False
        s, d, p, f = self.state, self.data, self.config["parameters"], self.state["forecast"]
        capacity = self.estimated_capacity_reached(now)
        forecast_ok = capacity is not None
        if self.hydraulics_ready or not s["enabled"] or not (s["price_enabled"] or s["efficiency_enabled"]):
            s["hold_until"] = 0.
        elif (s["enabled"] and (not s["price_enabled"] or forecast_ok)
              and (s["price_enabled"] or s["efficiency_enabled"]) and d["space_ok"] and d["rooms_ok"]
              and not self.comfort and not capacity and s["reduction"] > 0 and not s["hold_until"]):
            s["hold_until"] = now+1800
        self._efficiency(now, forecast_ok, capacity)
        requested = max(self.price_request(now), s["efficiency"] if s["efficiency_enabled"] else 0)
        wanted = requested
        reason = f"efficiency_level_{s['efficiency']}" if s["efficiency"] > self.price_request(now) and s["efficiency_enabled"] else f"price_level_{self.price_request(now)}"
        if not s["enabled"]:
            wanted, reason = 0, "advice_disabled"
        elif not s["price_enabled"] and not s["efficiency_enabled"]:
            wanted, reason = 0, "reduction_methods_disabled"
        elif s["price_enabled"] and not forecast_ok:
            wanted, reason = 0, "forecast_unavailable: "+(f.get("problems") or [self.price_state(now)])[0]
        elif not d["space_ok"]:
            wanted, reason = 0, "DHW_mode" if d["dhw_mode"] else "invalid_water_measurements"
        elif not d["rooms_ok"]:
            wanted, reason = 0, "room_data_missing: "+", ".join(d["room_missing"])
        elif self.comfort:
            wanted, reason = 0, "persistent_room_shortfall"
        elif capacity:
            wanted, reason = 0, "little_estimated_capacity_margin"
        elif not self.hydraulics_ready:
            if s["reduction"] > 0 and s["hold_until"] > now:
                wanted, reason = s["reduction"], "hydraulic_history_hold"
            else:
                wanted, reason = 0, "hydraulic_hold_expired" if s["hold_until"] else "hydraulic_history_warming_up"
        elif self.demand_rising:
            wanted, reason = min(requested, max(0, s["reduction"]-1)), "radiator_demand_rising"
        if d["curve_outdoor"] is None:
            s["reason"] = "outdoor_temperature_invalid"
            # Master-off must still clear the advisory command without a valid curve.
            if not s["enabled"]:
                s["reduction"] = s["effective_reduction"] = 0
            return True
        old = s["reduction"]
        new = 0 if not s["enabled"] else old+(1 if wanted > old else -1 if wanted < old else 0) if now-s["changed"] >= MIN_STEP_SECONDS else old
        if new != old:
            s["changed"] = now
        s["reduction"] = int(new)
        base = curve(d["curve_outdoor"], p, integer=True)
        target = max(p["target_floor"], base-new)
        effective = min(int(p["maximum_reduction"]), floor(max(0., base-target)+.5), max(0, floor(base-p["target_floor"])))
        # The external command is an integer shift; show its achievable target.
        target = base-effective if s["enabled"] else base
        s.update(baseline=base, target=target, effective_reduction=effective if s["enabled"] else 0,
                 reason=reason+("; waiting for 25-minute minimum interval" if new != wanted else ""))
        return True

    def _efficiency(self, now, forecast_ok, capacity):
        s, d, cfg, obs = self.state, self.data, self.state["settings"], self.observations
        current, wanted, reason = s["efficiency"], s["efficiency"], "efficiency_hold"
        if not (s["enabled"] and s["efficiency_enabled"]):
            wanted, reason = 0, "efficiency_disabled"
        elif self.comfort:
            wanted, reason = 0, "efficiency_comfort_recovery"
        elif capacity:
            wanted, reason = 0, "efficiency_capacity_recovery"
        elif not (0 <= cfg["gap_low"] < cfg["gap_high"] and 0 <= cfg["room_margin"] < cfg["room_recovery"]):
            reason = "efficiency_settings_invalid"
        elif not d["space_ok"] or not d["rooms_ok"] or d["local_outdoor"] is None or d["curve_outdoor"] is None:
            reason = "efficiency_inputs_unavailable"
        elif not d["learning_ok"]:
            reason = "efficiency_heating_paused"
        elif not self.hydraulics_ready or not self.observations_ready:
            reason = "efficiency_observations_warming_up"
        elif s["price_enabled"] and not forecast_ok:
            reason = "efficiency_waiting_for_forecast"
        elif d["limited_max_deficit"] >= cfg["room_recovery"]-.000001 or self.demand_rising:
            wanted, reason = max(0, current-1), "efficiency_demand_recovery"
        elif d["return_gap"] <= cfg["gap_low"]:
            wanted, reason = max(0, current-1), "efficiency_low_gap_recovery"
        elif d["limited_max_deficit"] > cfg["room_margin"]+.000001 or obs["deficit"]["change"] > .1+.000001:
            reason = "efficiency_room_margin_insufficient"
        elif obs["eligible_gap"]["minimum"] < cfg["gap_high"] or obs["eligible_flow"]["change"] > 0:
            reason = "efficiency_margin_inconclusive"
        elif current >= self.config["parameters"]["maximum_reduction"]:
            reason = "efficiency_maximum_reduction"
        elif max(self.config["parameters"]["target_floor"], curve(d["curve_outdoor"], self.config["parameters"], integer=True)-current-1) >= max(self.config["parameters"]["target_floor"], curve(d["curve_outdoor"], self.config["parameters"], integer=True)-current)-.01:
            reason = "efficiency_temperature_floor"
        else:
            wanted, reason = current+1, "efficiency_margin_available"
        enabled = s["enabled"] and s["efficiency_enabled"]
        new = 0 if not enabled else current+(1 if wanted > current else -1 if wanted < current else 0) if now-max(s["changed"], s["efficiency_changed"]) >= MIN_STEP_SECONDS else current
        if new != current:
            s["efficiency_changed"] = now
        s.update(efficiency=int(new), efficiency_reason=reason)

