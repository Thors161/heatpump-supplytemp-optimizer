"""Diagnostic states are derived from current observations and bounded timers."""
from homeassistant.components.binary_sensor import BinarySensorEntity
from .entity import OptimizerEntity
from .forecast import valid_forecast

DEFINITIONS = {"comfort_shortfall": "Comfort protection", "hydraulics_ready": "Hydraulic history ready",
    "observations_ready": "Efficiency observations ready",
    "eligible_heating": "Eligible heating", "forecast_ready": "Forecast ready",
    "estimated_capacity_reached": "Estimated capacity reached"}


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([OptimizerBinary(entry.runtime_data, key, name) for key, name in DEFINITIONS.items()])


class OptimizerBinary(OptimizerEntity, BinarySensorEntity):
    @property
    def is_on(self):
        e = self.coordinator.engine
        now = self.coordinator.data["now"]
        if self.key == "estimated_capacity_reached":
            return e.estimated_capacity_reached(now)
        return {"comfort_shortfall": e.comfort, "hydraulics_ready": e.hydraulics_ready,
            "observations_ready": e.observations_ready,
            "eligible_heating": e.data.get("learning_ok", False),
            "forecast_ready": valid_forecast(e.state["forecast"], now)}[self.key]

