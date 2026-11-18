"""Diagnostic states are derived from current observations and bounded timers."""
from homeassistant.components.binary_sensor import BinarySensorEntity
from .entity import OptimizerEntity
from .forecast import valid_forecast

DEFINITIONS = {"comfort_shortfall": "Comfort protection", "hydraulics_ready": "Hydraulic history ready",
    "observations_ready": "Efficiency observations ready", "demand_rising": "Radiator demand rising",
    "eligible_heating": "Eligible heating", "forecast_ready": "Forecast ready"}


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([OptimizerBinary(entry.runtime_data, key, name) for key, name in DEFINITIONS.items()])


class OptimizerBinary(OptimizerEntity, BinarySensorEntity):
    @property
    def is_on(self):
        e = self.coordinator.engine
        return {"comfort_shortfall": e.comfort, "hydraulics_ready": e.hydraulics_ready,
            "observations_ready": e.observations_ready, "demand_rising": e.demand_rising,
            "eligible_heating": e.data.get("learning_ok", False),
            "forecast_ready": valid_forecast(e.state["forecast"], self.coordinator.data["now"])}[self.key]

