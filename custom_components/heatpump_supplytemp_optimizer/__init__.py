"""Heat Pump Supply Temperature Optimizer custom integration."""
from copy import deepcopy

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from .const import CONFIG_MINOR_VERSION, PLATFORMS
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


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Remove the retired input mapping before loading an existing entry."""
    if entry.version == 1 and entry.minor_version < 2:
        data = deepcopy(dict(entry.data))
        options = deepcopy(dict(entry.options))
        for config in (data, options.get("configuration", {})):
            config.get("inputs", {}).pop("secondary_flow", None)
        hass.config_entries.async_update_entry(
            entry, data=data, options=options, minor_version=CONFIG_MINOR_VERSION
        )
    return True
