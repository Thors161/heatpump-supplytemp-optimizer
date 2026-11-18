"""Common identity and device association for integration-owned entities."""
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from .const import DOMAIN, VERSION


class OptimizerEntity(CoordinatorEntity):
    _attr_has_entity_name = True

    def __init__(self, coordinator, key, name):
        super().__init__(coordinator)
        self.key = key
        self._attr_name = name
        self._attr_unique_id = f"{coordinator.entry.entry_id}_{key}"
        self._attr_suggested_object_id = f"{DOMAIN}_{key}"
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, coordinator.entry.entry_id)},
            name=coordinator.config["name"], model="Supply Temperature Optimizer", sw_version=VERSION)

