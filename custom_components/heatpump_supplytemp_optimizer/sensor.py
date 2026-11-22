"""Advice, observations and per-room diagnostics. No writable equipment entities."""
from homeassistant.components.sensor import SensorEntity, SensorStateClass, SensorDeviceClass
from .entity import OptimizerEntity
from .forecast import valid_forecast
from .model import curve
from .descriptions import describe

# key: (name, unit, device class). Unit conversions happen before these values.
SENSORS = {
    "baseline_temperature": ("Original target", "°C", "temperature"),
    "decision_baseline": ("Baseline at last decision", "°C", "temperature"),
    "target_temperature": ("Advised target", "°C", "temperature"),
    "efficiency_curve_temperature": ("Efficiency curve", "°C", "temperature"),
    "effective_reduction": ("Final reduction", "°C", None),
    "reduction": ("Reduction level", "°C", None),
    "efficiency_reduction": ("Efficiency request", "°C", None),
    "price_reduction": ("Price request", "°C", None),
    "decision_price_reduction": ("Price at last decision", "°C", None),
    "efficiency_addition": ("Efficiency addition", "°C", None),
    "combined_reduction": ("Combined request", "°C", None),
    "protected_reduction": ("Reduction after protections", "°C", None),
    "decision": ("Decision", None, None), "efficiency_reason": ("Efficiency state", None, None),
    "price_state": ("Price state", None, None), "forecast": ("Forecast", None, None),
    "max_room_deficit": ("Raw room shortfall", "°C", None),
    "adjusted_room_deficit": ("Adjusted room shortfall", "°C", None),
    "limited_room_deficit": ("Room shortfall used by optimizer", "°C", None),
    "return_gap": ("Return gap", "°C", None),
    "primary_return": ("Primary return", "°C", "temperature"),
    "secondary_return": ("Secondary return", "°C", "temperature"),
    "primary_flow": ("Primary flow", "L/min", None),
    "return_gap_change_30m": ("Return-gap change, 30 min", "°C", None),
    "efficiency_gap_min_30m": ("Eligible minimum gap, 30 min", "°C", None),
    "room_deficit_change_30m": ("Shortfall change, 30 min", "°C", None),
    "heat_power": ("Measured heat output", "kW", "power"),
    "electric_power": ("Heat pump electricity", "kW", "power"),
    "measured_cop_30m": ("Measured COP, 30 min", None, None),
    "current_heat_cost": ("Heat cost now", "EUR/kWh", None),
    "reference_heat_cost": ("Next 6h median", "EUR/kWh", None),
    "heat_cost_premium_6h": ("Heat-cost premium", "EUR/kWh", None),
    "original_curve_heat_cost": ("Original-curve heat cost", "EUR/kWh", None),
    "efficiency_heat_cost_difference": ("Estimated cost difference", "EUR/kWh", None),
    "predicted_cop": ("Efficiency-curve COP", None, None),
    "original_curve_cop": ("Original-curve COP", None, None),
    "pv_power": ("PV used by advisor", "kW", "power"),
    "other_load": ("Other household load", "kW", "power"),
    "import_price": ("Import price", "EUR/kWh", None),
    "export_price": ("Export value", "EUR/kWh", None),
    "local_outdoor": ("Local outside temperature", "°C", "temperature"),
    "curve_outdoor": ("Curve outside temperature", "°C", "temperature"),
    "actual_target": ("Actual heat pump target", "°C", "temperature"),
    "advised_shift": ("Advised shift", "°C", None),
    "reported_shift": ("Reported shift", "°C", None),
    "shift_confirmation": ("Shift confirmation", None, None),
    "compressor_frequency": ("Compressor frequency", "Hz", None),
    "heating_mode": ("Heating mode", None, None),
    "predicted_heating_power": ("Predicted space-heating power", "kW", "power"),
    "measured_heating_power": ("Measured matched space-heating power", "kW", "power"),
}
COP_KEYS = {"measured_cop_30m", "predicted_cop", "original_curve_cop"}
FORECAST_KEYS = {"current_heat_cost":"current_cost", "reference_heat_cost":"reference_cost",
    "heat_cost_premium_6h":"premium", "original_curve_heat_cost":"original_current_cost",
    "efficiency_heat_cost_difference":"efficiency_heat_cost_difference", "predicted_cop":"cop",
    "original_curve_cop":"original_cop"}


async def async_setup_entry(hass, entry, async_add_entities):
    c = entry.runtime_data
    entities = [OptimizerSensor(c, key, *definition) for key, definition in SENSORS.items()]
    entities += [RoomShortfallSensor(c, room) for room in c.config["rooms"]]
    entities += [RoomShortfallSensor(c, room, limited=True) for room in c.config["rooms"]]
    async_add_entities(entities)


class OptimizerSensor(OptimizerEntity, SensorEntity):
    def __init__(self, coordinator, key, name, unit, device_class):
        super().__init__(coordinator, key, name)
        self._attr_native_unit_of_measurement = unit
        if device_class:
            self._attr_device_class = SensorDeviceClass(device_class)
        if unit or key in COP_KEYS:
            self._attr_state_class = SensorStateClass.MEASUREMENT
            self._attr_suggested_display_precision = 2 if unit == "EUR/kWh" else 1
        if key == "heat_cost_premium_6h":
            self._attr_suggested_display_precision = 4

    @property
    def native_value(self):
        e, key = self.coordinator.engine, self.key
        s, d, p = e.state, e.data, e.config["parameters"]
        now = self.coordinator.data["now"]
        if key == "baseline_temperature":
            return curve(d["curve_outdoor"], p, integer=True) if d.get("curve_outdoor") is not None else None
        if key == "decision_baseline":
            return s["baseline"]
        if key == "efficiency_curve_temperature":
            return max(p["target_floor"], curve(d["curve_outdoor"], p, integer=True)-e.efficiency_addition) if d.get("curve_outdoor") is not None else None
        if key == "target_temperature":
            return s["target"] if d.get("curve_outdoor") is not None else None
        if key == "reduction":
            return s["effective_reduction"]
        if key in ["effective_reduction", "decision_price_reduction", "combined_reduction", "protected_reduction"]:
            return s[key]
        if key == "efficiency_addition":
            return e.efficiency_addition if s["decision_price_reduction"] is not None else None
        if key == "advised_shift":
            return -s["effective_reduction"]
        if key == "shift_confirmation":
            return e.shift_confirmation(now)
        if key == "efficiency_reduction":
            return s["combined_reduction"] if s["enabled"] and s["efficiency_enabled"] else 0
        if key == "price_reduction":
            return e.price_request(now)
        if key == "decision":
            return s["reason"][:255]
        if key == "efficiency_reason":
            return s["efficiency_reason"]
        if key == "price_state":
            return e.price_state(now)
        if key == "forecast":
            f = s["forecast"]
            return "stale" if f.get("complete") and not valid_forecast(f, now) else f.get("status", "waiting")
        if key in FORECAST_KEYS:
            return s["forecast"].get(FORECAST_KEYS[key]) if valid_forecast(s["forecast"], now) else None
        if key == "adjusted_room_deficit":
            return d.get("max_deficit") if d.get("rooms_ok") else None
        if key == "limited_room_deficit":
            return d.get("limited_max_deficit") if d.get("rooms_ok") else None
        if key == "max_room_deficit":
            return d.get("raw_max_deficit") if d.get("rooms_ok") else None
        observations = {"return_gap_change_30m": ("gap", "change"),
            "efficiency_gap_min_30m": ("eligible_gap", "minimum"),
            "room_deficit_change_30m": ("deficit", "change")}
        if key in observations:
            source, field = observations[key]
            return e.observations.get(source, {}).get(field)
        if key == "measured_cop_30m":
            return e.power.cop(now) if now-e.started >= 1800 and d.get("cop_ok") else None
        if key == "electric_power":
            return d.get("electric_total")
        if key == "other_load":
            return d.get("other_power")
        if key == "import_price":
            return d.get("electricity_price")
        if key == "export_price":
            price = d.get("electricity_price")
            return price-p["export_discount"] if price is not None else None
        if key in ["predicted_heating_power", "measured_heating_power"]:
            h = s["power_history"]
            return h.get("predicted" if key == "predicted_heating_power" else "measured") if h.get("matched") else None
        return d.get(key)

    @property
    def available(self):
        return super().available and self.native_value is not None

    @property
    def extra_state_attributes(self):
        e = self.coordinator.engine
        if self.key in ["decision", "efficiency_reason", "price_state"]:
            description = describe(self.native_value)
            if self.key == "decision":
                return {"description": description, "decision_at": e.state["decision_at"],
                        "next_step_at": e.state["next_step_at"]}
            if self.native_value == "efficiency_waiting_for_reduction" and e.state["evaluated_reduction"] is not None:
                description += (f" At evaluation the combined request was {e.state['combined_reduction']}°C"
                                f" and the final reduction in effect was {e.state['evaluated_reduction']}°C.")
            return {"description": description}
        if self.key == "limited_room_deficit":
            maximum = self.native_value
            rooms = e.data["rooms"]
            names = [room["name"] for room in e.config["rooms"]
                     if room["enabled"] and maximum and rooms[room["id"]]["limited"] is not None
                     and round(rooms[room["id"]]["limited"], 6) == maximum]
            return {"limiting_rooms": names}
        if self.key == "forecast":
            f = e.state["forecast"]
            return {"result": f, "generated_at": f.get("generated_at"), "valid_until": f.get("valid_until")}
        if self.key == "pv_power":
            return {"source": e.data.get("pv_source"), "age_seconds": e.data.get("pv_age_seconds")}
        if self.key == "effective_reduction":
            return {"decision_baseline": e.state["baseline"], "advised_target": e.state["target"],
                    "advisory_only": True}
        if self.key in ["predicted_heating_power", "measured_heating_power"]:
            return {"interval_start": e.state["power_history"].get("completed")}
        return None


class RoomShortfallSensor(OptimizerSensor):
    def __init__(self, coordinator, room, *, limited=False):
        self.room = room
        self.field = "limited" if limited else "adjusted"
        prefix = "room_limited_shortfall_" if limited else "room_shortfall_"
        label = " shortfall used by optimizer" if limited else " shortfall"
        super().__init__(coordinator, prefix+room["id"], room["name"]+label, "°C", None)

    @property
    def native_value(self):
        return self.coordinator.engine.data.get("rooms", {}).get(self.room["id"], {}).get(self.field)

    @property
    def extra_state_attributes(self):
        return self.coordinator.engine.data.get("rooms", {}).get(self.room["id"], {})

