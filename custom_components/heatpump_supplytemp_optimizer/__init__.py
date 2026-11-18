"""Heat Pump Supply Temperature Optimizer custom integration."""
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from .const import PLATFORMS
from .coordinator import OptimizerCoordinator


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    coordinator = OptimizerCoordinator(hass, entry)
    await coordinator.async_initialize()
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    coordinator.async_start()


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    await entry.runtime_data.async_shutdown()

