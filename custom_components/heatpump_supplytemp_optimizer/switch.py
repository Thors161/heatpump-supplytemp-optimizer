"""Integration-owned advice toggles. These never control the heat pump relay."""
from homeassistant.components.switch import SwitchEntity
from homeassistant.exceptions import HomeAssistantError
from .entity import OptimizerEntity


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([OptimizerSwitch(entry.runtime_data, key, name) for key, name in [
        ("enabled", "Advisor enabled"), ("price_enabled", "Price reduction enabled"),
        ("efficiency_enabled", "Efficiency trim enabled")]])


class OptimizerSwitch(OptimizerEntity, SwitchEntity):
    @property
    def is_on(self):
        return self.coordinator.engine.state[self.key]

    async def _set(self, value):
        try:
            await self.coordinator.async_set_switch(self.key, value)
        except ValueError as err:
            raise HomeAssistantError(str(err)) from err

    async def async_turn_on(self, **kwargs):
        await self._set(True)

    async def async_turn_off(self, **kwargs):
        await self._set(False)

