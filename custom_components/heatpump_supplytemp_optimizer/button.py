"""Read-only refresh and dashboard export actions."""
import json
from pathlib import Path
from homeassistant.components.button import ButtonEntity
from homeassistant.helpers import entity_registry as er
from .const import DOMAIN, SETTINGS, SWITCHES
from .dashboard import build_dashboard
from .entity import OptimizerEntity
from .sensor import SENSORS
from .binary_sensor import DEFINITIONS


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([OptimizerButton(entry.runtime_data,"refresh_forecast","Refresh forecast"),
                        OptimizerButton(entry.runtime_data,"export_dashboard","Export dashboard")])


class OptimizerButton(OptimizerEntity, ButtonEntity):
    async def async_press(self):
        if self.key=="refresh_forecast":
            await self.coordinator.async_refresh_forecast()
            return
        c = self.coordinator
        registry = er.async_get(self.hass)
        identifiers = {}
        rooms = c.config["rooms"]
        platform_keys={"sensor":list(SENSORS)+[prefix+r["id"] for r in rooms
                                              for prefix in ("room_shortfall_", "room_limited_shortfall_")],
                       "binary_sensor":list(DEFINITIONS),"switch":list(SWITCHES),
                       "number":list(SETTINGS)+[prefix+r["id"] for r in rooms
                                              for prefix in ("room_allowance_", "room_max_shortfall_rise_")],
                       "button":["refresh_forecast","export_dashboard"]}
        for platform,keys in platform_keys.items():
            for key in keys:
                entity_id=registry.async_get_entity_id(platform,DOMAIN,f"{c.entry.entry_id}_{key}")
                if entity_id is None:
                    raise ValueError("An integration entity has not registered: "+key)
                identifiers[key]=entity_id
        config=build_dashboard(identifiers, rooms, defrost_entity=c.config["inputs"].get("defrost_state"))
        # JSON is valid YAML, including HA's raw dashboard YAML editor.
        path=Path(self.hass.config.path(f"{DOMAIN}_dashboard_{c.entry.entry_id}.yaml"))
        await self.hass.async_add_executor_job(path.write_text,json.dumps(config,indent=2,ensure_ascii=False)+"\n")

    @property
    def extra_state_attributes(self):
        if self.key=="export_dashboard":
            return {"output_file":f"{DOMAIN}_dashboard_{self.coordinator.entry.entry_id}.yaml",
                    "instructions":"Copy this file into a dedicated dashboard's raw configuration editor. Re-export after changing rooms or entity IDs."}
        return None

