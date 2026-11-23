"""Diagnostic states are derived from current observations and bounded timers."""
from homeassistant.components.binary_sensor import BinarySensorEntity
from .entity import OptimizerEntity
from .forecast import valid_forecast

DEFINITIONS = {"comfort_shortfall": "Comfort protection", "hydraulics_ready": "Hydraulic history ready",
    "observations_ready": "Efficiency observations ready",
    "eligible_heating": "Eligible heating", "forecast_ready": "Forecast ready",
    "estimated_capacity_reached": "Estimated capacity reached",
    "defrost_protection": "Frequent defrost protection"}


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([OptimizerBinary(entry.runtime_data, key, name) for key, name in DEFINITIONS.items()])


class OptimizerBinary(OptimizerEntity, BinarySensorEntity):
    @property
    def is_on(self):
        e = self.coordinator.engine
        now = self.coordinator.data["now"]
        if self.key == "estimated_capacity_reached":
            return e.estimated_capacity_reached(now)
        if self.key == "defrost_protection":
            return e.defrost_protection(now)
        return {"comfort_shortfall": e.comfort_protection, "hydraulics_ready": e.hydraulics_ready,
            "observations_ready": e.observations_ready,
            "eligible_heating": e.data.get("learning_ok", False),
            "forecast_ready": valid_forecast(e.state["forecast"], now)}[self.key]

    @property
    def extra_state_attributes(self):
        e = self.coordinator.engine
        if self.key == "comfort_shortfall":
            status = "Disabled" if not e.state["comfort_enabled"] else "Active" if self.is_on else "Monitoring"
            return {"status": status}
        if self.key == "estimated_capacity_reached":
            return {"demand_kw": e.current_heat_demand(self.coordinator.data["now"]),
                    "capacity_kw": e.config["parameters"]["capacity_kw"]}
        if self.key == "defrost_protection":
            starts = e.state["defrost_starts"]
            enabled = e.state["defrost_enabled"] and e.state["settings"]["minimum_defrost_interval"] > 0
            status = "Disabled" if not enabled else "Active" if self.is_on else "Monitoring"
            if enabled and not e.config["inputs"].get("defrost_state"):
                status = "No defrost sensor"
            return {"status": status, "interval_minutes": (starts[1]-starts[0])/60 if len(starts) == 2 else None,
                    "minimum_minutes": e.state["settings"]["minimum_defrost_interval"]}
        return None
