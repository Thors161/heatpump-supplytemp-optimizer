"""HA state subscriptions, read-only forecasts and atomic local persistence."""
import asyncio
import logging
from homeassistant.core import callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.event import async_track_state_change_event, async_track_time_change
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util
from .const import DOMAIN, ROOM_SHORTFALL_RISE, STORAGE_VERSION, SETTINGS
from .engine import Engine
from .forecast import predict, valid_forecast
from .schema import validate_config

_LOGGER = logging.getLogger(__name__)


class OptimizerCoordinator(DataUpdateCoordinator):
    def __init__(self, hass, entry):
        config = validate_config(entry.options.get("configuration", entry.data))
        super().__init__(hass, _LOGGER, name=DOMAIN, config_entry=entry)
        self.entry = entry
        self.config = config
        self.store = Store(hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}")
        self.engine = None
        self._unsub = []
        self._forecast_lock = asyncio.Lock()
        self._decision_lock = asyncio.Lock()
        self._stopping = False
        self._last_boundary = None

    async def async_initialize(self):
        saved = await self.store.async_load()
        now = dt_util.utcnow().timestamp()
        self.engine = Engine(self.config, saved, now)
        self.engine.sample(self.snapshot(), now)
        self.publish()

    @callback
    def snapshot(self):
        result = {}
        for entity in self.snapshot_entity_ids():
            state = self.hass.states.get(entity)
            if state is not None:
                attrs = dict(state.attributes)
                if entity.startswith("climate."):
                    attrs.setdefault("temperature_unit", self.hass.config.units.temperature_unit)
                result[entity] = dict(state=state.state, attributes=attrs)
        return result

    @callback
    def publish(self):
        self.async_set_updated_data({"engine": self.engine, "now": dt_util.utcnow().timestamp()})

    @callback
    def async_start(self):
        self._unsub.append(async_track_state_change_event(self.hass, list(self.snapshot_entity_ids()), self._on_state))
        self._unsub.append(async_track_time_change(self.hass, self._tick, second=20))
        self._unsub.append(async_track_time_change(self.hass, self._boundary, minute=[0, 15, 30, 45], second=0))
        self.entry.async_create_background_task(self.hass, self.async_refresh_forecast(), "optimizer startup forecast")

    def snapshot_entity_ids(self):
        result = set(self.config["inputs"].values()) | set(self.config["pv_forecast_entities"]) | {"sun.sun"}
        for room in self.config["rooms"]:
            result.update(v for k, v in room.items() if k in ["climate", "actual", "setpoint"] and v)
        return result

    @callback
    def _on_state(self, event):
        if self._stopping:
            return
        now = dt_util.utcnow().timestamp()
        self.engine.sample(self.snapshot(), now)
        self.publish()
        self.store.async_delay_save(self.engine.serialize, 10)
        if not valid_forecast(self.engine.state["forecast"], now):
            self.entry.async_create_background_task(self.hass, self.async_refresh_forecast(), "optimizer input recovery")

    async def _tick(self, now):
        if self._stopping:
            return
        timestamp = now.timestamp()
        self.engine.sample(self.snapshot(), timestamp)
        f = self.engine.state["forecast"]
        wrong_trim = f.get("efficiency_reduction") != self.engine.efficiency_addition
        if not valid_forecast(f, timestamp) or wrong_trim:
            await self.async_refresh_forecast()
        self.publish()
        self.store.async_delay_save(self.engine.serialize, 10)

    async def _boundary(self, now):
        quarter = int(now.timestamp()//900)
        if self._stopping or quarter == self._last_boundary:
            return
        self._last_boundary = quarter
        async with self._decision_lock:
            await self.async_refresh_forecast(boundary=True)
            timestamp = dt_util.utcnow().timestamp()
            self.engine.sample(self.snapshot(), timestamp)
            self.engine.decide(timestamp)
            await self.store.async_save(self.engine.serialize())
            self.publish()

    async def async_refresh_forecast(self, *, boundary=False):
        if self._stopping or (self._forecast_lock.locked() and not boundary):
            return
        async with self._forecast_lock:
            now = dt_util.utcnow().timestamp()
            recent = now - self.engine.forecast_attempt < 60
            if self._stopping or (recent and (not boundary or valid_forecast(self.engine.state["forecast"], now))):
                return
            self.engine.forecast_attempt = now
            self.engine.sample(self.snapshot(), now)
            inputs, p = self.config["inputs"], self.config["parameters"]
            problems, weather, solar = [], [], []
            weather_entity = inputs.get("weather")
            async def weather_call():
                if not weather_entity:
                    return None
                return await self.hass.services.async_call("weather", "get_forecasts",
                    {"entity_id": weather_entity, "type": "hourly"}, blocking=True, return_response=True)
            async def solar_call(entry_id):
                return await self.hass.services.async_call("forecast_solar", "get_forecast",
                    {"config_entry": entry_id, "resolution": "hourly"}, blocking=True, return_response=True)
            calls = [("weather", weather_call)]
            if p["pv_forecast_format"] == "forecast_solar":
                entries = set(self.config["pv_forecast_entries"])
                registry = er.async_get(self.hass)
                for entity in self.config["pv_forecast_entities"]:
                    item = registry.async_get(entity)
                    if item and item.config_entry_id:
                        entries.add(item.config_entry_id)
                    else:
                        problems.append("pv_provider_not_found:"+entity)
                calls.extend(("pv:"+entry_id, lambda entry_id=entry_id: solar_call(entry_id)) for entry_id in entries)
            async def guarded(name, operation):
                try:
                    async with asyncio.timeout(10):
                        return name, await operation()
                except Exception as err:  # Provider failures must not kill retries.
                    _LOGGER.debug("Forecast provider failed (%s): %s", name, type(err).__name__)
                    return name, None
            for name, result in await asyncio.gather(*(guarded(name, op) for name, op in calls)):
                if result is None:
                    problems.append("forecast_provider_unavailable:"+name)
                elif name == "weather":
                    weather = result.get(weather_entity, {}).get("forecast", [])
                else:
                    solar.append(result)
            if p["pv_forecast_format"] == "generic":
                for entity in self.config["pv_forecast_entities"]:
                    state = self.hass.states.get(entity)
                    solar.append(state.attributes.get(p["pv_forecast_attribute"], []) if state else [])
            now = dt_util.utcnow().timestamp()
            self.engine.sample(self.snapshot(), now)
            price_entity = inputs.get("electricity_price")
            state = self.hass.states.get(price_entity) if price_entity else None
            prices = state.attributes.get(p["price_attribute"], []) if state else []
            other = self.engine.general.integral("other", now)
            forecast = predict(self.config, self.engine.data, prices, weather, solar, now,
                efficiency=self.engine.efficiency_addition,
                other_mean=other["mean"] if other["available"] else None, provider_problems=problems)
            self.engine.set_forecast(forecast)
            self.publish()
            self.store.async_delay_save(self.engine.serialize, 10)

    async def async_set_switch(self, key, enabled):
        if key == "price_enabled" and enabled and not self.config["model"]["confirmed"]:
            raise ValueError("Configure and confirm the heat-demand/COP model before enabling price reduction")
        async with self._decision_lock:
            self.engine.state[key] = bool(enabled)
            now = dt_util.utcnow().timestamp()
            self.engine.sample(self.snapshot(), now)
            self.engine.decide(now)
            await self.store.async_save(self.engine.serialize())
            self.publish()

    async def async_set_number(self, key, value, room_id=None, room_setting="allowances"):
        if room_id:
            lo, hi = ROOM_SHORTFALL_RISE[1:3] if room_setting == "shortfall_rise_limits" else (0, 2)
            if not lo <= value <= hi:
                raise ValueError("Room setting is outside its supported range")
            self.engine.state[room_setting][room_id] = value
        else:
            default, lo, hi, step, unit = SETTINGS[key]
            if not lo <= value <= hi:
                raise ValueError("Setting is outside its supported range")
            self.engine.state["settings"][key] = value
        self.engine.sample(self.snapshot(), dt_util.utcnow().timestamp())
        await self.store.async_save(self.engine.serialize())
        self.publish()

    async def async_shutdown(self):
        if self._stopping:
            return
        self._stopping = True
        for unsub in self._unsub:
            unsub()
        self._unsub.clear()
        if self.engine is not None:
            await self.store.async_save(self.engine.serialize())
        await super().async_shutdown()

