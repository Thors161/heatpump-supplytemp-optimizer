"""Advisory state machine. Never sends commands to heating equipment."""
from copy import deepcopy
from datetime import timezone
from math import ceil, floor
from .const import DEFROST_RECOVERY_SECONDS, MAX_SAMPLE_GAP, MIN_STEP_SECONDS, ROOM_SHORTFALL_RISE, SNAPSHOT_VERSION
from .forecast import valid_forecast
from .inputs import read_inputs
from .model import clamp, curve, heating_load, price_level
from .rolling import Rolling
from .savings import Savings, rates
from .schema import numeric


class Engine:
    def __init__(self, config, saved=None, now=0, *, tz=timezone.utc):
        self.config = config
        saved = saved if isinstance(saved, dict) and saved.get("version") == SNAPSHOT_VERSION else {}
        default_rise, rise_low, rise_high, _, _ = ROOM_SHORTFALL_RISE
        default_rises = {room["id"]: room.get("max_shortfall_rise", default_rise) for room in config["rooms"]}
        self.state = dict(version=SNAPSHOT_VERSION, enabled=False, price_enabled=False,
                          efficiency_enabled=True, efficiency_addition=0, comfort_enabled=True, defrost_enabled=True,
                          effective_reduction=0, target=None, baseline=None,
                          decision_price_reduction=None, combined_reduction=None, protected_reduction=None,
                          decision_at=None, next_step_at=None, evaluated_reduction=None,
                          changed=0., efficiency_changed=0., hold_until=0.,
                          reason="startup_waiting_for_decision", efficiency_reason="startup_waiting_for_observations",
                          pv_cache={}, settings=dict(config["settings"]), allowances={},
                          shortfall_rise_limits={}, limited_shortfalls={},
                          defrost_starts=[],
                          forecast={}, power_history={}, predictions={}, savings={},
                          default_settings=dict(config["settings"]),
                          default_allowances={r["id"]: r["allowance"] for r in config["rooms"]})
        for key in self.state:
            if key in saved:
                self.state[key] = deepcopy(saved[key])
        for flag in ["enabled", "price_enabled", "efficiency_enabled", "comfort_enabled", "defrost_enabled"]:
            self.state[flag] = self.state[flag] is True
        if "efficiency_addition" not in saved:
            # Earlier snapshots stored Efficiency's request including its Price share.
            legacy = numeric(saved.get("efficiency")) or 0
            self.state["efficiency_addition"] = legacy - (numeric(saved.get("decision_price_reduction")) or 0)
        for key in ["efficiency_addition", "effective_reduction"]:
            self.state[key] = int(clamp(numeric(self.state[key]) or 0, 0, config["parameters"]["maximum_reduction"]))
        for key in ["changed", "efficiency_changed", "hold_until"]:
            self.state[key] = numeric(self.state[key]) or 0.
        for key in ["pv_cache", "settings", "allowances", "shortfall_rise_limits", "limited_shortfalls",
                    "forecast", "power_history", "predictions", "savings"]:
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
        defrost_source = config["inputs"].get("defrost_state")
        starts = self.state["defrost_starts"]
        self.state["defrost_source"] = defrost_source
        self.state["defrost_starts"] = (
            sorted({value for t in starts if (value := numeric(t)) is not None and value <= now})[-2:]
            if defrost_source and defrost_source == saved.get("defrost_source") and isinstance(starts, list)
            else []
        )
        self._defrost_active = None
        self._defrost_ended_at = None
        self._shift_mismatch_since = None
        self._shortfall_sample_times = {}
        self.general = Rolling(saved.get("samples", []))
        self.efficiency_gap = Rolling()
        # An outage must not be interpolated as a measured heating interval.
        self.power = Rolling()
        self.savings = Savings(self.state["savings"], tz)
        self.started = now
        self.data = {}
        self.forecast_attempt = 0.
        self.last_sample_minute = None
        self._heating_pause_since = None
        self.space_since = self.learning_since = None
        self.comfort_since = self.comfort_clear_since = None
        self._comfort_threshold = self.state["settings"]["comfort_threshold"]
        self.comfort = False
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
        self._observe_defrost(d["defrost_state"], now)
        self._observe_shift(now)
        minute = int(now//60)
        self._observe_heating(now, minute != self.last_sample_minute)
        self.power.add(now, {"heat": d["heat_power"] if d["cop_ok"] else None,
                             "electric": d["electric_total"] if d["cop_ok"] else None})
        self._update_power_history(now)
        self._observe_savings(now)
        if minute != self.last_sample_minute:
            self.general.add(now, dict(gap=d["return_gap"] if d["space_ok"] else None,
                deficit=d["limited_max_deficit"] if d["rooms_ok"] and d["space_mode"] else None,
                other=d["other_power"]))
            self.last_sample_minute = minute
        self.observations = {k: self.general.changes(k, now) for k in ["gap", "deficit"]}
        self.observations["eligible_gap"] = self.efficiency_gap.changes(
            "gap", now, defrost_pause=d["defrost_pause"] and self.observations_ready)
        self.hydraulics_ready = (self.space_since is not None and now-self.space_since >= 1800
                                and self.observations["gap"]["ready"])
        self.observations_ready = (self.learning_since is not None and now-self.learning_since >= 300
                                  and all(self.observations[k]["ready"] for k in ["eligible_gap", "deficit"]))
        threshold = self.state["settings"]["comfort_threshold"]
        if threshold != self._comfort_threshold:
            self.comfort_since = self.comfort_clear_since = None
            self.comfort = False
            self._comfort_threshold = threshold
        shortfall = not d["rooms_ok"] or d["limited_max_deficit"] >= threshold
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

    def _observe_heating(self, now, new_minute):
        d = self.data
        recovering = (self._defrost_ended_at is not None
                      and now-self._defrost_ended_at < DEFROST_RECOVERY_SECONDS)
        d["defrost_pause"] = d["space_mode"] and (
            self._defrost_active is True or (not d["learning_ok"] and recovering))
        d["learning_ok"] = d["learning_ok"] and not d["defrost_pause"]

        # Wait for a separately reported defrost flag before discarding a heating window.
        stopped = (self._defrost_active is False and d["space_ok"] and d["rooms_ok"]
                   and d["active_rooms"] > 0 and d["compressor_frequency"] == 0)
        if stopped and self._heating_pause_since is None:
            self._heating_pause_since = now
        pending_stop = self._heating_pause_since is not None
        brief_stop = pending_stop and now-self._heating_pause_since < DEFROST_RECOVERY_SECONDS
        ordinary_stop = pending_stop and (not brief_stop or (not stopped and not (recovering or d["defrost_pause"])))
        if not stopped:
            self._heating_pause_since = None
        if d["learning_ok"]:
            self._defrost_ended_at = None

        paused = d["defrost_pause"] or (stopped and brief_stop)
        if ordinary_stop or (not paused and not d["learning_ok"]):
            self.learning_since = None
        if d["learning_ok"] and self.learning_since is None:
            self.learning_since = now

        eligible = d["learning_ok"] and d["curve_outdoor"] is not None
        if ordinary_stop or (not eligible and not paused):
            self.efficiency_gap.samples.clear()
        elif new_minute:
            if eligible and self.shift_confirmation(now) in ("unconfigured", "confirmed"):
                self.efficiency_gap.add(now, {"gap": d["return_gap"]})
            elif d["defrost_pause"] and self.efficiency_gap.samples:
                self.efficiency_gap.add(now, {"defrost": True})
        if not d["space_ok"]:
            self.space_since = None
        elif self.space_since is None:
            self.space_since = now

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

    def _observe_defrost(self, state, now):
        state = state.strip().lower() if isinstance(state, str) else ""
        if state in {"on", "active"}:
            starts = self.state["defrost_starts"]
            if self._defrost_active is False and (not starts or now > starts[-1]):
                self.state["defrost_starts"] = [*starts[-1:], now]
            self._defrost_active = True
            self._defrost_ended_at = None
        elif state in {"off", "inactive"}:
            if self._defrost_active is True:
                self._defrost_ended_at = now
            self._defrost_active = False
        else:
            self._defrost_active = None
            self._defrost_ended_at = None

    @property
    def comfort_protection(self):
        return self.state["comfort_enabled"] and self.comfort

    def defrost_protection(self, now):
        starts = self.state["defrost_starts"]
        threshold = self.state["settings"]["minimum_defrost_interval"] * 60
        return (self.state["defrost_enabled"] and len(starts) == 2 and starts[1] - starts[0] < threshold
                and 0 <= now - starts[1] < threshold)

    def _observe_savings(self, now):
        sample = rates(self.data, self.config, self.state, self._defrost_active)
        self.savings.observe(now, sample)

    def _observe_shift(self, now):
        if self.shift_confirmation(now) in ("waiting", "not_applied"):
            if self._shift_mismatch_since is None:
                self._shift_mismatch_since = now
        else:
            self._shift_mismatch_since = None

    def shift_confirmation(self, now):
        if "reported_shift" not in self.config["inputs"]:
            return "unconfigured"
        if not self.state["enabled"] or not self.data.get("space_mode"):
            return "inactive"
        reported = self.data.get("reported_shift")
        if reported is None:
            return "unavailable"
        if abs(reported + self.state["effective_reduction"]) < .01:
            return "confirmed"
        grace = self.state["settings"]["shift_confirmation_grace"] * 60
        if self._shift_mismatch_since is not None and now - self._shift_mismatch_since >= grace:
            return "not_applied"
        return "waiting"

    def _update_power_history(self, now):
        h, d = self.state["power_history"], self.data
        q0 = int(now//900)*900
        old = h.get("interval")
        previous_time = h.get("observed_at", now)
        continuous = h.get("ok", False) and d["cop_ok"] and 0 <= now-previous_time <= MAX_SAMPLE_GAP
        if old:
            dt = min(now, old["start"]+900) - previous_time
            valid = old["valid"] and continuous
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
            elapsed = now - q0
            valid = (d["cop_ok"] and elapsed <= 10 and now > self.started
                     and (elapsed == 0 or (continuous and previous_time <= q0)))
            area = h["power"] * elapsed if valid and elapsed else 0.
            h["interval"] = dict(start=q0, area=area, valid=valid, predicted=pred)
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
        s = self.state
        if not s["enabled"] or not s["price_enabled"] or not valid_forecast(s["forecast"], now):
            return 0
        return min(int(self.config["parameters"]["maximum_reduction"]), price_level(s["forecast"]["premium"]))

    @property
    def efficiency_addition(self):
        s = self.state
        if not (s["enabled"] and s["efficiency_enabled"]):
            return 0
        return s["efficiency_addition"]

    def price_state(self, now):
        s, f = self.state, self.state["forecast"]
        if not s["enabled"]:
            return "advice_disabled"
        if not s["price_enabled"]:
            return "price_disabled"
        if valid_forecast(f, now):
            return f"price_level_{self.price_request(now)}"
        if f.get("complete") or (f.get("valid_until") and f["valid_until"] <= now):
            return "forecast_stale"
        return "forecast_unavailable" if f.get("problems") else "forecast_missing"

    def current_heat_demand(self, now):
        d, inputs, model = self.data, self.config["inputs"], self.config["model"]
        if not model["confirmed"] or not d.get("rooms_ok") or d["indoor"] is None:
            return None
        if d["local_outdoor"] is None or d["pv_power"] is None or (d["pv_hold_until"] and now >= d["pv_hold_until"]):
            return None
        if "stove_temperature" in inputs and d["stove_temperature"] is None:
            return None
        if "ventilation_power" in inputs and (d["ventilation_power"] is None or d["ventilation_power"] < 0):
            return None
        _, need = heating_load(d, d["local_outdoor"], d["pv_power"], model)
        return need

    def estimated_capacity_reached(self, now):
        need = self.current_heat_demand(now)
        return None if need is None else need >= self.config["parameters"]["capacity_kw"]

    def decide(self, now):
        s, d, p = self.state, self.data, self.config["parameters"]
        if not d:
            return False
        base = curve(d["curve_outdoor"], p, integer=True) if d["curve_outdoor"] is not None else None
        maximum = int(p["maximum_reduction"])
        if base is not None:
            maximum = min(maximum, max(0, floor(base-p["target_floor"])))

        price = self.price_request(now)
        forecast_ok = valid_forecast(s["forecast"], now)
        capacity = self.estimated_capacity_reached(now)
        defrost = self.defrost_protection(now)
        s["decision_at"] = now
        s["evaluated_reduction"] = s["effective_reduction"]
        efficiency_waiting = self._efficiency(now, price, forecast_ok, capacity, defrost, maximum)
        s["decision_price_reduction"] = price
        s["combined_reduction"] = price + self.efficiency_addition
        wanted, reason = self._protect(now, forecast_ok, capacity, defrost)
        self._apply_shift(now, wanted, reason, base, maximum)
        self._observe_savings(now)
        eligible_at = None
        if s["effective_reduction"] != s["protected_reduction"]:
            eligible_at = s["changed"] + MIN_STEP_SECONDS
        elif efficiency_waiting:
            eligible_at = max(s["changed"], s["efficiency_changed"]) + MIN_STEP_SECONDS
        s["next_step_at"] = ceil(eligible_at / 900) * 900 if eligible_at is not None else None
        return True

    def _protect(self, now, forecast_ok, capacity, defrost):
        s, d = self.state, self.data
        methods_enabled = s["price_enabled"] or s["efficiency_enabled"]
        if self.hydraulics_ready or defrost or not s["enabled"] or not methods_enabled:
            s["hold_until"] = 0.
        if not s["enabled"]:
            return 0, "advice_disabled"
        if not methods_enabled:
            return 0, "reduction_methods_disabled"
        if s["price_enabled"] and not forecast_ok:
            problem = (s["forecast"].get("problems") or [self.price_state(now)])[0]
            return 0, "forecast_unavailable: " + problem
        if not d["space_ok"]:
            return 0, "DHW_mode" if d["dhw_mode"] else "invalid_water_measurements"
        if not d["rooms_ok"]:
            return 0, "room_data_missing: " + ", ".join(d["room_missing"])
        if self.comfort_protection:
            return 0, "persistent_room_shortfall"
        if capacity:
            return 0, "little_estimated_capacity_margin"
        if defrost:
            return 0, "defrost_interval_too_short"
        if not self.hydraulics_ready:
            if s["effective_reduction"] and not s["hold_until"]:
                s["hold_until"] = now + 1800
            if s["hold_until"] > now:
                return s["effective_reduction"], "hydraulic_history_hold"
            return 0, "hydraulic_hold_expired" if s["hold_until"] else "hydraulic_history_warming_up"
        if d["curve_outdoor"] is None:
            return s["effective_reduction"], "outdoor_temperature_invalid"
        requested = s["combined_reduction"]
        return requested, f"efficiency_level_{requested}" if self.efficiency_addition else f"price_level_{requested}"

    def _apply_shift(self, now, wanted, reason, base, maximum):
        s = self.state
        old = s["effective_reduction"]
        if wanted > maximum:
            wanted, reason = maximum, "target_floor_limits_reduction"
        final = min(old, maximum)
        if not s["enabled"]:
            final = 0
        elif final == old and now-s["changed"] >= MIN_STEP_SECONDS:
            final += clamp(wanted-old, -1, 1)
        if final != wanted and old <= maximum:
            reason += "; waiting for 25-minute minimum interval" if final == old else "; moving one degree per decision"
        s.update(baseline=base, target=base-final if base is not None else None,
                 effective_reduction=final, protected_reduction=wanted, reason=reason)
        if final != old:
            s["changed"] = now
            self._shift_mismatch_since = None
            self._observe_shift(now)

    def _efficiency(self, now, price, forecast_ok, capacity, defrost, maximum):
        s, d, cfg, obs = self.state, self.data, self.state["settings"], self.observations
        enabled = s["enabled"] and s["efficiency_enabled"]
        previous_price = s["decision_price_reduction"] or 0
        # A higher Price absorbs existing trim degree for degree.
        price_rise = max(0, price - previous_price)
        current = max(0, s["efficiency_addition"] - price_rise)
        wanted, reason = current, "efficiency_hold"
        applied = s["effective_reduction"]
        price_handoff = price < previous_price and price + current < applied <= maximum
        recovery = max(0, current - 1)
        confirmation = self.shift_confirmation(now)
        if not enabled:
            wanted, reason = 0, "efficiency_disabled"
        elif self.comfort_protection:
            wanted, reason = recovery, "efficiency_comfort_recovery"
        elif capacity:
            wanted, reason = recovery, "efficiency_capacity_recovery"
        elif defrost:
            reason = "efficiency_defrost_hold"
        elif d["defrost_pause"]:
            reason = "efficiency_defrost_pause"
        elif not (0 <= cfg["gap_low"] < cfg["gap_high"] and 0 <= cfg["room_margin"] < cfg["room_recovery"]):
            reason = "efficiency_settings_invalid"
        elif not d["space_ok"] or not d["rooms_ok"] or d["local_outdoor"] is None or d["curve_outdoor"] is None:
            reason = "efficiency_inputs_unavailable"
        elif not d["learning_ok"]:
            reason = "efficiency_heating_paused"
        elif not self.hydraulics_ready:
            reason = "efficiency_observations_warming_up"
        elif s["price_enabled"] and not forecast_ok:
            reason = "efficiency_waiting_for_forecast"
        elif confirmation in ("waiting", "not_applied", "unavailable"):
            reason = {
                "waiting": "efficiency_waiting_for_shift",
                "not_applied": "efficiency_shift_not_applied",
                "unavailable": "efficiency_shift_readback_unavailable",
            }[confirmation]
        elif d["limited_max_deficit"] >= cfg["room_recovery"]-.000001:
            wanted = recovery
            reason = "efficiency_room_shortfall_recovery" if current else "efficiency_room_shortfall_no_addition"
        elif d["return_gap"] <= cfg["gap_low"]:
            wanted, reason = recovery, "efficiency_low_gap_recovery" if current else "efficiency_low_gap_no_addition"
        elif price + current > maximum:
            reason = "efficiency_temperature_floor"
        elif price + current != applied and not price_handoff:
            reason = "efficiency_waiting_for_reduction"
        elif not self.observations_ready:
            reason = "efficiency_observations_warming_up"
        elif d["limited_max_deficit"] > cfg["room_margin"]+.000001 or obs["deficit"]["change"] > .1+.000001:
            reason = "efficiency_room_margin_insufficient"
        elif obs["eligible_gap"]["minimum"] < cfg["gap_high"]:
            reason = "efficiency_margin_inconclusive"
        elif price_handoff:
            # Ownership changes at the existing shift; no physical step or timer reset.
            s.update(efficiency_addition=applied-price, efficiency_reason="efficiency_holds_released_price")
            return False
        elif applied >= self.config["parameters"]["maximum_reduction"]:
            reason = "efficiency_maximum_reduction"
        elif applied >= maximum:
            reason = "efficiency_temperature_floor"
        else:
            wanted, reason = current+1, "efficiency_margin_available"
        new = wanted if not enabled or now-max(s["changed"], s["efficiency_changed"]) >= MIN_STEP_SECONDS else current
        if new != current:
            s["efficiency_changed"] = now
        elif wanted != current:
            reason += "; waiting for 25-minute minimum interval"
        s.update(efficiency_addition=new, efficiency_reason=reason)
        return wanted != new

