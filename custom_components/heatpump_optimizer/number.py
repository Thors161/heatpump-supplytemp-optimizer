"""Persistent efficiency settings and one allowance for each configured room."""
from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.helpers.entity import EntityCategory
from .const import SETTINGS
from .entity import OptimizerEntity

NAMES = {"gap_high":"High return gap", "gap_low":"Low return gap", "room_margin":"Room margin for more trim", "room_recovery":"Room shortfall for recovery"}


async def async_setup_entry(hass, entry, async_add_entities):
    c = entry.runtime_data
    entities = [OptimizerNumber(c, key, NAMES[key], lo, hi, step, unit)
                for key, (default, lo, hi, step, unit) in SETTINGS.items()]
    entities += [OptimizerNumber(c, "room_allowance_"+r["id"], r["name"]+" allowance", 0, 2, .1, "°C", r["id"]) for r in c.config["rooms"]]
    async_add_entities(entities)


class OptimizerNumber(OptimizerEntity, NumberEntity):
    _attr_mode = NumberMode.BOX
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, c, key, name, lo, hi, step, unit, room_id=None):
        super().__init__(c, key, name)
        self.room_id = room_id
        self._attr_native_min_value, self._attr_native_max_value = lo, hi
        self._attr_native_step, self._attr_native_unit_of_measurement = step, unit

    @property
    def native_value(self):
        state = self.coordinator.engine.state
        return state["allowances"].get(self.room_id, 0.) if self.room_id else state["settings"][self.key]

    async def async_set_native_value(self, value):
        await self.coordinator.async_set_number(self.key, value, self.room_id)

